"""Scenario 4 — data integrity under failure (spec Part B, Phase 11), with the real gateway, PostgreSQL + TimescaleDB,
Redis, the processor and the simulator:

1. Database outage: the tracker gets no ACK, then resends; nothing is lost and nothing is duplicated.
2. Coverage gap: hours of buffered records arrive late; the live marker never moves backwards, late records are
   flagged, and recompute rebuilds trips for the gap.
3. Bad data: GPS jumps and invalid fixes are stored but excluded from routes and distance.
4. Malformed packets close only that connection; other trackers carry on.
5. Stream loss (Redis restart): records stored while the processor couldn't see them are analysed from the
   database, so no trip is skipped.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from django.db import connection

from apps.analysis.models import Trip
from apps.device_catalog.models import DeviceModel, ListenerPort
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import Vehicle
from apps.tracking.models import DeviceState
from apps.tracking.tests.test_gateway_ingest import free_port, redis_available
from apps.tracking.tests.test_positions import ALL_APPS

gpsgw = pytest.importorskip("gpsgw", reason="install the gateway package")
pytest.importorskip("gpssim.scenarios", reason="add ../simulator to PYTHONPATH")
pytest.importorskip("gpsproc.app", reason="add ../processor to PYTHONPATH")
from gpsgw.app import Gateway  # noqa: E402
from gpsgw.config import GatewayConfig  # noqa: E402
from gpsgw.protocols.teltonika.encoder import AvlRecordSpec, encode_imei, encode_tcp_packet  # noqa: E402
from gpsproc.app import Processor, ProcessorConfig  # noqa: E402
from gpssim.scenarios import run_device  # noqa: E402

pytestmark = [pytest.mark.skipif("connection.vendor != 'postgresql'"),
              pytest.mark.django_db(transaction=True, available_apps=[
                  *ALL_APPS, "apps.analysis", "apps.geofences", "apps.alerts", "apps.integrations",
                  "apps.settings_app"])]
IMEIS = {"outage": "356307042441013", "gap": "352093086403655", "jump": "490154203237518",
         "invalid": "359632107452945", "bad": "353201234567896", "steady": "861234567890126"}


def dsn() -> str:
    db = connection.settings_dict
    return f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST'] or 'localhost'}:{db['PORT'] or 5432}/{db['NAME']}"


def count(sql: str, *args: object) -> int:
    with connection.cursor() as cur:
        cur.execute(sql, args)
        return int(cur.fetchone()[0])


class Outage:
    """A database outage as the gateway sees it: its sessions are killed and new ones refused. Driven from a
    separate connection to the ``postgres`` database; the test's own session is spared."""

    def __init__(self) -> None:
        import psycopg

        db = connection.settings_dict
        self.name = db["NAME"]
        with connection.cursor() as cur:
            cur.execute("SELECT pg_backend_pid()")
            self.spare = cur.fetchone()[0]
        self.admin = psycopg.connect(host=db["HOST"] or "localhost", port=db["PORT"] or 5432, user=db["USER"],
                                     password=db["PASSWORD"], dbname="postgres", autocommit=True)

    def set(self, on: bool) -> None:
        self.admin.execute(f'ALTER DATABASE "{self.name}" ALLOW_CONNECTIONS {"false" if on else "true"}')
        if on:
            self.admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND "
                               "pid <> %s AND backend_type = 'client backend'", [self.name, self.spare])

    def close(self) -> None:
        self.set(False)
        self.admin.close()


async def processor_pass() -> Processor:
    """Consume everything on the positions stream once, then run due recomputes (as the service would)."""
    import asyncpg
    import redis.asyncio as aioredis

    proc = Processor(ProcessorConfig(database_url=dsn(), redis_url=settings.REDIS_URL, recompute_debounce_s=0))

    class NullLayer:
        async def group_send(self, group: str, message: dict) -> None:  # type: ignore[type-arg]
            return None
    proc.pool = await asyncpg.create_pool(dsn(), min_size=1, max_size=2)
    proc.layer = NullLayer()  # type: ignore[assignment]
    client = aioredis.Redis.from_url(settings.REDIS_URL)
    try:
        await proc.handle(await client.xrange("positions"))
        proc.stats.recomputes_run += await proc.analysis.run_due_recomputes(proc.pool, debounce_s=0)
    finally:
        await client.aclose()
        await proc.pool.close()
    return proc


@pytest.fixture
def fleet(isolated_redis_url):  # type: ignore[no-untyped-def]
    if not redis_available():
        pytest.skip("Redis is required")
    import redis
    redis.Redis.from_url(settings.REDIS_URL).delete("positions")
    from apps.organizations.models import Organization
    org = Organization.objects.create(name="Integrity Ltd", slug="integrity")
    model = DeviceModel.objects.get(slug="teltonika-fmc920")
    out = {}
    for n, (key, imei) in enumerate(IMEIS.items()):
        v = Vehicle.unscoped.create(organization=org, registration_number=f"T {100 + n} INT")
        d = Device.unscoped.create(organization=org, name=key, identifier=imei, device_model=model)
        DeviceAssignment.unscoped.create(organization=org, device=d, vehicle=v,
                                         assigned_at=datetime.now(UTC) - timedelta(days=2))
        out[key] = (d, v)
    port = free_port()
    ListenerPort.objects.filter(port=5027, transport="tcp").update(port=port)
    ListenerPort.objects.filter(port=5027, transport="udp").update(is_enabled=False)
    ListenerPort.objects.exclude(port=port).update(is_enabled=False)
    return org, out, port


def gateway_config() -> GatewayConfig:
    return GatewayConfig(teltonika_tcp_port=0, teltonika_udp_port=0, health_port=free_port(), storage="postgres",
                         allow_memory_storage=False, dev_devices=(), database_url=dsn(), redis_url=settings.REDIS_URL,
                         listen_host="127.0.0.1", reconcile_interval=60)


def test_database_outage_no_ack_then_resend_without_loss_or_duplicates(fleet):  # type: ignore[no-untyped-def]
    org, devices, port = fleet
    device = devices["outage"][0]
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
    first = encode_tcp_packet([AvlRecordSpec(time=t0 + timedelta(seconds=10 * i), latitude=-6.8, longitude=39.2 + i
                                             * 0.001, satellites=9, speed_kmh=30) for i in range(3)])
    second = encode_tcp_packet([AvlRecordSpec(time=t0 + timedelta(seconds=60 + 10 * i), latitude=-6.81,
                                              longitude=39.21 + i * 0.001, satellites=9, speed_kmh=30)
                                for i in range(4)])

    async def session() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(encode_imei(IMEIS["outage"]))
        assert await asyncio.wait_for(r.readexactly(1), 5) == b"\x01"
        return r, w

    outage = Outage()

    async def scenario() -> list[str]:
        gw = Gateway(gateway_config())
        await gw.start()
        log: list[str] = []
        try:
            r, w = await session()
            w.write(first)
            log.append(f"ack {int.from_bytes(await asyncio.wait_for(r.readexactly(4), 5), 'big')}")
            await asyncio.to_thread(outage.set, True)
            w.write(second)
            try:
                got = await asyncio.wait_for(r.read(4), 10)
                log.append("closed" if got == b"" else f"ack {int.from_bytes(got, 'big')}")
            except TimeoutError:
                log.append("no reply")
            w.close()
            await asyncio.to_thread(outage.set, False)
            await asyncio.sleep(0.5)
            r, w = await session()                       # the tracker reconnects and resends its flash
            w.write(second)
            log.append(f"ack {int.from_bytes(await asyncio.wait_for(r.readexactly(4), 10), 'big')}")
            w.write(second)                              # and once more: the ACK it missed
            log.append(f"ack {int.from_bytes(await asyncio.wait_for(r.readexactly(4), 10), 'big')}")
            w.close()
        finally:
            await asyncio.to_thread(outage.set, False)
            await gw.stop()
        return log

    try:
        log = asyncio.run(asyncio.wait_for(scenario(), 90))
    finally:
        outage.close()
    assert log[0] == "ack 3" and log[1] in ("closed", "no reply") and log[2:] == ["ack 4", "ack 4"]
    assert count("SELECT count(*) FROM positions WHERE device_id = %s", device.pk) == 7          # nothing lost
    assert count("SELECT count(*) FROM (SELECT device_time FROM positions WHERE device_id = %s GROUP BY "
                 "device_time HAVING count(*) > 1) d", device.pk) == 0                            # no duplicates


def test_backlog_bad_data_and_malformed_packets_through_the_pipeline(fleet):  # type: ignore[no-untyped-def]
    org, devices, port = fleet
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=8)

    async def scenario() -> dict[str, object]:
        gw = Gateway(gateway_config())
        await gw.start()
        try:
            runs = await asyncio.gather(
                run_device("127.0.0.1", port, IMEIS["gap"], "coverage_gap_backlog", steps=240, interval_s=30,
                           start=start, gap_minutes=60),
                run_device("127.0.0.1", port, IMEIS["jump"], "gps_jump", steps=120, interval_s=30, start=start),
                run_device("127.0.0.1", port, IMEIS["invalid"], "invalid_fix", steps=120, interval_s=30,
                           start=start),
                run_device("127.0.0.1", port, IMEIS["bad"], "malformed_packet", steps=60, interval_s=30,
                           start=start),
                run_device("127.0.0.1", port, IMEIS["steady"], "normal_day", steps=120, interval_s=30,
                           start=start))
            # A record older than what is already stored (e.g. a second buffer flushed later): it is "late".
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(encode_imei(IMEIS["gap"]))
            assert await asyncio.wait_for(r.readexactly(1), 5) == b"\x01"
            late_t = start + timedelta(minutes=95, seconds=7)
            w.write(encode_tcp_packet([AvlRecordSpec(time=late_t, latitude=-6.79, longitude=39.21, satellites=9,
                                                     speed_kmh=25, io={239: (1, 1)})]))
            assert int.from_bytes(await asyncio.wait_for(r.readexactly(4), 5), "big") == 1
            w.close()
        finally:
            await gw.stop()
        return {r.scenario: r for r in runs}

    runs = asyncio.run(scenario())
    gap, jump, invalid, bad, steady = (runs[k] for k in ("coverage_gap_backlog", "gps_jump", "invalid_fix",
                                                          "malformed_packet", "normal_day"))
    assert gap.records_acked == 240 and gap.max_backlog > 100            # buffered, then uploaded in one go
    assert bad.malformed_closed and bad.records_acked == 60 and steady.records_acked == 120   # others unaffected
    proc = asyncio.run(processor_pass())

    gap_dev, gap_vehicle = devices["gap"]
    newest = count("SELECT extract(epoch FROM max(device_time))::bigint FROM positions WHERE device_id = %s",
                   gap_dev.pk)
    state = DeviceState.unscoped.get(device=gap_dev)
    assert int(state.device_time.timestamp()) == newest                  # live marker = newest, never behind
    assert count("SELECT count(*) FROM positions WHERE device_id = %s AND is_late", gap_dev.pk) == 1
    assert count("SELECT count(*) FROM positions WHERE device_id = %s AND server_time - device_time > "
                 "interval '1 hour'", gap_dev.pk) > 100              # the backlog arrived hours late
    assert proc.stats.recompute_requests >= 1 and proc.stats.recomputes_run >= 1
    gap_trips = Trip.unscoped.filter(vehicle=gap_vehicle)
    assert gap_trips.exists()

    jump_dev, jump_vehicle = devices["jump"]
    assert count("SELECT count(*) FROM positions WHERE device_id = %s AND is_outlier", jump_dev.pk) == 1
    inv_dev, inv_vehicle = devices["invalid"]
    invalid_n = count("SELECT count(*) FROM positions WHERE device_id = %s AND NOT gps_valid", inv_dev.pk)
    assert invalid_n > 0
    # Routes exclude outliers and invalid fixes; distance has no 50 km jump.
    from django.test import Client

    from apps.accounts.models import User
    from apps.organizations.models import OrganizationMembership
    from apps.rbac.models import Role
    user = User.objects.create_user(email="i4@integrity.test", password="Integrity-Pass-2026!")
    OrganizationMembership.unscoped.create(organization=org, user=user,
                                           role=Role.objects.get(slug="org_admin", organization__isnull=True))
    client = Client()
    token = client.post("/api/v1/auth/login/", {"email": "i4@integrity.test", "password": "Integrity-Pass-2026!"},
                        content_type="application/json").json()["access"]
    for vehicle in (jump_vehicle, inv_vehicle):
        route = client.get("/api/v1/tracking/history/", {"vehicle": vehicle.pk,
                                                        "start": (start - timedelta(hours=1)).isoformat(),
                                                        "end": datetime.now(UTC).isoformat(), "zoom": 22},
                           HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_ORGANIZATION_ID=str(org.pk)).json()
        assert route["points"] and all(abs(p[1] + 6.8) < 0.3 and p[1] != 0 for p in route["points"])
    jump_km = sum(t.distance_km for t in Trip.unscoped.filter(vehicle=jump_vehicle))
    steady_km = sum(t.distance_km for t in Trip.unscoped.filter(vehicle=devices["steady"][1]))
    assert jump_km < steady_km * 1.5 + 5                                 # the 50 km jump added nothing


def test_stream_loss_processor_resumes_from_database(fleet):  # type: ignore[no-untyped-def]
    """Records stored while Redis lost the stream are analysed from the database when the next record arrives."""
    from apps.analysis.tests.test_processor_analysis import entries, insert_positions, journey, run

    org, devices, port = fleet
    device, vehicle = devices["steady"]
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=4)
    recs = journey(t0, n_trips=2)
    insert_positions(device, vehicle, recs)
    head, tail = recs[:5], recs[-5:]                     # only these reached the stream
    from gpsproc.app import Processor as P

    asyncio.run(run(P(ProcessorConfig(database_url=dsn(), redis_url="redis://unused")), [entries(device, head)]))
    assert Trip.unscoped.filter(vehicle=vehicle).count() == 0
    proc = P(ProcessorConfig(database_url=dsn(), redis_url="redis://unused"))
    asyncio.run(run(proc, [entries(device, tail)]))
    assert proc.stats.caught_up > 100
    assert Trip.unscoped.filter(vehicle=vehicle).count() == 2            # both trips, none skipped
