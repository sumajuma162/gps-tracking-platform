"""Analysis engine (spec A10 / A16 processing tests): trips in each mode, stops, idling, overspeed episodes,
geofence hysteresis and dwell, drift filter, outlier filter, behaviour events, state round-trip."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gpsproc.analysis.engine import Config, Engine, Sample
from gpsproc.analysis.geo import Fence, haversine_m, point_in_polygon

T0 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
LAT, LON = -6.80, 39.20
STEP = 0.00025                         # ~28 m of longitude per 10 s = ~100 km/h; scaled by speed below


def drive(t: datetime, lat: float, lon: float, *, seconds: int, speed: float, ignition: bool | None = True,
          motion: bool | None = None, every: int = 10, heading_east: bool = True, **kw):  # type: ignore[no-untyped-def]
    """Samples every ``every`` s at a constant speed eastwards; returns (samples, end_time, end_lon)."""
    out = []
    metres_per_step = speed / 3.6 * every
    dlon = metres_per_step / 110_400 * (1 if heading_east else -1)
    for _ in range(seconds // every):
        out.append(Sample(t=t, lat=lat, lon=lon, speed=speed, ignition=ignition,
                          motion=(speed > 3) if motion is None else motion, **kw))
        t += timedelta(seconds=every)
        lon += dlon
    return out, t, lon


def run(engine: Engine, samples: list[Sample]) -> list[dict]:  # type: ignore[type-arg]
    out: list[dict] = []  # type: ignore[type-arg]
    for s in samples:
        out += engine.process(s)
    return out


def kinds(items: list[dict], kind: str) -> list[dict]:  # type: ignore[type-arg]
    return [i for i in items if i["kind"] == kind]


def a_day(mode: str = "ignition", ignition: bool | None = True):  # type: ignore[no-untyped-def]
    """Park 5 min (engine off) -> drive 10 min at 50 -> park 10 min (engine off) -> drive 5 min -> park."""
    samples = []
    p, t, lon = drive(T0, LAT, LON, seconds=300, speed=0, ignition=False if ignition is not None else None)
    samples += p
    d, t, lon = drive(t, LAT, lon, seconds=600, speed=50, ignition=ignition)
    samples += d
    p, t, lon = drive(t, LAT, lon, seconds=600, speed=0, ignition=False if ignition is not None else None)
    samples += p
    d, t, lon = drive(t, LAT, lon, seconds=300, speed=50, ignition=ignition)
    samples += d
    p, t, lon = drive(t, LAT, lon, seconds=300, speed=0, ignition=False if ignition is not None else None)
    samples += p
    return samples


# --- trips ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,ignition", [("ignition", True), ("motion", None), ("speed", None)])
def test_two_trips_in_each_mode(mode, ignition):
    trips = kinds(run(Engine(Config(mode=mode)), a_day(mode, ignition)), "trip")
    assert len(trips) == 2
    first = trips[0]
    assert first["distance_km"] == pytest.approx(50 * 600 / 3600, rel=0.05)        # ~8.3 km in 10 min at 50
    assert first["max_speed_kmh"] == 50 and first["distance_source"] == "gps"
    assert 590 <= first["driving_s"] <= 610 and first["avg_moving_speed_kmh"] == pytest.approx(50, rel=0.05)
    assert first["start"] >= T0 + timedelta(seconds=290) and first["end"] <= T0 + timedelta(seconds=920)


def test_short_trips_are_discarded_as_noise():
    samples, t, lon = drive(T0, LAT, LON, seconds=60, speed=0, ignition=False)
    d, t, lon = drive(t, LAT, lon, seconds=60, speed=20)                            # 1 min, 0.3 km: yard manoeuvre
    samples += d + drive(t, LAT, lon, seconds=60, speed=0, ignition=False)[0]
    assert kinds(run(Engine(), samples), "trip") == []


def test_odometer_distance_when_the_model_has_one():
    samples = []
    p, t, lon = drive(T0, LAT, LON, seconds=60, speed=0, ignition=False, odometer_km=1000.0)
    d, t, lon = drive(t, LAT, lon, seconds=600, speed=50)
    d = [Sample(**{**x.__dict__, "odometer_km": 1000.0 + i * 0.139}) for i, x in enumerate(d)]
    samples = p + d + drive(t, LAT, lon, seconds=60, speed=0, ignition=False, odometer_km=1008.4)[0]
    trip = kinds(run(Engine(Config(use_odometer=True)), samples), "trip")[0]
    assert trip["distance_source"] == "odometer" and trip["distance_km"] == pytest.approx(8.4, abs=0.01)


def test_drift_while_parked_never_starts_a_motion_trip_or_adds_distance():
    jitter = [Sample(t=T0 + timedelta(seconds=10 * i), lat=LAT + (0.00012 if i % 2 else 0), lon=LON, speed=0,
                     motion=False) for i in range(180)]                             # +-13 m wander for 30 min
    assert kinds(run(Engine(Config(mode="motion")), jitter), "trip") == []
    # During an ignition trip, standing still with jitter inside the 30 m radius adds no distance
    samples, t, lon = drive(T0, LAT, LON, seconds=600, speed=50)
    samples += [Sample(t=t + timedelta(seconds=10 * i), lat=LAT + (0.00012 if i % 2 else 0), lon=lon, speed=0,
                       ignition=True) for i in range(60)]
    samples += [Sample(t=t + timedelta(seconds=700), lat=LAT, lon=lon, speed=0, ignition=False)]
    trip = kinds(run(Engine(), samples), "trip")[0]
    assert trip["distance_km"] == pytest.approx(50 * 600 / 3600, rel=0.05)
    assert trip["idle_s"] == 700        # engine on from the first idle sample until the engine-off record at +700 s


def test_outliers_and_invalid_fixes_add_no_distance():
    samples, t, lon = drive(T0, LAT, LON, seconds=600, speed=50)
    jump = Sample(t=samples[30].t + timedelta(seconds=5), lat=-3.37, lon=36.68, speed=50, ignition=True,
                  is_outlier=True)
    nofix = Sample(t=samples[31].t + timedelta(seconds=5), lat=0.0, lon=0.0, speed=0, ignition=True, gps_valid=False)
    samples = samples[:31] + [jump] + samples[31:32] + [nofix] + samples[32:]
    samples += [Sample(t=t, lat=LAT, lon=lon, speed=0, ignition=False)]
    trip = kinds(run(Engine(), samples), "trip")[0]
    assert trip["distance_km"] == pytest.approx(50 * 600 / 3600, rel=0.05)          # Arusha is 500 km away


# --- stops, idling, overspeed -------------------------------------------------------------------------------

def test_stops_need_the_minimum_duration():
    samples, t, lon = drive(T0, LAT, LON, seconds=120, speed=40)
    s1, t, lon = drive(t, LAT, lon, seconds=120, speed=0, ignition=True)             # 2 min: not a stop
    s2, t2, lon = drive(t + timedelta(seconds=0), LAT, lon, seconds=60, speed=40)
    s3, t3, lon = drive(t2, LAT, lon, seconds=300, speed=0, ignition=False)           # 5 min, engine off
    end = [Sample(t=t3, lat=LAT, lon=lon, speed=40, ignition=True)]
    stops = kinds(run(Engine(), samples + s1 + s2 + s3 + end), "stop")
    assert len(stops) == 1 and stops[0]["duration_s"] == 300 and stops[0]["engine_off"] is True


def test_idling_needs_engine_on_for_the_idle_duration():
    samples, t, lon = drive(T0, LAT, LON, seconds=60, speed=40)
    idle, t, lon = drive(t, LAT, lon, seconds=420, speed=0, ignition=True)            # 7 min engine on
    short, t, lon = drive(t + timedelta(seconds=0), LAT, lon, seconds=60, speed=40)
    idle2, t, lon = drive(t, LAT, lon, seconds=240, speed=0, ignition=True)           # 4 min: below 5 min
    out = run(Engine(), samples + idle + short + idle2 + [Sample(t=t, lat=LAT, lon=lon, speed=40, ignition=True)])
    idles = kinds(out, "idle")
    assert len(idles) == 1 and idles[0]["duration_s"] == 420


def test_overspeed_episodes_spikes_and_severity():
    seq = [50] * 6 + [95] + [50] * 6 + [85, 92, 101, 99, 88] + [60] * 4 + [83, 84, 86] + [70] * 3
    samples = [Sample(t=T0 + timedelta(seconds=10 * i), lat=LAT, lon=LON + i * 0.001, speed=v, ignition=True)
               for i, v in enumerate(seq)]
    eps = kinds(run(Engine(), samples), "overspeed")
    assert [(e["max_speed_kmh"], e["severity"], e["duration_s"]) for e in eps] == [
        (101, "critical", 40), (86, "warning", 20)]                                  # the 10 s spike is ignored
    assert eps[0]["threshold_kmh"] == 80


# --- geofences ----------------------------------------------------------------------------------------------

DEPOT = Fence(id=7, kind="circle", center=(LAT, LON + 0.01), radius_m=200, dwell_s=600)
PORT = Fence(id=8, kind="polygon", polygon=((-6.82, 39.28), (-6.82, 39.30), (-6.84, 39.30), (-6.84, 39.28)))


def test_geometry_helpers():
    assert haversine_m(-6.80, 39.20, -6.80, 39.21) == pytest.approx(1104, abs=5)
    assert point_in_polygon(-6.83, 39.29, list(PORT.polygon)) and not point_in_polygon(-6.83, 39.31, list(PORT.polygon))
    assert DEPOT.contains(LAT, LON + 0.01) and not DEPOT.contains(LAT, LON + 0.014)


def test_geofence_entry_exit_with_hysteresis_and_dwell():
    e = Engine(fences=[DEPOT])
    t = T0
    pts = [(LON, 0)] * 2 + [(LON + 0.0095, 30)] * 1 + [(LON + 0.0101, 0)] * 70 + [(LON + 0.02, 40)] * 3
    samples = []
    for lon, v in pts:
        samples.append(Sample(t=t, lat=LAT, lon=lon, speed=v, ignition=True))
        t += timedelta(seconds=10)
    events = kinds(run(e, samples), "geofence")
    assert [ev["event"] for ev in events] == ["enter", "dwell", "exit"]
    assert events[0]["at"] == T0 + timedelta(seconds=20)                             # time first seen inside
    assert events[1]["dwell_s"] >= 600


def test_boundary_flapping_produces_no_events():
    """GPS noise along the fence edge: inside, outside, inside... one sample each. Hysteresis needs 2 points or 20 s."""
    e = Engine(fences=[DEPOT])
    edge_in, edge_out = LON + 0.01 + 0.0017, LON + 0.01 + 0.0019                      # ~188 m and ~210 m from centre
    samples = [Sample(t=T0 + timedelta(seconds=10 * i), lat=LAT, lon=edge_out if i % 2 else edge_in, speed=5,
                      ignition=True) for i in range(1, 40)]
    samples.insert(0, Sample(t=T0, lat=LAT, lon=LON, speed=5, ignition=True))      # state: outside
    # 10 s apart: each "inside" sample is alone, so neither 2 points nor 20 s is reached
    assert kinds(run(e, samples), "geofence") == []


def test_outliers_never_enter_geofences():
    e = Engine(fences=[PORT])
    samples = [Sample(t=T0, lat=LAT, lon=LON, speed=40), Sample(t=T0 + timedelta(seconds=10), lat=-6.83, lon=39.29,
                                                              speed=40, is_outlier=True),
               Sample(t=T0 + timedelta(seconds=20), lat=-6.83, lon=39.29, speed=40, is_outlier=True)]
    assert kinds(run(e, samples), "geofence") == []


# --- behaviour, ordering, state ----------------------------------------------------------------------------

def test_behaviour_events_keep_the_last_known_position():
    samples = [Sample(t=T0, lat=LAT, lon=LON, speed=40),
               Sample(t=T0 + timedelta(seconds=10), lat=0, lon=0, speed=0, gps_valid=False,
                      events=("crash", "unknown")),
               Sample(t=T0 + timedelta(seconds=20), lat=LAT, lon=LON, speed=30, events=("harsh_braking",))]
    beh = kinds(run(Engine(), samples), "behavior")
    assert [(b["type"], b["lat"]) for b in beh] == [("crash", LAT), ("harsh_braking", LAT)]


def test_duplicates_and_out_of_order_records_are_ignored():
    samples = a_day()
    noisy = samples[:200] + [samples[150], samples[199]] + samples[200:]
    assert run(Engine(), noisy) == run(Engine(), samples)


def test_state_survives_a_restart_mid_trip():
    """Checkpoint the state as JSON halfway (processor restart), continue in a new engine: identical results."""
    samples = a_day()
    whole = run(Engine(), samples)
    first = Engine(fences=[DEPOT])
    part1 = run(first, samples[:75])                                                 # stops mid-way through trip 1
    assert first.open_trip_started() is not None
    restored = Engine(fences=[DEPOT], state=json.loads(json.dumps(first.state)))
    part2 = run(restored, samples[75:])
    strip = lambda items: [i for i in items if i["kind"] != "geofence"]  # noqa: E731
    assert strip(part1 + part2) == strip(whole)
