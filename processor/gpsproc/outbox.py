"""Webhook events (spec A13): written to the web app's ``webhook_events`` outbox in the same transaction as the
result they report; the web app's Celery worker signs and delivers them. Skipped (cheaply) for organizations without
an active webhook for that event."""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

HOOKS_SQL = "SELECT coalesce(array_agg(DISTINCT e), '{}') FROM webhooks w, jsonb_array_elements_text(w.events) e " \
            "WHERE w.organization_id = $1 AND w.is_active"
INSERT_SQL = """INSERT INTO webhook_events (organization_id, event, dedup_key, payload, occurred_at, created_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, now()) ON CONFLICT (organization_id, dedup_key) DO NOTHING"""


def _default(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else str(v)


class Outbox:
    def __init__(self, ttl: float = 30.0) -> None:
        self.ttl = ttl
        self._subscribed: dict[int, tuple[float, set[str]]] = {}
        self.emitted = 0

    async def subscribed(self, conn: Any, organization_id: int) -> set[str]:
        hit = self._subscribed.get(organization_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        events = set(await conn.fetchval(HOOKS_SQL, organization_id) or [])
        self._subscribed[organization_id] = (time.monotonic() + self.ttl, events)
        return events

    async def emit(self, conn: Any, organization_id: int, event: str, dedup_key: str, payload: dict[str, Any],
                   occurred_at: datetime) -> bool:
        if event not in await self.subscribed(conn, organization_id):
            return False
        status = await conn.execute(INSERT_SQL, organization_id, event, dedup_key[:200],
                                    json.dumps(payload, default=_default), occurred_at)
        added = status.endswith(" 1")
        self.emitted += int(added)
        return added

    def invalidate(self, organization_id: int | None = None) -> None:
        if organization_id is None:
            self._subscribed.clear()
        else:
            self._subscribed.pop(organization_id, None)
