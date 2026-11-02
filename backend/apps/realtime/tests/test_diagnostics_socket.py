"""The /ws/devices/{id}/diagnostics/ socket: ticket auth, tenant checks, origin check, live updates."""
from __future__ import annotations

import asyncio

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.db import connection  # noqa: F401  (used by skipif)
from django.utils import timezone

from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device, DeviceConnection
from apps.tracking.tests.test_positions import ALL_APPS
from config.asgi import application

pytestmark = pytest.mark.django_db(transaction=True, available_apps=[*ALL_APPS, "apps.realtime"])


@pytest.fixture(autouse=True)
def fast(settings):
    settings.WS_DIAGNOSTICS_INTERVAL = 0.1
    settings.ALLOWED_HOSTS = ["testserver", "localhost"]


def ticket(client) -> str:  # type: ignore[no-untyped-def]
    return client.post("/api/v1/auth/ws-ticket/").json()["ticket"]


def socket(path: str, origin: bytes = b"http://localhost") -> WebsocketCommunicator:
    return WebsocketCommunicator(application, path, headers=[(b"origin", origin), (b"host", b"localhost")])


@pytest.fixture
def device(org_a):
    return Device.unscoped.create(organization=org_a, name="Wizard tracker", identifier="356307042441013",
                                  device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))


@pytest.mark.skipif("connection.vendor != 'postgresql'", reason="needs PostgreSQL views")
def test_live_updates_for_a_member(org_a, make_member, api, device):
    client = api(make_member(org_a, "org_admin"))

    async def run() -> None:
        ws = socket(f"/ws/devices/{device.pk}/diagnostics/?ticket={await database_sync_to_async(ticket)(client)}")
        connected, _ = await ws.connect()
        assert connected
        first = await ws.receive_json_from(timeout=3)
        assert first["type"] == "diagnostics" and first["data"]["current_stage"] == "connected"

        @database_sync_to_async
        def gateway_connects() -> None:
            DeviceConnection.unscoped.create(organization=org_a, device=device, transport="tcp", port=5027,
                                             peer="198.51.100.4:6000", connected_at=timezone.now())
        await gateway_connects()
        update = await ws.receive_json_from(timeout=3)
        assert update["data"]["connection"]["peer"] == "198.51.100.4:6000"
        assert update["data"]["current_stage"] == "first_packet"
        await ws.disconnect()
    asyncio.run(run())


def test_rejections(org_a, org_b, make_member, api, device):
    member = api(make_member(org_a, "org_admin"))
    outsider = api(make_member(org_b, "org_admin", "out@b.test"))

    async def closes(path: str, origin: bytes = b"http://localhost") -> bool:
        ws = socket(path, origin)
        connected, _ = await ws.connect()
        await ws.disconnect()
        return not connected

    async def run() -> None:
        path = f"/ws/devices/{device.pk}/diagnostics/"
        assert await closes(path)                                                    # no ticket
        assert await closes(path + "?ticket=forged")                                 # unknown ticket
        good = await database_sync_to_async(ticket)(member)
        assert await closes(path + f"?ticket={good}", b"https://evil.example")      # foreign origin (ticket untouched)
        ws = socket(path + f"?ticket={good}")
        assert (await ws.connect())[0]                                               # its owner can still use it once
        await ws.disconnect()
        assert await closes(path + f"?ticket={good}")                                # ...but never twice
        theirs = await database_sync_to_async(ticket)(outsider)
        assert await closes(path + f"?ticket={theirs}")                              # other organization
        missing = await database_sync_to_async(ticket)(member)
        assert await closes(f"/ws/devices/999999/diagnostics/?ticket={missing}")     # unknown tracker
    asyncio.run(run())
