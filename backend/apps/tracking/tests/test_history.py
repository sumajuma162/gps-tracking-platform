"""Route history (spec A12): simplification, stops, summary, limits, permissions, exports, performance."""
from __future__ import annotations

import csv
import io
import math
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device
from apps.fleet.models import Vehicle
from apps.tracking import history
from apps.tracking.history import Point

DAY = datetime(2026, 9, 30, tzinfo=UTC)


def straight(n: int, *, speed: float = 40.0, start: datetime = DAY, step_s: int = 10) -> list[Point]:
    return [Point(start + timedelta(seconds=i * step_s), -6.80 + i * 0.0001, 39.28, speed, 0.0, True) for i in range(n)]


# --- pure functions -------------------------------------------------------------------------------------

def test_simplify_straight_line_keeps_endpoints_and_forced_points():
    pts = straight(500)
    assert history.simplify(pts, 5.0, set()) == [0, 499]
    assert history.simplify(pts, 5.0, {123}) == [0, 123, 499]


def test_simplify_keeps_corners():
    pts = straight(100) + [Point(DAY + timedelta(hours=1, seconds=i * 10), -6.80 + 99 * 0.0001, 39.28 + i * 0.0001,
                                 40, 90, True) for i in range(1, 100)]
    kept = history.simplify(pts, 5.0, set())
    assert kept == [0, 99, 198]                                     # start, the corner, the end


def test_detect_stops_needs_the_minimum_duration():
    moving = straight(10)
    short = [Point(DAY + timedelta(seconds=100 + i * 10), -6.80, 39.28, 0, None, True) for i in range(12)]   # 110 s
    long = [Point(DAY + timedelta(seconds=300 + i * 10), -6.79, 39.28, 0, None, False) for i in range(30)]   # 290 s
    pts = moving + short + straight(5, start=DAY + timedelta(seconds=230)) + long
    stops = history.detect_stops(pts, min_seconds=180)
    assert len(stops) == 1 and stops[0].as_dict(pts)["engine_off"] is True
    assert stops[0].as_dict(pts)["duration_s"] == 290


def test_speed_series_samples_by_time_and_keeps_peaks():
    pts = [Point(DAY + timedelta(seconds=i * 10), -6.8, 39.28, 40.0, 0.0, True) for i in range(8640)]
    pts[4321] = Point(pts[4321].t, -6.8, 39.28, 112.0, 0.0, True)      # one 10-second overspeed
    series = history.speed_series(pts)
    assert 590 <= len(series) <= 601
    assert max(v for _, v in series) == 112.0                          # the peak survives
    assert sum(1 for _, v in series if v == 40.0) >= 590               # the rest of the day stays at 40
    assert [t for t, _ in series] == sorted(t for t, _ in series)
    assert history.speed_series([]) == []


def test_busy_ranges_still_fit_the_budget(settings):
    """Thousands of stops used to force more points than the budget allows, so coarsening never converged."""
    settings.HISTORY_POINT_BUDGET = 1000
    pts: list[Point] = []
    t = DAY
    for k in range(2500):                       # 2,500 short drive + 4-minute stop cycles
        for i in range(6):
            pts.append(Point(t, -6.8 + k * 1e-4, 39.2 + i * 1e-3, 40, 90, True))
            t += timedelta(seconds=10)
        for _ in range(25):
            pts.append(Point(t, -6.8 + k * 1e-4, 39.2 + 6e-3, 0, None, False))
            t += timedelta(seconds=10)
    stops = history.detect_stops(pts, min_seconds=180)
    assert len(stops) == 2500
    keep = history.forced_points(pts, stops, 1000)
    assert 0 < len(keep) <= 500
    started = time.perf_counter()
    kept = history.simplify(pts, 50.0, keep)
    tol = 50.0
    for _ in range(40):
        if len(kept) <= 1000:
            break
        tol *= 2
        kept = history.simplify(pts, tol, keep)
    assert len(kept) <= 1000 and time.perf_counter() - started < 5


def test_summary_idle_average_speed_and_drift():
    t = DAY
    drive = [Point(t + timedelta(seconds=i * 10), -6.8, 39.2 + i * 0.001, 40, 90, True) for i in range(10)]   # ~1 km
    def still(offset: int, ignition: bool) -> list[Point]:      # standing, with ~22 m of GPS jitter
        return [Point(t + timedelta(seconds=offset + i * 10), -6.8 + (i % 2) * 2e-4, 39.209, 0, None, ignition)
                for i in range(30)]
    idle, parked = still(100, True), still(400, False)
    pts = drive + idle + parked
    s = history.summarize(pts, history.detect_stops(pts, min_seconds=180))
    assert 0.9 < s["distance_km"] < 1.05            # 22 m GPS jitter while standing adds nothing
    assert s["idle_s"] == 300 and s["driving_s"] == 100
    assert 30 < s["avg_moving_speed_kmh"] < 40


def test_speeding_episodes_ignore_short_spikes():
    t = DAY
    speeds = [50] * 5 + [95] * 1 + [50] * 5 + [90, 104, 99, 88] + [60] * 5     # a 10 s spike, then a 30 s episode
    pts = [Point(t + timedelta(seconds=i * 10), -6.8, 39.2, v, 0, True) for i, v in enumerate(speeds)]
    eps = history.speeding_episodes(pts, 80, 15)
    ts = int(t.timestamp())
    assert eps == [[ts + 110, ts + 140, 104.0]]


def test_zoom_tolerance():
    assert history.tolerance_for_zoom(10, -6.8) > history.tolerance_for_zoom(16, -6.8) > 0
    assert history.tolerance_for_zoom(16, -6.8) == pytest.approx(3.55, abs=0.05)   # ~2.4 m/px at z16 near the equator


# --- with data ------------------------------------------------------------------------------------------

pg_only = pytest.mark.skipif("connection.vendor != 'postgresql'", reason="positions live in PostgreSQL")
INSERT = ("INSERT INTO positions (organization_id, device_id, vehicle_id, device_time, latitude, longitude, speed_kmh, "
          "heading, ignition, gps_valid, is_outlier, protocol, record_hash) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, 'teltonika', %s)")


@pytest.fixture
def day(org_a):
    """A working day at 10 s: driving east with a 30-minute engine-off stop every 2 hours (8,640 points)."""
    vehicle = Vehicle.unscoped.create(organization=org_a, registration_number="T 123 HIS")
    device = Device.unscoped.create(organization=org_a, name="History", identifier="356307042441013",
                                    device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    rows, lon = [], 39.10
    for i in range(8640):
        t = DAY + timedelta(seconds=i * 10)
        stopped = (i % 720) >= 540                                  # last 30 min of every 2 h
        if not stopped:
            lon += 0.00011 * (1 + 0.3 * ((i // 37) % 3))             # ~40-60 km/h with gentle speed changes
        lat = -6.80 + 0.002 * ((i // 90) % 5) + 0.00003 * math.sin(i / 7)   # a zig-zag road with ~3 m GPS wiggle
        speed = 0.0 if stopped else 40.0 + 10 * ((i // 37) % 3)
        rows.append((org_a.pk, device.pk, vehicle.pk, t, lat, lon, speed, not stopped, True, False, f"h{i}"))
    rows.append((org_a.pk, device.pk, vehicle.pk, DAY + timedelta(hours=5, seconds=5), 0.0, 0.0, 0, True, False, False,
                 "nofix"))
    rows.append((org_a.pk, device.pk, vehicle.pk, DAY + timedelta(hours=6, seconds=5), -3.37, 36.68, 50, True, True,
                 True, "jump"))
    with connection.cursor() as cur:
        cur.executemany(INSERT, rows)
    return vehicle


def url(vehicle, extra: str = "", start: datetime = DAY, end: datetime = DAY + timedelta(days=1)) -> str:  # type: ignore[no-untyped-def]
    return (f"/api/v1/tracking/history/{extra}?vehicle={vehicle.pk}&start={start.isoformat().replace('+00:00', 'Z')}"
            f"&end={end.isoformat().replace('+00:00', 'Z')}")


@pg_only
def test_a_day_of_history(org_a, make_member, api, day):
    client = api(make_member(org_a, "monitoring_officer"))
    started = time.perf_counter()
    body = client.get(url(day) + "&zoom=14").json()
    elapsed = time.perf_counter() - started
    s = body["summary"]
    assert s["points"] == 8640                                       # no-fix and outlier records excluded
    assert 100 < s["returned_points"] < 3000                         # simplified for drawing
    assert len(body["stops"]) == 12 and all(st["engine_off"] for st in body["stops"])
    assert {st["duration_s"] for st in body["stops"]} == {1790}
    assert s["max_speed_kmh"] == 60.0 and 4 * 3600 < s["stopped_s"] < 6.5 * 3600
    assert 90 < s["distance_km"] < 300
    times = [p[0] for p in body["points"]]
    assert times == sorted(times) and times[0] == int(DAY.timestamp())
    stop_starts = {int(datetime.fromisoformat(st["start"]).timestamp()) for st in body["stops"]}
    assert stop_starts <= set(times)                                 # stop boundaries are never simplified away
    assert all(abs(p[1]) > 1 for p in body["points"])                # the 0,0 no-fix record is not on the route
    driving = [v for _, v in body["speed"] if v > 0]
    assert len(body["speed"]) > 550 and len(driving) > 0.6 * len(body["speed"])   # speed chart reflects driving
    detailed = client.get(url(day) + "&zoom=18").json()["summary"]["returned_points"]
    assert detailed > s["returned_points"]
    assert elapsed < 1.5, f"a day of history took {elapsed:.2f} s"


@pg_only
def test_limits_and_validation(org_a, make_member, api, day):
    client = api(make_member(org_a, "monitoring_officer"))
    too_long = client.get(url(day, end=DAY + timedelta(days=32)))
    assert too_long.status_code == 400 and "31 days" in str(too_long.json())
    backwards = client.get(url(day, start=DAY + timedelta(hours=2), end=DAY))
    assert backwards.status_code == 400
    empty = client.get(url(day, start=DAY - timedelta(days=3), end=DAY - timedelta(days=2))).json()
    assert empty["points"] == [] and empty["summary"]["points"] == 0
    assert client.get(url(day).replace(f"vehicle={day.pk}", "vehicle=999999")).status_code == 404


@pg_only
@pytest.mark.parametrize("role,view,export", [("fleet_manager", True, True), ("monitoring_officer", True, False),
                                              ("viewer", True, False), ("auditor", False, False),
                                              ("driver", False, False)])
def test_permissions(org_a, make_member, api, day, role, view, export):
    client = api(make_member(org_a, role))
    assert (client.get(url(day)).status_code == 200) is view
    assert (client.get(url(day, "export/") + "&type=csv").status_code == 200) is export


@pg_only
def test_exports_are_full_resolution_valid_and_audited(org_a, make_member, api, day):
    client = api(make_member(org_a, "fleet_manager"))           # export needs reports.export as well
    window = dict(start=DAY, end=DAY + timedelta(hours=3))
    resp = client.get(url(day, "export/", **window) + "&type=csv")
    assert resp["Content-Disposition"] == 'attachment; filename="T-123-HIS_2026-09-30.csv"'
    rows = list(csv.DictReader(io.StringIO(resp.content.decode())))
    assert len(rows) == 1080 and rows[0]["time_utc"].startswith("2026-09-30T00:00:00")
    gpx = ET.fromstring(client.get(url(day, "export/", **window) + "&type=gpx").content)  # noqa: S314 (our own output)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    assert len(gpx.findall(".//g:trkpt", ns)) == 1080
    kml = ET.fromstring(client.get(url(day, "export/", **window) + "&type=kml").content)  # noqa: S314 (our own output)
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    assert len(kml.find(".//k:LineString/k:coordinates", ns).text.split()) == 1080
    assert len(kml.findall(".//k:Point", ns)) == 1                   # the 02:30 stop
    assert AuditLog.objects.filter(action="routes.exported").count() == 3
    assert client.get(url(day, "export/") + "&type=xls").status_code == 400
    nothing = client.get(url(day, "export/", start=DAY - timedelta(days=2), end=DAY - timedelta(days=1)) + "&type=csv")
    assert nothing.status_code == 404


@pg_only
def test_history_uses_the_vehicle_snapshot(org_a, make_member, api, day):
    """Positions recorded while a tracker was in another vehicle never appear in this vehicle's history."""
    other = Vehicle.unscoped.create(organization=org_a, registration_number="T 999 OTH")
    with connection.cursor() as cur:
        cur.execute(INSERT, (org_a.pk, Device.unscoped.get().pk, other.pk, DAY + timedelta(hours=1, seconds=3),
                             -6.5, 39.5, 30, True, True, False, "other"))
    body = api(make_member(org_a, "monitoring_officer")).get(url(day)).json()
    assert all(p[1] != -6.5 for p in body["points"]) and body["summary"]["points"] == 8640
    timezone.now()


@pg_only
def test_consecutive_windows_never_share_a_point(org_a, make_member, api, day):
    client = api(make_member(org_a, "fleet_manager"))
    counts = []
    for h in range(0, 6, 3):
        window = dict(start=DAY + timedelta(hours=h), end=DAY + timedelta(hours=h + 3))
        resp = client.get(url(day, "export/", **window) + "&type=csv")
        counts.append(len(resp.content.decode().strip().splitlines()) - 1)
    whole = client.get(url(day, "export/", start=DAY, end=DAY + timedelta(hours=6)) + "&type=csv")
    assert sum(counts) == len(whole.content.decode().strip().splitlines()) - 1 == 2160


MONTH_SQL = """
INSERT INTO positions (organization_id, device_id, vehicle_id, device_time, latitude, longitude, speed_kmh, heading,
                       ignition, gps_valid, is_outlier, protocol, record_hash)
SELECT %s, %s, %s, %s::timestamptz + make_interval(secs => i * 30),
       -6.80 + 0.01 * sin(i / 300.0) + 0.00003 * sin(i / 7.0),
       39.10 + 0.25 * sin(i / 2000.0),
       CASE WHEN (i %% 240) >= 150 THEN 0 WHEN (i %% 600) < 20 THEN 92 ELSE 45 END,
       90, (i %% 240) < 150, true, false, 'teltonika', 'm' || i
FROM generate_series(0, 89279) AS i
"""


@pg_only
def test_a_31_day_route_stays_within_the_point_budget_and_time(org_a, make_member, api):
    """Phase 6 done criterion (spec Part B) and the A15 target: 31 days downsampled in under 3 s."""
    vehicle = Vehicle.unscoped.create(organization=org_a, registration_number="T 31 MON")
    device = Device.unscoped.create(organization=org_a, name="Month", identifier="490154203237518",
                                    device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    start = DAY - timedelta(days=30)
    with connection.cursor() as cur:
        cur.execute(MONTH_SQL, [org_a.pk, device.pk, vehicle.pk, start])
    client = api(make_member(org_a, "monitoring_officer"))
    began = time.perf_counter()
    body = client.get(url(vehicle, start=start, end=start + timedelta(days=31)) + "&zoom=12").json()
    elapsed = time.perf_counter() - began
    s = body["summary"]
    assert s["points"] == 89_280
    assert s["returned_points"] <= s["point_budget"] == 5000
    assert len(body["stops"]) == 372                                 # one 45-minute stop every 2 hours
    assert len(body["speeding"]) > 100 and all(e[2] == 92.0 for e in body["speeding"])
    assert len(body["speed"]) <= 601
    print(f"\n31-day route: {s['points']} points -> {s['returned_points']} in {elapsed:.2f} s")
    assert elapsed < 3.0, f"31-day route took {elapsed:.2f} s"


@pg_only
def test_detail_for_the_visible_area(org_a, make_member, api, day):
    client = api(make_member(org_a, "monitoring_officer"))
    whole = client.get(url(day) + "&zoom=12").json()
    area = "&bbox=-6.81,39.12,-6.79,39.16&zoom=17"
    body = client.get(url(day) + area).json()
    inside = [p for seg in body["segments"] for p in seg]
    assert inside and all(-6.81 <= p[1] <= -6.79 and 39.12 <= p[2] <= 39.16 for p in inside)
    assert body["summary"]["returned_points"] > sum(1 for p in whole["points"] if 39.12 <= p[2] <= 39.16)
    assert client.get(url(day) + "&bbox=1,2,3").status_code == 400
