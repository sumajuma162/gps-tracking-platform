"""Phase 5 acceptance: tracker packet -> gateway -> stream -> processor -> Channels -> /ws/tracking/ in < 2 s.

Everything is real except the browser: the gateway, the processor, Redis (stream and channel layer) and the
tracking consumer. Needs PostgreSQL + Redis and the gateway/processor packages on PYTHONPATH.
"""
from __future__ import annotations

import asyncio
import socket
import time
from datetime import UTC, datetime, timedelta

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.device_catalog.models import DeviceModel, ListenerPort
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import Vehicle
from apps.organizations.models import Organization
from apps.tracking.models import DeviceState
from apps.tracking.tests.test_positions import ALL_APPS

gpsgw = pytest.importorskip("gpsgw")
gpsproc = pytest.importorskip("gpsproc.app", reason="add ../processor to PYTHONPATH")
from gpsgw.app import Gateway  # noqa: E402
from gpsgw.config import GatewayConfig  # noqa: E402
from gpsgw.protocols.teltonika.encoder import AvlRecordSpec, encode_imei, encode_tcp_packet  # noqa: E402
from gpsproc.app import Processor, ProcessorConfig  # noqa: E402

pytestmark = [pytest.mark.skipif("connection.vendor != 'postgresql'"),
              pytest.mark.django_db(transaction=True, available_apps=[*ALL_APPS, "apps.realtime"])]
IMEI = "356307042441013"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def redis_layer(settings, isolated_redis_url):  # type: ignore[no-untyped-def]
    import redis
    try:
        redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1).ping()
    except Exception:
        pytest.skip("Redis is required")
    from channels.layers import channel_layers
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer",
                                           "CONFIG": {"hosts": [settings.REDIS_URL]}}}
    settings.WS_TRACKING_FLUSH = 0.25
    channel_layers.backends.clear()
    client = redis.Redis.from_url(settings.REDIS_URL)
    client.delete("positions")
    yield client
    channel_layers.backends.clear()


def test_tracker_to_browser_under_two_seconds(org_b, make_member, api, redis_layer):
    org = Organization.objects.create(name="Live Ltd", slug="live-ltd")
    vehicle = Vehicle.unscoped.create(organization=org, registration_number="T 555 LIV")
    device = Device.unscoped.create(organization=org, name="Live tracker", identifier=IMEI,
                                    device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    DeviceAssignment.unscoped.create(organization=org, device=device, vehicle=vehicle, assigned_at=timezone.now())
    officer = api(make_member(org, "monitoring_officer"))
    outsider = api(make_member(org_b, "monitoring_officer", "outsider@b.test"))
    tcp_port = free_port()
    ListenerPort.objects.filter(port=5027, transport="tcp").update(port=tcp_port)
    ListenerPort.objects.filter(port=5027, transport="udp").update(is_enabled=False)
    db = connection.settings_dict
    dsn = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST'] or 'localhost'}:{db['PORT'] or 5432}/{db['NAME']}"
    gw_cfg = GatewayConfig(teltonika_tcp_port=0, teltonika_udp_port=0, health_port=free_port(), storage="postgres",
                           allow_memory_storage=False, dev_devices=(), database_url=dsn, redis_url=settings.REDIS_URL,
                           listen_host="127.0.0.1")
    latencies: list[float] = []

    async def ticket(client) -> str:  # type: ignore[no-untyped-def]
        return (await database_sync_to_async(client.post)("/api/v1/auth/ws-ticket/")).json()["ticket"]

    async def socket_for(client, org_id: int) -> WebsocketCommunicator:  # type: ignore[no-untyped-def]
        ws = WebsocketCommunicator(  # type: ignore[no-untyped-call]
            __import__("config.asgi", fromlist=["application"]).application,
            f"/ws/tracking/?ticket={await ticket(client)}&org={org_id}",
            headers=[(b"origin", b"http://localhost"), (b"host", b"localhost")])
        assert (await ws.connect())[0]
        await ws.receive_json_from(timeout=3)          # hello
        return ws

    async def scenario() -> list[dict]:  # type: ignore[type-arg]
        gateway = Gateway(gw_cfg)
        processor = Processor(ProcessorConfig(database_url=dsn, redis_url=settings.REDIS_URL, consumer="test",
                                              sweep_seconds=3600))
        await gateway.start()
        await processor.start()
        ws = await socket_for(officer, org.pk)
        spy = await socket_for(outsider, org_b.pk)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", tcp_port)
            writer.write(encode_imei(IMEI))
            assert await reader.readexactly(1) == b"\x01"
            seen: list[dict] = []  # type: ignore[type-arg]
            for i in range(5):
                lat = -6.8160 + i * 0.0006
                rec = AvlRecordSpec(time=datetime.now(UTC) - timedelta(seconds=1), latitude=lat, longitude=39.2804,
                                    satellites=11, speed_kmh=38 + i, heading=30, io={239: (1, 1), 240: (1, 1)})
                sent = time.monotonic()
                writer.write(encode_tcp_packet([rec]))
                assert int.from_bytes(await reader.readexactly(4), "big") == 1
                while True:
                    msg = await ws.receive_json_from(timeout=3)
                    match = [u for u in msg.get("updates", []) if u["device_id"] == device.pk
                             and abs(u["latitude"] - lat) < 1e-6]
                    if match:
                        latencies.append(time.monotonic() - sent)
                        seen.append(match[0])
                        break
            writer.close()
            # The status sweep: silence -> OFFLINE, pushed to the map too.
            await database_sync_to_async(DeviceState.unscoped.filter(device=device).update)(
                last_packet_at=timezone.now() - timedelta(minutes=10))
            assert await processor.sweep() == 1
            offline = await ws.receive_json_from(timeout=3)
            seen.append(offline["updates"][0])
            assert await spy.receive_nothing(timeout=0.5)           # never delivered to another organization
            return seen
        finally:
            await ws.disconnect()
            await spy.disconnect()
            await processor.stop()
            await gateway.stop()

    seen = asyncio.run(scenario())
    assert len(latencies) == 5
    assert max(latencies) < 2.0, f"latencies {latencies}"
    assert seen[0]["status"] == "ONLINE_MOVING" and seen[0]["vehicle_id"] == vehicle.pk
    assert seen[-1]["status"] == "OFFLINE"
    assert DeviceState.unscoped.get(device=device).status == "OFFLINE"
    print(f"\npacket -> browser latency (s): {[round(x, 3) for x in latencies]}")
