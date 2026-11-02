"""/ws/tracking/: live positions and status for one organization (spec A13).

The processor pushes ``tracking.update`` messages into the Channels group ``tracking.org.<id>``. Each socket keeps
only the latest update per tracker and flushes at most once per ``WS_TRACKING_FLUSH`` seconds, optionally
filtered to the map's viewport.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from apps.realtime.auth import session_still_active
from apps.realtime.consumers import CLOSE_FORBIDDEN, CLOSE_UNAUTHENTICATED, tenant_permissions

CLOSE_NEEDS_ORGANIZATION = 4400


@database_sync_to_async
def _resolve_org(user: Any, org_param: str) -> int | None:
    from apps.organizations.models import OrganizationMembership

    if org_param:
        try:
            org_id = int(org_param)
        except ValueError:
            return None
    else:
        ids = list(OrganizationMembership.unscoped.filter(user=user, is_active=True, organization__is_active=True)
                   .values_list("organization_id", flat=True)[:2])
        if len(ids) != 1:
            return -1
        org_id = ids[0]
    perms = tenant_permissions(user, org_id)
    return org_id if perms is not None and "tracking.view_live" in perms else None


def _in_bounds(update: dict[str, Any], bounds: list[float] | None) -> bool:
    if bounds is None:
        return True
    lat, lon = update.get("latitude"), update.get("longitude")
    if lat is None or lon is None:
        return False
    south, west, north, east = bounds
    return south <= lat <= north and west <= lon <= east


class TrackingConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        org_id = await _resolve_org(user, self.scope.get("org_param", ""))
        if org_id == -1:
            await self.close(code=CLOSE_NEEDS_ORGANIZATION)
            return
        if org_id is None:
            await self.close(code=CLOSE_FORBIDDEN)
            return
        self.organization_id = org_id
        self.group = f"tracking.org.{org_id}"
        self.pending: dict[int, dict[str, Any]] = {}
        self.bounds: list[float] | None = None
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        from common.metrics import ws_opened
        ws_opened("tracking")
        self._counted = True
        await self.send(text_data=json.dumps({"type": "hello", "organization_id": org_id}))
        self.flusher = asyncio.create_task(self._flush_loop())

    async def disconnect(self, code: int) -> None:
        if getattr(self, "_counted", False):
            from common.metrics import ws_closed
            ws_closed("tracking")
            self._counted = False
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)
        task = getattr(self, "flusher", None)
        if task:
            task.cancel()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        try:
            msg = json.loads(text_data or "{}")
        except ValueError:
            return
        if msg.get("type") == "viewport":
            b = msg.get("bounds")
            valid = isinstance(b, list) and len(b) == 4 and all(isinstance(x, int | float) for x in b)
            self.bounds = [float(x) for x in b] if valid else None

    async def tracking_update(self, event: dict[str, Any]) -> None:
        for update in event.get("updates", []):
            self.pending[int(update["device_id"])] = update   # newest wins within one flush window

    async def _flush_loop(self) -> None:
        interval = float(getattr(settings, "WS_TRACKING_FLUSH", 1.0))
        ticks = 0
        while True:
            await asyncio.sleep(interval)
            ticks += 1
            if ticks % max(1, int(30 / interval)) == 0 and not await session_still_active(
                    self.scope.get("session_id")):
                await self.close(code=CLOSE_UNAUTHENTICATED)
                return
            if not self.pending:
                continue
            batch = [u for u in self.pending.values() if _in_bounds(u, self.bounds)]
            self.pending = {}
            if batch:
                await self.send(text_data=json.dumps({"type": "updates", "updates": batch}))
