"""Redis stream publishing and change events against a real Redis (skipped when none is reachable)."""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest

from gpsgw.normalization.normalized_location import NormalizedLocation
from gpsgw.services.ports import DeviceInfo
from gpsgw.services.redis_bus import EventSubscriber, NullPublisher, RedisStreamPublisher

URL = os.environ.get("GATEWAY_TEST_REDIS_URL", "redis://localhost:6379/14")


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    import redis.asyncio as aioredis
    c = aioredis.Redis.from_url(URL, socket_connect_timeout=1)
    try:
        await c.ping()
    except Exception:
        pytest.skip("no Redis available")
    await c.delete("positions")
    yield c
    await c.delete("positions")
    await c.aclose()


async def test_stream_and_events(client):  # type: ignore[no-untyped-def]
    device = DeviceInfo(5, "356307042441013", "teltonika", 2, None, None)
    now = datetime.now(UTC)
    rec = NormalizedLocation(device_identifier=device.identifier, protocol="teltonika", device_time=now,
                             server_time=now, latitude=-6.8, longitude=39.2, gps_valid=True, record_hash="h1")
    await RedisStreamPublisher(client).publish(device, [rec, rec])
    entries = await client.xrange("positions")
    assert len(entries) == 2 and entries[0][1][b"device_id"] == b"5" and entries[0][1][b"vehicle_id"] == b""
    await NullPublisher().publish(device, [rec])

    got: dict[str, list[object]] = {"device": [], "listeners": [], "command": []}

    async def on_device(p: dict) -> None:  # type: ignore[type-arg]
        got["device"].append(p)

    async def on_listeners() -> None:
        got["listeners"].append(1)

    async def on_command(p: dict) -> None:  # type: ignore[type-arg]
        got["command"].append(p)
    sub = EventSubscriber(client, on_device, on_listeners, on_command)
    task = asyncio.create_task(sub.run())
    try:
        await asyncio.wait_for(sub.ready.wait(), 3)
        await client.publish("device_registry", "not json")
        await client.publish("device_registry", '{"identifier": "1"}')
        await client.publish("listener_ports", '{"action": "reload"}')
        await client.publish("commands", '{"identifier": "1"}')
        for _ in range(30):
            if got["command"]:
                break
            await asyncio.sleep(0.1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert got == {"device": [{"identifier": "1"}], "listeners": [1], "command": [{"identifier": "1"}]}
