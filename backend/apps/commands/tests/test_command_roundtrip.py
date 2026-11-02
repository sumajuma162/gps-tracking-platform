"""Phase 8 acceptance: a Codec 12 command round-trips with the simulator through the real gateway (spec Part B)."""
from __future__ import annotations

import asyncio
import socket
import time

import pytest
from django.conf import settings
from django.db import connection, connections

from apps.audit.models import AuditLog
from apps.commands.models import Command
from apps.device_catalog.models import CommandTemplate, DeviceModel, ListenerPort
from apps.devices.models import Device
from apps.organizations.models import Organization
from apps.tracking.tests.test_positions import ALL_APPS

pytest.importorskip("gpsgw")
pytest.importorskip("gpssim", reason="add ../simulator to PYTHONPATH")
from gpsgw.app import Gateway  # noqa: E402
from gpsgw.config import GatewayConfig  # noqa: E402
from gpsgw.protocols.teltonika.encoder import encode_imei  # noqa: E402
from gpssim.client import TcpClient  # noqa: E402

IMEI = "356307042441013"
pytestmark = [pytest.mark.skipif("connection.vendor != 'postgresql'"),
              pytest.mark.django_db(transaction=True, available_apps=[*ALL_APPS, "apps.alerts", "apps.commands"])]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def in_thread(fn):  # type: ignore[no-untyped-def]
    def run():  # type: ignore[no-untyped-def]
        try:
            return fn()
        finally:
            connections.close_all()
    return await asyncio.to_thread(run)


async def wait_status(pk: int, status: str, timeout: float = 8.0) -> Command:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        cmd = await in_thread(lambda: Command.unscoped.prefetch_related("responses").get(pk=pk))
        if cmd.status == status:
            return cmd
        await asyncio.sleep(0.2)
    raise AssertionError(f"command {pk} is {cmd.status}, expected {status}")


def test_command_round_trip_queue_and_timeout(isolated_redis_url, make_member, api):  # type: ignore[no-untyped-def]
    import redis
    try:
        redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1).ping()
    except Exception:
        pytest.skip("Redis is required")
    org = Organization.objects.create(name="Commands Ltd", slug="commands-ltd")
    device = Device.unscoped.create(organization=org, name="Commanded", identifier=IMEI,
                                    device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    getinfo = CommandTemplate.objects.get(device_model=device.device_model, command_text="getinfo")
    getver = CommandTemplate.objects.get(device_model=device.device_model, command_text="getver")
    admin = api(make_member(org, "org_admin"))
    port = free_port()
    ListenerPort.objects.filter(port=5027, transport="tcp").update(port=port)
    ListenerPort.objects.filter(port=5027, transport="udp").update(is_enabled=False)
    db = connection.settings_dict
    dsn = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST'] or 'localhost'}:{db['PORT'] or 5432}/{db['NAME']}"
    cfg = GatewayConfig(teltonika_tcp_port=0, teltonika_udp_port=0, health_port=free_port(), storage="postgres",
                        allow_memory_storage=False, dev_devices=(), database_url=dsn, redis_url=settings.REDIS_URL,
                        listen_host="127.0.0.1")

    def queue(template: CommandTemplate) -> int:
        resp = admin.post("/api/v1/commands/", {"device": device.pk, "template": template.pk}, format="json")
        assert resp.status_code == 201, resp.json()
        return int(resp.json()["id"])

    async def scenario() -> dict[str, float]:
        gw = Gateway(cfg)
        await gw.start()
        await asyncio.wait_for(gw.subscriber.ready.wait(), 5)
        timings = {}
        try:
            # 1. Queued while the tracker is offline: stays PENDING, then goes out when it connects.
            offline_id = await in_thread(lambda: queue(getver))
            await asyncio.sleep(0.5)
            assert (await in_thread(lambda: Command.unscoped.get(pk=offline_id))).status == "PENDING"
            sim = TcpClient("127.0.0.1", port, IMEI)
            await sim.connect()
            listener = asyncio.create_task(sim.idle(30))
            done = await wait_status(offline_id, "ACKNOWLEDGED")
            assert done.responses.first().response_text.startswith("Ver:03.28")

            # 2. Round trip for a connected tracker: API -> Redis event -> gateway -> simulator -> response.
            started = time.monotonic()
            live_id = await in_thread(lambda: queue(getinfo))
            done = await wait_status(live_id, "ACKNOWLEDGED")
            timings["round_trip_s"] = time.monotonic() - started
            assert done.sent_at and done.completed_at and done.responses.first().response_text.startswith("RTC:")
            assert sim.commands_received == ["getver", "getinfo"]
            listener.cancel()
            await sim.close()

            # 3. A tracker that never answers: SENT, then TIMEOUT.
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(encode_imei(IMEI))
            assert await reader.readexactly(1) == b"\x01"
            silent_id = await in_thread(lambda: queue(getinfo))
            await wait_status(silent_id, "SENT")
            gw.commands.response_timeout = __import__("datetime").timedelta(0)
            assert await gw.commands.expire() >= 1
            timed_out = await wait_status(silent_id, "TIMEOUT")
            assert "no response" in timed_out.error
            writer.close()
            return timings
        finally:
            await gw.stop()

    timings = asyncio.run(scenario())
    assert timings["round_trip_s"] < 5, timings
    assert AuditLog.objects.filter(action="commands.queued").count() == 3
    print(f"\ncommand round trip: {timings['round_trip_s']:.2f} s")
