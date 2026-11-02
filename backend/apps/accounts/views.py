"""Authentication API (spec A8). Refresh tokens live only in an httpOnly cookie."""
from __future__ import annotations

import secrets
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import permissions, serializers, status
from rest_framework.authentication import CSRFCheck
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services
from apps.accounts.models import UserSession
from apps.accounts.serializers import (
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordForgotSerializer,
    PasswordResetSerializer,
    ProfileSerializer,
    SessionSerializer,
    memberships_payload,
)
from apps.audit.services import record
from common.exceptions import ApiError
from common.middleware import client_ip

_ERROR = OpenApiResponse(description="Error envelope: {error: {code, message, details?}, request_id}")
_SESSION = inline_serializer("SessionPayload", {
    "access": serializers.CharField(), "access_expires_in": serializers.IntegerField(),
    "user": ProfileSerializer(), "memberships": serializers.ListField(child=serializers.DictField())})
_ME = inline_serializer("MePayload", {"user": ProfileSerializer(),
                                      "memberships": serializers.ListField(child=serializers.DictField())})

LOGIN_FAILED_MESSAGE = ("Email or password is incorrect, or the account is locked for 15 minutes after repeated "
                        "failed attempts. Try again later or reset your password.")


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(settings.REFRESH_COOKIE_NAME, token, max_age=settings.JWT_REFRESH_DAYS * 86400,
                        httponly=True, secure=settings.REFRESH_COOKIE_SECURE, samesite="Strict",
                        path=settings.REFRESH_COOKIE_PATH)


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(settings.REFRESH_COOKIE_NAME, path=settings.REFRESH_COOKIE_PATH, samesite="Strict")


def _enforce_csrf(request: Request) -> None:
    check = CSRFCheck(lambda r: None)  # type: ignore[arg-type, return-value]
    check.process_request(request._request)
    reason = check.process_view(request._request, None, (), {})
    if reason:
        raise ApiError("csrf_failed", "Security check failed. Reload the page and try again.", 403)


def _password_errors(exc: DjangoValidationError) -> ApiError:
    return ApiError("password_invalid", "Choose a stronger password.", 400, {"new_password": list(exc.messages)})


def _session_payload(issued: services.IssuedTokens) -> dict[str, Any]:
    user = issued.session.user
    return {"access": issued.access, "access_expires_in": settings.JWT_ACCESS_MINUTES * 60,
            "user": ProfileSerializer(user).data, "memberships": memberships_payload(user)}


class AnonAuthView(APIView):
    authentication_classes: list[Any] = []
    permission_classes = [permissions.AllowAny]


@method_decorator(ensure_csrf_cookie, name="dispatch")
@extend_schema(request=LoginSerializer, responses={200: _SESSION, 401: _ERROR}, auth=[])
class LoginView(AnonAuthView):
    throttle_scope = "login"

    def post(self, request: Request) -> Response:
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            issued = services.login(data.validated_data["email"], data.validated_data["password"],
                                    client_ip(request), request.headers.get("User-Agent", ""))
        except services.AuthFailed:
            return Response({"error": {"code": "login_failed", "message": LOGIN_FAILED_MESSAGE},
                             "request_id": getattr(request._request, "request_id", None)},
                            status=status.HTTP_401_UNAUTHORIZED)
        response = Response(_session_payload(issued))
        _set_refresh_cookie(response, issued.refresh)
        return response


@method_decorator(ensure_csrf_cookie, name="dispatch")
@extend_schema(request=None, responses={200: _SESSION, 401: _ERROR}, auth=[],
               description="Uses the httpOnly refresh cookie and the X-CSRFToken header; rotates the cookie.")
class RefreshView(AnonAuthView):
    throttle_scope = "refresh"

    def post(self, request: Request) -> Response:
        _enforce_csrf(request)
        raw = request.COOKIES.get(settings.REFRESH_COOKIE_NAME, "")
        try:
            issued = services.refresh(raw, client_ip(request))
        except services.AuthFailed:
            response = Response({"error": {"code": "session_ended", "message": "Your session has ended. Sign in "
                                           "again."}}, status=status.HTTP_401_UNAUTHORIZED)
            _clear_refresh_cookie(response)
            return response
        response = Response(_session_payload(issued))
        _set_refresh_cookie(response, issued.refresh)
        return response


@extend_schema(request=None, responses={204: None}, auth=[])
class LogoutView(AnonAuthView):
    def post(self, request: Request) -> Response:
        _enforce_csrf(request)
        services.logout(None, request.COOKIES.get(settings.REFRESH_COOKIE_NAME), client_ip(request))
        response = Response(status=status.HTTP_204_NO_CONTENT)
        _clear_refresh_cookie(response)
        return response


class MeView(APIView):
    @extend_schema(responses=_ME)
    def get(self, request: Request) -> Response:
        return Response({"user": ProfileSerializer(request.user).data,
                         "memberships": memberships_payload(request.user)})

    @extend_schema(request=ProfileSerializer, responses=_ME)
    def patch(self, request: Request) -> Response:
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"user": serializer.data, "memberships": memberships_payload(request.user)})


@extend_schema(request=PasswordChangeSerializer, responses={204: None, 400: _ERROR})
class PasswordChangeView(APIView):
    throttle_scope = "password"

    def post(self, request: Request) -> Response:
        data = PasswordChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            services.change_password(request.user, data.validated_data["current_password"],
                                     data.validated_data["new_password"], getattr(request, "session_id", None),
                                     client_ip(request))
        except services.AuthFailed as exc:
            raise ApiError("current_password_wrong", "Your current password is not correct.", 400) from exc
        except DjangoValidationError as exc:
            raise _password_errors(exc) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(request=PasswordForgotSerializer, responses={202: OpenApiResponse(description="Always accepted")},
               auth=[])
class PasswordForgotView(AnonAuthView):
    throttle_scope = "password"

    def post(self, request: Request) -> Response:
        data = PasswordForgotSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.request_password_reset(data.validated_data["email"], client_ip(request))
        return Response({"message": "If an account exists for that email, we sent a link to reset the password."},
                        status=status.HTTP_202_ACCEPTED)


@extend_schema(request=PasswordResetSerializer, responses={204: None, 400: _ERROR}, auth=[])
class PasswordResetView(AnonAuthView):
    throttle_scope = "password"

    def post(self, request: Request) -> Response:
        data = PasswordResetSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            services.reset_password(data.validated_data["uid"], data.validated_data["token"],
                                    data.validated_data["new_password"], client_ip(request))
        except services.AuthFailed as exc:
            raise ApiError("reset_link_invalid", "This reset link is invalid or has expired. Request a new one.",
                           400) from exc
        except DjangoValidationError as exc:
            raise _password_errors(exc) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(responses=SessionSerializer(many=True))
class SessionListView(APIView):
    def get(self, request: Request) -> Response:
        rows = UserSession.objects.filter(user=request.user, revoked_at__isnull=True)
        ctx = {"current_session": getattr(request, "session_id", None)}
        return Response([s for s in SessionSerializer(rows, many=True, context=ctx).data])


@extend_schema(responses={204: None, 404: _ERROR})
class SessionRevokeView(APIView):
    def delete(self, request: Request, session_id: str) -> Response:
        session = UserSession.objects.filter(user=request.user, pk=session_id, revoked_at__isnull=True).first()
        if session is None:
            raise ApiError("not_found", "Session not found.", 404)
        session.revoke("user_revoked")
        record("auth.session_revoked", user=request.user, ip=client_ip(request), resource=session)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(request=None, responses=inline_serializer("RevokedCount", {"ended": serializers.IntegerField()}))
class SessionRevokeOthersView(APIView):
    def post(self, request: Request) -> Response:
        ended = services.revoke_sessions(request.user, "user_revoked_others", keep=getattr(request, "session_id",
                                                                                            None))
        record("auth.sessions_revoked", user=request.user, ip=client_ip(request), metadata={"count": ended})
        return Response({"ended": ended})


@extend_schema(request=None, responses=inline_serializer("WsTicket", {
    "ticket": serializers.CharField(), "expires_in": serializers.IntegerField()}))
class WebSocketTicketView(APIView):
    """One-time 30-second ticket for WebSocket auth, so JWTs never appear in URLs (spec A8)."""

    def post(self, request: Request) -> Response:
        ticket = secrets.token_urlsafe(32)
        cache.set(f"wsticket:{ticket}", {"user_id": request.user.pk,
                                         "session_id": getattr(request, "session_id", None)}, 30)
        return Response({"ticket": ticket, "expires_in": 30})


def consume_ws_ticket(ticket: str) -> dict[str, Any] | None:
    key = f"wsticket:{ticket}"
    data = cache.get(key)
    if data is not None:
        cache.delete(key)
    return data  # type: ignore[no-any-return]
