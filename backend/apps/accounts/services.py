"""Login, lockout, refresh rotation, logout and password flows (spec A8)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from apps.accounts import tokens
from apps.accounts.models import User, UserSession
from apps.audit.services import record, record_for_user

_DUMMY_HASH = make_password("timing-equalizer-not-a-real-password")


class AuthFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class IssuedTokens:
    access: str
    refresh: str
    session: UserSession


def _issue(user: User, ip: str | None, user_agent: str) -> IssuedTokens:
    jti = tokens.new_jti()
    session = UserSession.objects.create(
        user=user, refresh_jti=jti, ip_address=ip, user_agent=user_agent[:300],
        expires_at=timezone.now() + timedelta(days=settings.JWT_REFRESH_DAYS))
    sid = str(session.pk)
    return IssuedTokens(tokens.access_token(user.pk, sid), tokens.refresh_token(user.pk, sid, jti), session)


def login(email: str, password: str, ip: str | None, user_agent: str) -> IssuedTokens:
    now = timezone.now()
    user = User.objects.select_for_update().filter(email=email.strip().lower()).first()
    if user is None:
        check_password(password, _DUMMY_HASH)  # equalize timing for unknown accounts
        record("auth.login_failed", ip=ip, metadata={"reason": "unknown_account"})
        raise AuthFailed("login_failed")
    if user.is_locked(now):
        record_for_user("auth.login_failed", user, ip=ip, metadata={"reason": "locked"})
        raise AuthFailed("login_failed")
    if not user.check_password(password) or not user.is_active:
        window = timedelta(minutes=settings.LOCKOUT_WINDOW_MINUTES)
        if user.first_failed_login_at is None or now - user.first_failed_login_at > window:
            user.failed_login_count, user.first_failed_login_at = 0, now
        user.failed_login_count += 1
        reason = "inactive" if user.is_active is False else "bad_password"
        if user.failed_login_count >= settings.LOCKOUT_THRESHOLD:
            user.locked_until = now + timedelta(minutes=settings.LOCKOUT_MINUTES)
            record_for_user("auth.account_locked", user, ip=ip, metadata={"failures": user.failed_login_count})
        user.save(update_fields=["failed_login_count", "first_failed_login_at", "locked_until"])
        record_for_user("auth.login_failed", user, ip=ip, metadata={"reason": reason})
        raise AuthFailed("login_failed")
    user.failed_login_count, user.first_failed_login_at, user.locked_until = 0, None, None
    user.last_login = now
    user.save(update_fields=["failed_login_count", "first_failed_login_at", "locked_until", "last_login"])
    issued = _issue(user, ip, user_agent)
    record_for_user("auth.login", user, ip=ip, resource=issued.session, metadata={"user_agent": user_agent[:120]})
    return issued


def refresh(raw_refresh: str, ip: str | None) -> IssuedTokens:
    try:
        claims = tokens.decode(raw_refresh, "refresh")
    except tokens.TokenInvalid as exc:
        raise AuthFailed("refresh_invalid") from exc
    reused = False
    with transaction.atomic():
        session = UserSession.objects.select_for_update().select_related("user").filter(pk=claims.session_id).first()
        if session is None or not session.is_active or not session.user.is_active:
            raise AuthFailed("refresh_invalid")
        if session.refresh_jti != claims.jti:
            # A rotated-out token was presented again: treat the session as stolen. The revocation must
            # commit, so the error is raised only after this block exits normally.
            session.revoke("refresh_reuse")
            record_for_user("auth.refresh_reuse_detected", session.user, ip=ip, resource=session)
            reused = True
        else:
            session.refresh_jti = tokens.new_jti()
            session.last_used_at = timezone.now()
            session.save(update_fields=["refresh_jti", "last_used_at"])
    if reused:
        raise AuthFailed("refresh_invalid")
    sid = str(session.pk)
    return IssuedTokens(tokens.access_token(session.user_id, sid),
                        tokens.refresh_token(session.user_id, sid, session.refresh_jti), session)


def logout(session_id: str | None, raw_refresh: str | None, ip: str | None) -> None:
    sid = session_id
    if sid is None and raw_refresh:
        try:
            sid = tokens.decode(raw_refresh, "refresh").session_id
        except tokens.TokenInvalid:
            sid = None
    session = UserSession.objects.filter(pk=sid).select_related("user").first() if sid else None
    if session and session.revoked_at is None:
        session.revoke("logout")
        record_for_user("auth.logout", session.user, ip=ip, resource=session)


def revoke_sessions(user: User, reason: str, keep: str | None = None) -> int:
    qs = UserSession.objects.filter(user=user, revoked_at__isnull=True)
    if keep:
        qs = qs.exclude(pk=keep)
    return qs.update(revoked_at=timezone.now(), revoked_reason=reason)


def change_password(user: User, current: str, new: str, current_session: str | None, ip: str | None) -> None:
    if not user.check_password(current):
        raise AuthFailed("current_password_wrong")
    validate_password(new, user)
    user.set_password(new)
    user.password_changed_at = timezone.now()
    user.save(update_fields=["password", "password_changed_at"])
    ended = revoke_sessions(user, "password_changed", keep=current_session)
    record_for_user("auth.password_changed", user, ip=ip, metadata={"other_sessions_ended": ended})


def request_password_reset(email: str, ip: str | None) -> None:
    user = User.objects.filter(email=email.strip().lower(), is_active=True).first()
    if user is None:
        return
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
    send_mail("Reset your password",
              f"Someone asked to reset the password for {user.email}.\n\nSet a new password here (valid for "
              f"{settings.PASSWORD_RESET_TIMEOUT // 3600} hours):\n{link}\n\nIf this wasn't you, ignore this email.",
              settings.DEFAULT_FROM_EMAIL, [user.email])
    record_for_user("auth.password_reset_requested", user, ip=ip)


def reset_password(uid: str, token: str, new: str, ip: str | None) -> None:
    try:
        user = User.objects.get(pk=int(force_str(urlsafe_base64_decode(uid))), is_active=True)
    except (ValueError, TypeError, OverflowError, User.DoesNotExist) as exc:
        raise AuthFailed("reset_link_invalid") from exc
    if not default_token_generator.check_token(user, token):
        raise AuthFailed("reset_link_invalid")
    validate_password(new, user)
    user.set_password(new)
    user.password_changed_at = timezone.now()
    user.failed_login_count, user.first_failed_login_at, user.locked_until = 0, None, None
    user.save(update_fields=["password", "password_changed_at", "failed_login_count",
                             "first_failed_login_at", "locked_until"])
    revoke_sessions(user, "password_reset")
    record_for_user("auth.password_reset", user, ip=ip)
