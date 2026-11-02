"""DRF authentication: `Authorization: Bearer <access JWT>` bound to a live session."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication, exceptions
from rest_framework.request import Request

from apps.accounts import tokens
from apps.accounts.models import User, UserSession


class JWTAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request: Request) -> tuple[User, tokens.Claims] | None:
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid authorization header.")
        try:
            claims = tokens.decode(header[1].decode(), "access")
        except (tokens.TokenInvalid, UnicodeDecodeError) as exc:
            raise exceptions.AuthenticationFailed("Your session has expired. Sign in again.",
                                                  code="token_invalid") from exc
        session = UserSession.objects.select_related("user").filter(pk=claims.session_id).first()
        if session is None or not session.is_active or session.user_id != claims.user_id \
                or not session.user.is_active:
            raise exceptions.AuthenticationFailed("Your session has ended. Sign in again.", code="session_ended")
        now = timezone.now()
        if now - session.last_used_at > timedelta(minutes=1):
            UserSession.objects.filter(pk=session.pk).update(last_used_at=now)
        request.session_id = str(session.pk)  # type: ignore[attr-defined]
        return session.user, claims

    def authenticate_header(self, request: Request) -> str:
        return self.keyword

