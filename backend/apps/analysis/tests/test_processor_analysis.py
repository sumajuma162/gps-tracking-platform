"""Phase 7 acceptance against PostgreSQL: the real processor turns stream records into trips, stops, overspeed and
geofence events; resumes after a restart; and recomputes backlog windows (spec A10, A16, Scenario 4)."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.analysis.models import RecomputeRequest, SpeedEvent, Stop, Trip
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import Driver, DriverAssignment, Vehicle
from apps.geofences.models import Geofence, GeofenceEvent
from apps.organizations.models import Organization
from apps.tracking.models import ProcessingCheckpoint
from apps.tracking.tests.test_positions import ALL_APPS

pytest.importorskip("gpsproc.app", reason="add ../processor to PYTHONPATH")
from gpsproc.app import Processor, ProcessorConfig  # noqa: E402

pytestmark = [pytest.mark.skipif("connection.vendor != 'postgresql'"),
              pytest.mark.django_db(transaction=True, available_apps=[*ALL_APPS, "apps.analysis", "apps.geofences"])]
LAT, LON = -6.80, 39.20


class NullLayer:
    async def group_send(self, group: str, message: dict) -> None:  # type: ignore[type-arg]
        return None


def journey(t0: datetime, n_trips: int = 2) -> list[dict]:  # type: ignore[type-arg]
    """Parked 5 min -> [drive 15 min (1 min at 95 km/h) through the depot -> parked 10 min] x n."""
    recs, t, lon, i = [], t0, LON, 0

    def add(speed: float, ign: bool) -> None:
        nonlocal t, lon, i
        recs.append({"device_time": t.isoformat(), "server_time": t.isoformat(), "latitude": LAT, "longitude": lon,
                     "speed_kmh": speed, "heading": 90.0, "ignition": ign, "motion": speed > 3, "gps_valid": True,
                     "is_outlier": False, "is_late": False, "odometer_km": None, "events": [], "record_hash": f"r{i}"})
        i += 1
        t += timedelta(seconds=10)
        lon += speed / 3.6 * 10 / 110_400
    for _ in range(30):
        add(0, False)
    for _trip in range(n_trips):
        for k in range(90):
            add(95 if 20 <= k < 26 else 50, True)
        for _ in range(60):
            add(0, False)
    return recs


def insert_positions(device: Device, vehicle: Vehicle, recs: list[dict], late: bool = False) -> None:  # type: ignore[type-arg]
    with connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO positions (organization_id, device_id, vehicle_id, device_time, latitude, longitude, "
            "speed_kmh, heading, ignition, motion, gps_valid, is_outlier, is_late, protocol, record_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,false,%s,'teltonika',%s) ON CONFLICT DO NOTHING",
            [(device.organization_id, device.pk, vehicle.pk, r["device_time"], r["latitude"], r["longitude"],
              r["speed_kmh"], r["heading"], r["ignition"], r["motion"], late, r["record_hash"]) for r in recs])


def entries(device: Device, recs: list[dict]) -> list[tuple[bytes, dict[bytes, bytes]]]:  # type: ignore[type-arg]
    return [(f"{i}-0".encode(), {b"device_id": str(device.pk).encode(), b"record": json.dumps(r).encode()})
            for i, r in enumerate(recs)]


@pytest.fixture
def fleet():  # type: ignore[no-untyped-def]
    org = Organization.objects.create(name="Analysis Ltd", slug="analysis-ltd")
    vehicle = Vehicle.unscoped.create(organization=org, registration_number="T 777 ANA")
    driver = Driver.unscoped.create(organization=org, first_name="Asha", last_name="Said")
    device = Device.unscoped.create(organization=org, name="Analysed", identifier="356307042441013",
                                    device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    now = timezone.now()
    DeviceAssignment.unscoped.create(organization=org, device=device, vehicle=vehicle, assigned_at=now)
    DriverAssignment.unscoped.create(organization=org, driver=driver, vehicle=vehicle, assigned_at=now)
    depot = Geofence.unscoped.create(organization=org, name="Depot", kind="circle", center_lat=LAT,
                                     center_lon=LON + 0.03, radius_m=300)
    return org, vehicle, driver, device, depot


def processor() -> Processor:
    db = connection.settings_dict
    dsn = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST'] or 'localhost'}:{db['PORT'] or 5432}/{db['NAME']}"
    return Processor(ProcessorConfig(database_url=dsn, redis_url="redis://unused", recompute_debounce_s=0))


async def run(proc: Processor, batches: list[list]) -> None:  # type: ignore[type-arg]
    import asyncpg
    proc.pool = await asyncpg.create_pool(proc.config.database_url, min_size=1, max_size=2)
    proc.layer = NullLayer()  # type: ignore[assignment]
    try:
        for batch in batches:
            await proc.handle(batch)
    finally:
        await proc.pool.close()


def test_live_analysis_restart_and_backlog_recompute(fleet):  # type: ignore[no-untyped-def]
    org, vehicle, driver, device, depot = fleet
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=6)
    recs = journey(t0, n_trips=2)
    insert_positions(device, vehicle, recs)
    half = 30 + 150 + 45                                             # restart in the middle of trip 2 (180..269)
    asyncio.run(run(processor(), [entries(device, recs[:half])]))
    assert Trip.unscoped.count() == 1
    state = ProcessingCheckpoint.objects.get(device=device).state
    assert state["trip"] is not None                                 # trip 2 open, checkpointed
    asyncio.run(run(processor(), [entries(device, recs[half:]), entries(device, recs[half:])]))   # + redelivery

    trips = list(Trip.unscoped.order_by("start_time"))
    assert len(trips) == 2
    for trip in trips:
        assert (trip.vehicle_id, trip.driver_id, trip.organization_id) == (vehicle.pk, driver.pk, org.pk)
        assert trip.distance_km == pytest.approx((84 * 50 + 6 * 95) / 3600 * 10, rel=0.05)  # 13.2 km
        assert trip.max_speed_kmh == 95 and trip.detection_mode == "ignition" and not trip.delayed
    assert Stop.unscoped.count() == 2                                # the two 10-minute parkings
    speed = list(SpeedEvent.unscoped.all())
    assert len(speed) == 2 and all(e.max_speed_kmh == 95 and e.severity == "warning" for e in speed)
    fence_events = [e.event for e in GeofenceEvent.unscoped.order_by("occurred_at")]
    assert fence_events[:2] == ["enter", "exit"]                     # drove through the depot on trip 1

    # --- backlog: two hours of earlier driving arrive late, in several batches ---------------------------
    backlog = journey(t0 - timedelta(hours=3), n_trips=1)
    for r in backlog:
        r["is_late"] = True
        r["record_hash"] = "b" + r["record_hash"]
    insert_positions(device, vehicle, backlog, late=True)
    asyncio.run(run(processor(), [entries(device, backlog[:100]), entries(device, backlog[100:])]))
    pending = RecomputeRequest.objects.filter(device=device, done_at__isnull=True)
    assert pending.count() == 1                                      # one widened request, not one per batch
    assert Trip.unscoped.count() == 2                                # nothing changes until the replay runs

    async def replay() -> int:
        import asyncpg
        proc = processor()
        pool = await asyncpg.create_pool(proc.config.database_url, min_size=1, max_size=2)
        try:
            return await proc.analysis.run_due_recomputes(pool, debounce_s=0)
        finally:
            await pool.close()
    assert asyncio.run(replay()) == 1

    trips = list(Trip.unscoped.order_by("start_time"))
    assert len(trips) == 3                                           # backlog trip added, live trips intact
    assert trips[0].delayed and trips[0].start_time < t0             # recomputed from late data
    assert not trips[1].delayed and not trips[2].delayed
    assert RecomputeRequest.objects.get().done_at is not None and RecomputeRequest.objects.get().replayed_points > 0
    assert len({(t.start_time) for t in trips}) == 3                 # no duplicates anywhere
