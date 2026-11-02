"""WebSocket authentication with one-time tickets (spec A8: JWTs never appear in URLs).

The browser POSTs /api/v1/auth/ws-ticket/ with its access token, then connects to ``wss://.../ws/...?ticket=``.
A ticket works once, for 30 seconds, and only while the session it was issued for is still active.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware

from apps.accounts.models import UserSession


@database_sync_to_async
def _user_for_ticket(ticket: str) -> tuple[Any, str | None]:
    from apps.accounts.views import consume_ws_ticket

    data = consume_ws_ticket(ticket) if ticket else None
    if not data:
        return None, None
    session = UserSession.objects.select_related("user").filter(pk=data.get("session_id")).first()
    if session is None or not session.is_active or not session.user.is_active or session.user_id != data["user_id"]:
        return None, None
    return session.user, str(session.pk)


@database_sync_to_async
def session_still_active(session_id: str | None) -> bool:
    session = UserSession.objects.select_related("user").filter(pk=session_id).first() if session_id else None
    return bool(session and session.is_active and session.user.is_active)


class TicketAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> Any:
        query = parse_qs(scope.get("query_string", b"").decode())
        user, session_id = await _user_for_ticket((query.get("ticket") or [""])[0])
        scope["user"] = user
        scope["session_id"] = session_id
        scope["org_param"] = (query.get("org") or [""])[0]
        return await super().__call__(scope, receive, send)
