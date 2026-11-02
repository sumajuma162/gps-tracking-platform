"""Phase 3 acceptance: the real gateway against PostgreSQL + TimescaleDB + Redis (spec Part B, phase 3).

Teltonika's own documented packets are sent over real TCP and UDP sockets; the test then checks what landed in
the database. It also measures hot reload: registering, disabling and adding a listener port without restarts.
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from django.db import connection, connections
from django.utils import timezone

from apps.device_catalog.models import DeviceModel, DeviceProtocol, ListenerPort
from apps.devices.events import device_changed, listener_ports_changed
from apps.devices.models import Device, DeviceAssignment, DeviceConnection, UnregisteredDeviceAttempt
from apps.fleet.models import Vehicle
from apps.organizations.models import Organization
from apps.tracking.tests.test_positions import ALL_APPS

gpsgw = pytest.importorskip("gpsgw", reason="install the gateway package: pip install -e ../gateway")
from gpsgw.app import Gateway  # noqa: E402
from gpsgw.config import GatewayConfig  # noqa: E402
from gpsgw.protocols.teltonika.encoder import AvlRecordSpec, encode_imei, encode_tcp_packet  # noqa: E402

FX = pytest.importorskip("tests.fixtures.teltonika_official", reason="run from the repository checkout")

TCP_IMEI, UDP_IMEI, NEW_IMEI = "356307042441013", "352093086403655", "490154203237518"
pytestmark = [pytest.mark.skipif("connection.vendor != 'postgresql'"),
              pytest.mark.django_db(transaction=True, available_apps=ALL_APPS)]


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def redis_available() -> bool:
    try:
        import redis
        return bool(redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1).ping())
    except Exception:
        return False


async def orm(fn):  # type: ignore[no-untyped-def]
    """Run Django ORM code from async code (its own thread and connection, closed afterwards)."""
    def run():  # type: ignore[no-untyped-def]
        try:
            return fn()
        finally:
            connections.close_all()
    return await asyncio.to_thread(run)


async def tcp_session(port: int, imei: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(bytes.fromhex(FX.IMEI_HANDSHAKE) if imei == TCP_IMEI else encode_imei(imei))
    return r, w, await asyncio.wait_for(r.readexactly(1), 5)


async def send(r: asyncio.StreamReader, w: asyncio.StreamWriter, hex_or_bytes: str | bytes) -> int:
    w.write(bytes.fromhex(hex_or_bytes) if isinstance(hex_or_bytes, str) else hex_or_bytes)
    return int.from_bytes(await asyncio.wait_for(r.readexactly(4), 5), "big")


async def udp_exchange(port: int, datagram_hex: str) -> bytes:
    loop = asyncio.get_running_loop()
    got: asyncio.Queue[bytes] = asyncio.Queue()

    class Client(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: object) -> None:
            got.put_nowait(data)

    transport, _ = await loop.create_datagram_endpoint(Client, remote_addr=("127.0.0.1", port))
    transport.sendto(bytes.fromhex(datagram_hex))
    try:
        return await asyncio.wait_for(got.get(), 5)
    finally:
        transport.close()


async def wait_until(check, timeout: float) -> float:  # type: ignore[no-untyped-def]
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if await check():
            return time.monotonic() - start
        await asyncio.sleep(0.2)
    raise AssertionError(f"condition not met within {timeout} s")


def test_gateway_end_to_end(isolated_redis_url):
    if not redis_available():
        pytest.skip("Redis is required for hot-reload events and the positions stream")
    import redis
    rds = redis.Redis.from_url(settings.REDIS_URL)
    rds.delete("positions")
    with connection.cursor() as cur:  # keep background policies from compressing/dropping 2019 fixture chunks
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        if cur.fetchone():
            cur.execute("SELECT alter_job(job_id, scheduled => false) FROM timescaledb_information.jobs "
                        "WHERE hypertable_name IN ('positions', 'raw_packets')")

    org = Organization.objects.create(name="Gateway Test Ltd", slug="gateway-test")
    truck = Vehicle.unscoped.create(organization=org, registration_number="T 100 GW")
    trailer = Vehicle.unscoped.create(organization=org, registration_number="T 200 GW")
    fmc920 = DeviceModel.objects.get(slug="teltonika-fmc920")
    tcp_dev = Device.unscoped.create(organization=org, name="TCP tracker", identifier=TCP_IMEI, device_model=fmc920,
                                     log_raw_packets=True)
    udp_dev = Device.unscoped.create(organization=org, name="UDP tracker", identifier=UDP_IMEI, device_model=fmc920)
    DeviceAssignment.unscoped.create(organization=org, device=tcp_dev, vehicle=truck, assigned_at=timezone.now())
    tcp_port, udp_port, extra_port = free_port(), free_port(socket.SOCK_DGRAM), free_port()
    ListenerPort.objects.filter(port=5027, transport="tcp").update(port=tcp_port)
    ListenerPort.objects.filter(port=5027, transport="udp").update(port=udp_port)
    db = connection.settings_dict
    dsn = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST'] or 'localhost'}:{db['PORT'] or 5432}/{db['NAME']}"
    cfg = GatewayConfig(teltonika_tcp_port=0, teltonika_udp_port=0, health_port=free_port(), storage="postgres",
                        allow_memory_storage=False, dev_devices=(), database_url=dsn, redis_url=settings.REDIS_URL,
                        listen_host="127.0.0.1", reconcile_interval=1.0)
    timings: dict[str, float] = {}

    async def scenario() -> bytes:
        gw = Gateway(cfg)
        await gw.start()
        await asyncio.wait_for(gw.subscriber.ready.wait(), 5)
        try:
            # 1. Official TCP packets: handshake, Codec 8 x3, a resend, Codec 8 Extended.
            r, w, reply = await tcp_session(tcp_port, TCP_IMEI)
            assert reply == b"\x01"
            assert await send(r, w, FX.CODEC8_EX1) == 1
            assert await send(r, w, FX.CODEC8_EX2) == 1
            assert await send(r, w, FX.CODEC8_EX3) == 2          # older than EX2: a backlog upload
            assert await send(r, w, FX.CODEC8_EX1) == 1          # device did not see the ACK: resend
            assert await send(r, w, FX.CODEC8E_EX1) == 1
            w.close()
            # 2. Official UDP datagrams: the ACK must match Teltonika's documented response.
            assert (await udp_exchange(udp_port, FX.UDP_CODEC8)).hex().upper() == FX.UDP_CODEC8_ACK
            assert (await udp_exchange(udp_port, FX.UDP_CODEC8E)).hex().upper() == FX.UDP_CODEC8E_ACK
            # 3. Unknown IMEI refused and recorded.
            _, w2, reply = await tcp_session(tcp_port, NEW_IMEI)
            assert reply == b"\x00"
            w2.close()

            # 4. Register it "in the UI" while the gateway runs: accepted without a restart.
            def register() -> None:
                device_changed(Device.unscoped.create(organization=org, name="New tracker", identifier=NEW_IMEI,
                                                      device_model=fmc920), "created")
            await orm(register)

            async def accepted() -> bool:
                _, wx, rep = await tcp_session(tcp_port, NEW_IMEI)
                wx.close()
                return rep == b"\x01"
            timings["register"] = await wait_until(accepted, 10)

            # 5. Disable it while connected: the open connection is closed.
            r3, w3, reply = await tcp_session(tcp_port, NEW_IMEI)
            assert reply == b"\x01"

            def disable() -> None:
                dev = Device.unscoped.get(identifier=NEW_IMEI)
                dev.is_active = False
                dev.save(update_fields=["is_active"])
                device_changed(dev, "disabled")
            started = time.monotonic()
            await orm(disable)
            assert await asyncio.wait_for(r3.read(10), 5) == b""
            timings["disable"] = time.monotonic() - started

            # 6. Reassign the TCP tracker to another vehicle: the next record carries the new vehicle.
            def reassign() -> None:
                DeviceAssignment.unscoped.filter(device=tcp_dev, unassigned_at__isnull=True).update(
                    unassigned_at=timezone.now())
                DeviceAssignment.unscoped.create(organization=org, device=tcp_dev, vehicle=trailer,
                                                 assigned_at=timezone.now())
                device_changed(Device.unscoped.get(pk=tcp_dev.pk), "assigned")
            await orm(reassign)
            await asyncio.sleep(0.5)
            r4, w4, _ = await tcp_session(tcp_port, TCP_IMEI)
            fresh = AvlRecordSpec(time=datetime.now(UTC) - timedelta(minutes=1), latitude=-6.8161, longitude=39.2804,
                                  satellites=11, speed_kmh=42, io={239: (1, 1), 66: (2, 12650)})
            assert await send(r4, w4, encode_tcp_packet([fresh])) == 1
            w4.close()

            # 7. A listener port added "in the UI" starts listening within the reconcile window.
            def add_port() -> None:
                ListenerPort.objects.create(port=extra_port, transport="tcp",
                                            protocol=DeviceProtocol.objects.get(name="teltonika"))
                listener_ports_changed()
            await orm(add_port)

            async def listening() -> bool:
                try:
                    _, wx, rep = await tcp_session(extra_port, TCP_IMEI)
                    wx.close()
                    return rep == b"\x01"
                except OSError:
                    return False
            timings["listener"] = await wait_until(listening, 10)

            reader, writer = await asyncio.open_connection("127.0.0.1", cfg.health_port)
            writer.write(b"GET /metrics HTTP/1.1\r\n\r\n")
            metrics = await reader.read()
            writer.close()
            await asyncio.sleep(0.3)  # let connection-close bookkeeping finish
            return metrics
        finally:
            await gw.stop()

    metrics = asyncio.run(scenario())

    # --- What landed in the database --------------------------------------------------------------------
    with connection.cursor() as cur:
        cur.execute("""SELECT device_time, vehicle_id, organization_id, gps_valid, is_late, external_voltage,
                              gsm_signal, odometer_km, raw_metadata, protocol
                       FROM positions WHERE device_id = %s ORDER BY device_time""", [tcp_dev.pk])
        # Django's psycopg setup returns jsonb as text from raw cursors; decode it here.
        tcp_rows = [(*r[:8], json.loads(r[8]) if isinstance(r[8], str) else r[8], r[9]) for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT jsonb_typeof(raw_metadata) || '/' || jsonb_typeof(events) || '/' || "
                    "jsonb_typeof(sensors) FROM positions")
        json_types = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT jsonb_typeof(io) FROM device_state")
        state_io_types = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT count(*) FROM positions WHERE device_id = %s", [udp_dev.pk])
        udp_count = cur.fetchone()[0]
        cur.execute("SELECT direction, count(*) FROM raw_packets WHERE device_id = %s GROUP BY 1 ORDER BY 1",
                    [tcp_dev.pk])
        raw = dict(cur.fetchall())
        cur.execute("SELECT name, implemented FROM device_protocols ORDER BY name")
        protocols = dict(cur.fetchall())
        cur.execute("SELECT last_packet_at IS NOT NULL, gps_valid, latitude, vehicle_id FROM device_state "
                    "WHERE device_id = %s", [tcp_dev.pk])
        state = cur.fetchone()

    assert json_types == {"object/array/object"}, json_types   # stored as JSON objects, never as JSON strings
    assert state_io_types == {"object"}, state_io_types
    assert len(tcp_rows) == 6, "5 unique fixture records + 1 fresh record (the resend is not stored twice)"
    by_time = {row[0]: row for row in tcp_rows}
    ex1 = by_time[datetime(2019, 6, 10, 10, 4, 46, tzinfo=UTC)]
    assert ex1[1] == truck.pk and ex1[2] == org.pk and ex1[9] == "teltonika"
    assert ex1[3] is False                                       # 0 satellites: kept, never routed
    assert ex1[5] == pytest.approx(24.079) and ex1[6] == 3      # IO 66 = 0x5E0F mV, IO 21 = 3
    assert ex1[8]["io"]["241"] == 0x601A                         # unmapped IO kept in raw metadata
    late = [row for row in tcp_rows if row[4]]
    assert sorted(r[0].minute for r in late) == [1, 1]          # Codec 8 example 3 arrived after example 2
    ex8e = by_time[datetime(2019, 6, 10, 11, 36, 32, tzinfo=UTC)]
    assert ex8e[7] == pytest.approx(0x015E2C88 * 0.001)          # IO 16 total odometer, metres -> km
    newest = tcp_rows[-1]
    assert newest[1] == trailer.pk and newest[3] is True and newest[5] == pytest.approx(12.65)
    assert udp_count == 2
    assert state == (True, True, pytest.approx(-6.8161), trailer.pk)   # live state = newest valid fix
    assert raw.get("in", 0) >= 6 and raw.get("out", 0) >= 5         # handshake + packets in, ACKs out

    conn_rows = list(DeviceConnection.unscoped.filter(device=tcp_dev).order_by("connected_at"))
    assert conn_rows and all(c.disconnected_at for c in conn_rows)
    first = conn_rows[0]
    assert (first.packets, first.records, first.transport, first.port) == (5, 6, "tcp", tcp_port)
    tcp_dev.refresh_from_db()
    assert tcp_dev.last_packet_at and tcp_dev.last_connected_at
    attempt = UnregisteredDeviceAttempt.objects.get(identifier=NEW_IMEI)
    assert attempt.protocol == "teltonika" and attempt.attempts >= 1 and attempt.sample_hex.startswith("000f")

    assert protocols["teltonika"] is True and protocols["gt06"] is True and protocols["generic_json"] is True
    assert rds.xlen("positions") >= 8                               # every stored batch was published
    assert b'gpsgw_records_total{outcome="stored",protocol="teltonika"} 8.0' in metrics
    assert b'outcome="duplicate"' in metrics and b'outcome="late"' in metrics

    assert timings["register"] < 10 and timings["disable"] < 5 and timings["listener"] < 10, timings
    print(f"\nhot reload timings (s): {timings}")
