"""Live connection-test feed for the Add Device wizard (spec A13: /ws/devices/{id}/diagnostics/)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from apps.realtime.auth import session_still_active

CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403


def tenant_permissions(user: Any, organization_id: int) -> frozenset[str] | None:
    """Permissions ``user`` has in the organization (membership or active break-glass), like the REST API."""
    from apps.organizations.models import BreakGlassGrant, OrganizationMembership
    from apps.rbac.catalog import BREAK_GLASS_PERMISSIONS
    from apps.rbac.services import role_permissions

    membership = (OrganizationMembership.unscoped.select_related("role")
                  .filter(user=user, organization_id=organization_id, is_active=True, organization__is_active=True,
                          organization__deleted_at__isnull=True).first())
    if membership:
        return role_permissions(membership.role)
    if user.is_platform_admin:
        grant = (BreakGlassGrant.objects.filter(user=user, organization_id=organization_id, revoked_at__isnull=True)
                 .order_by("-expires_at").first())
        if grant and grant.is_active:
            return frozenset(BREAK_GLASS_PERMISSIONS)
    return None


@database_sync_to_async
def _authorize(user: Any, device_id: int) -> int | None:
    from apps.devices.models import Device
    from common.rls import rls_bypass

    with transaction.atomic(), rls_bypass():
        org_id = Device.unscoped.filter(pk=device_id, deleted_at__isnull=True).values_list("organization_id",
                                                                                            flat=True).first()
    if org_id is None:
        return None
    perms = tenant_permissions(user, org_id)
    return org_id if perms is not None and "devices.view" in perms else None


@database_sync_to_async
def _snapshot(device_id: int, organization_id: int, include_raw: bool) -> dict[str, Any] | None:
    from apps.devices.diagnostics import diagnose
    from apps.devices.models import Device
    from common.rls import set_current_org
    from common.tenancy import tenant_context

    with transaction.atomic(), tenant_context(organization_id):
        set_current_org(organization_id)
        device = Device.objects.select_related("device_model__manufacturer").filter(
            pk=device_id, deleted_at__isnull=True).first()
        return diagnose(device, include_raw) if device else None


class DeviceDiagnosticsConsumer(AsyncJsonWebsocketConsumer):
    """Pushes the tracker's diagnostics whenever they change (checked every ``WS_DIAGNOSTICS_INTERVAL`` s)."""

    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        self.device_id = int(self.scope["url_route"]["kwargs"]["device_id"])
        self.organization_id = await _authorize(user, self.device_id)
        if self.organization_id is None:
            await self.close(code=CLOSE_FORBIDDEN)  # also for trackers of other organizations: no existence leak
            return
        await self.accept()
        from common.metrics import ws_opened
        ws_opened("diagnostics")
        self._counted = True
        self.task = asyncio.create_task(self._watch(bool(user.is_platform_admin)))

    async def disconnect(self, code: int) -> None:
        if getattr(self, "_counted", False):
            from common.metrics import ws_closed
            ws_closed("diagnostics")
            self._counted = False
        task = getattr(self, "task", None)
        if task:
            task.cancel()

    async def _watch(self, include_raw: bool) -> None:
        """Runs until the socket closes. A send racing a browser disconnect is the normal end, not an error."""
        from channels.exceptions import ChannelFull

        try:
            await self._watch_loop(include_raw)
        except (asyncio.CancelledError, ConnectionError, ChannelFull):
            return
        except Exception as exc:  # uvicorn's ClientDisconnected and websockets' ConnectionClosed vary by version
            if type(exc).__name__ in ("ClientDisconnected", "ConnectionClosed", "ConnectionClosedOK",
                                      "ConnectionClosedError"):
                return
            raise

    async def _watch_loop(self, include_raw: bool) -> None:
        last = ""
        ticks = 0
        interval = float(getattr(settings, "WS_DIAGNOSTICS_INTERVAL", 1.0))
        while True:
            if ticks % 30 == 0 and ticks and not await session_still_active(self.scope.get("session_id")):
                await self.close(code=CLOSE_UNAUTHENTICATED)
                return
            data = await _snapshot(self.device_id, self.organization_id, include_raw)
            if data is None:
                await self.close(code=CLOSE_FORBIDDEN)
                return
            fingerprint = json.dumps({k: v for k, v in data.items() if k != "checked_at"}, cls=DjangoJSONEncoder)
            if fingerprint != last:
                last = fingerprint
                await self.send(text_data=json.dumps({"type": "diagnostics", "data": data}, cls=DjangoJSONEncoder))
            ticks += 1
            await asyncio.sleep(interval)
