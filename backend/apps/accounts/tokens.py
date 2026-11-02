"""JWT access/refresh tokens (HS256). Access tokens live 10 minutes; refresh tokens rotate."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import jwt
from django.conf import settings
from django.utils import timezone

ALGORITHM = "HS256"


class TokenInvalid(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    user_id: int
    session_id: str
    jti: str
    token_type: str
    expires_at: datetime


def _key() -> str:
    return str(settings.JWT_SIGNING_KEY)


def new_jti() -> str:
    return secrets.token_urlsafe(24)


def encode(user_id: int, session_id: str, token_type: str, lifetime: timedelta, jti: str | None = None) -> str:
    now = timezone.now()
    payload = {"sub": str(user_id), "sid": session_id, "typ": token_type, "jti": jti or new_jti(),
               "iat": int(now.timestamp()), "exp": int((now + lifetime).timestamp()), "iss": settings.JWT_ISSUER}
    return jwt.encode(payload, _key(), algorithm=ALGORITHM)


def decode(token: str, expected_type: str) -> Claims:
    try:
        data: dict[str, Any] = jwt.decode(token, _key(), algorithms=[ALGORITHM], issuer=settings.JWT_ISSUER,
                                          options={"require": ["exp", "iat", "sub", "sid", "jti", "typ"]})
    except jwt.PyJWTError as exc:
        raise TokenInvalid(str(exc)) from exc
    if data["typ"] != expected_type:
        raise TokenInvalid("wrong token type")
    return Claims(int(data["sub"]), str(data["sid"]), str(data["jti"]), data["typ"],
                  datetime.fromtimestamp(data["exp"], tz=timezone.get_current_timezone()))


def access_token(user_id: int, session_id: str) -> str:
    return encode(user_id, session_id, "access", timedelta(minutes=settings.JWT_ACCESS_MINUTES))


def refresh_token(user_id: int, session_id: str, jti: str) -> str:
    return encode(user_id, session_id, "refresh", timedelta(days=settings.JWT_REFRESH_DAYS), jti=jti)
