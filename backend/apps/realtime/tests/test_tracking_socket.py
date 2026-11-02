"""/ws/tracking/: batching, viewport filter and tenant isolation (spec A13, Phase 5 done criterion)."""
from __future__ import annotations

import asyncio

import pytest
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator

from apps.tracking.tests.test_positions import ALL_APPS
from config.asgi import application

pytestmark = pytest.mark.django_db(transaction=True, available_apps=[*ALL_APPS, "apps.realtime"])


@pytest.fixture(autouse=True)
def fast(settings):
    settings.WS_TRACKING_FLUSH = 0.2


def ticket(client) -> str:  # type: ignore[no-untyped-def]
    return client.post("/api/v1/auth/ws-ticket/").json()["ticket"]


async def open_socket(client, org_id: int | str | None) -> WebsocketCommunicator:  # type: ignore[no-untyped-def]
    t = await database_sync_to_async(ticket)(client)
    query = f"?ticket={t}" + (f"&org={org_id}" if org_id is not None else "")
    return WebsocketCommunicator(application, f"/ws/tracking/{query}",
                                 headers=[(b"origin", b"http://localhost"), (b"host", b"localhost")])


def update(device_id: int, lat: float, status: str = "ONLINE_MOVING") -> dict:  # type: ignore[type-arg]
    return {"device_id": device_id, "vehicle_id": None, "status": status, "latitude": lat, "longitude": 39.28,
            "speed_kmh": 40, "heading": 90, "satellites": 11, "ignition": True, "gps_valid": True}


def test_updates_are_batched_and_isolated(org_a, org_b, make_member, api):
    alice = api(make_member(org_a, "monitoring_officer"))
    bob = api(make_member(org_b, "monitoring_officer", "bob@b.test"))

    async def run() -> None:
        a_ws, b_ws = await open_socket(alice, org_a.pk), await open_socket(bob, org_b.pk)
        assert (await a_ws.connect())[0] and (await b_ws.connect())[0]
        assert (await a_ws.receive_json_from(timeout=3)) == {"type": "hello", "organization_id": org_a.pk}
        await b_ws.receive_json_from(timeout=3)
        layer = get_channel_layer()
        await layer.group_send(f"tracking.org.{org_a.pk}", {"type": "tracking.update", "updates": [update(1, -6.80)]})
        await layer.group_send(f"tracking.org.{org_a.pk}", {"type": "tracking.update", "updates": [update(1, -6.81),
                                                                                               update(2, -6.90)]})
        msg = await a_ws.receive_json_from(timeout=3)
        assert msg["type"] == "updates"
        assert {u["device_id"]: u["latitude"] for u in msg["updates"]} == {1: -6.81, 2: -6.90}   # newest wins
        assert await b_ws.receive_nothing(timeout=0.6)                    # org B never sees org A's vehicles
        await a_ws.disconnect()
        await b_ws.disconnect()
    asyncio.run(run())


def test_viewport_filter(org_a, make_member, api):
    alice = api(make_member(org_a, "monitoring_officer"))

    async def run() -> None:
        ws = await open_socket(alice, org_a.pk)
        await ws.connect()
        await ws.receive_json_from(timeout=3)
        await ws.send_json_to({"type": "viewport", "bounds": [-6.85, 39.2, -6.75, 39.35]})
        await asyncio.sleep(0.05)
        await get_channel_layer().group_send(f"tracking.org.{org_a.pk}", {"type": "tracking.update", "updates": [
            update(1, -6.80), update(2, -3.37)]})                            # Dar es Salaam in view, Arusha not
        msg = await ws.receive_json_from(timeout=3)
        assert [u["device_id"] for u in msg["updates"]] == [1]
        await ws.disconnect()
    asyncio.run(run())


def test_rejections(org_a, org_b, make_member, api):
    alice = api(make_member(org_a, "monitoring_officer"))
    auditor = api(make_member(org_a, "auditor", "aud@a.test"))           # no tracking.view_live
    multi_user = make_member(org_a, "viewer", "multi@x.test")
    make_member(org_b, "viewer", "multi@x.test")
    multi = api(multi_user)

    async def code(client, org) -> int | None:  # type: ignore[no-untyped-def]
        ws = await open_socket(client, org)
        connected, close_code = await ws.connect()
        await ws.disconnect()
        return None if connected else close_code

    async def run() -> None:
        assert await code(alice, org_b.pk) == 4403                       # someone else's organization
        assert await code(alice, "abc") == 4403
        assert await code(auditor, org_a.pk) == 4403                     # role without live tracking
        assert await code(multi, None) == 4400                           # must choose an organization
        assert await code(multi, org_b.pk) is None                       # ...and then it works
        assert await code(alice, None) is None                           # single membership: implied
    asyncio.run(run())
