"""Route history: positions for one vehicle and time range, simplified for drawing, with stops and a summary
(spec A12 route history and playback). Must run inside the vehicle's tenant context."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from apps.tracking.models import Position

EARTH_M = 6_371_008.8


def max_range() -> timedelta:
    return timedelta(days=settings.HISTORY_MAX_DAYS)


def point_budget() -> int:
    return int(settings.HISTORY_POINT_BUDGET)


@dataclass(frozen=True)
class Point:
    t: datetime
    lat: float
    lon: float
    speed: float
    heading: float | None
    ignition: bool | None


@dataclass(frozen=True)
class Stop:
    start: int   # index of the first stopped point
    end: int     # index of the last stopped point

    def as_dict(self, pts: list[Point]) -> dict[str, object]:
        a, b = pts[self.start], pts[self.end]
        return {"start": a.t.isoformat(), "end": b.t.isoformat(), "duration_s": int((b.t - a.t).total_seconds()),
                "latitude": a.lat, "longitude": a.lon,
                "engine_off": any(pts[i].ignition is False for i in range(self.start, self.end + 1))}


def fetch(vehicle_id: int, start: datetime, end: datetime) -> list[Point]:
    """Valid, non-outlier fixes in device-time order over the half-open range [start, end), so consecutive
    windows (e.g. day-by-day exports) never contain the same point twice. Backlog (late) records are included."""
    rows = (Position.objects.filter(vehicle_id=vehicle_id, device_time__gte=start, device_time__lt=end,
                                    gps_valid=True, is_outlier=False)
            .order_by("device_time")
            .values_list("device_time", "latitude", "longitude", "speed_kmh", "heading", "ignition"))
    return [Point(t, lat, lon, speed or 0.0, heading, ign) for t, lat, lon, speed, heading, ign in rows]


def distance_m(a: Point, b: Point) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(h)))


def tolerance_for_zoom(zoom: int, lat: float) -> float:
    """About 1.5 screen pixels at this zoom, in metres (Web Mercator ground resolution)."""
    metres_per_pixel = 156_543.03392 * math.cos(math.radians(lat)) / (2 ** max(0, min(zoom, 22)))
    return 1.5 * metres_per_pixel


def detect_stops(pts: list[Point], speed_kmh: float = 3.0, min_seconds: int = 180) -> list[Stop]:
    stops: list[Stop] = []
    i, n = 0, len(pts)
    while i < n:
        if pts[i].speed >= speed_kmh:
            i += 1
            continue
        j = i
        while j + 1 < n and pts[j + 1].speed < speed_kmh:
            j += 1
        if (pts[j].t - pts[i].t).total_seconds() >= min_seconds:
            stops.append(Stop(i, j))
        i = j + 1
    return stops


def simplify(pts: list[Point], tolerance_m: float, keep: set[int]) -> list[int]:
    """Douglas-Peucker on a local equirectangular projection (metres); indices in ``keep`` always survive."""
    n = len(pts)
    if n <= 2:
        return list(range(n))
    lat0 = math.radians(sum(p.lat for p in pts) / n)
    xs = [math.radians(p.lon) * math.cos(lat0) * EARTH_M for p in pts]
    ys = [math.radians(p.lat) * EARTH_M for p in pts]
    marked = bytearray(n)
    marked[0] = marked[-1] = 1
    for k in keep:
        if 0 <= k < n:
            marked[k] = 1
    anchors = [i for i in range(n) if marked[i]]
    stack = list(zip(anchors, anchors[1:], strict=False))
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay, bx, by = xs[a], ys[a], xs[b], ys[b]
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        best, best_i = -1.0, -1
        for i in range(a + 1, b):
            d = (abs(dy * xs[i] - dx * ys[i] + bx * ay - by * ax) / seg) if seg else math.hypot(xs[i] - ax, ys[i] - ay)
            if d > best:
                best, best_i = d, i
        if best > tolerance_m:
            marked[best_i] = 1
            stack.extend(((a, best_i), (best_i, b)))
    return [i for i in range(n) if marked[i]]


def speeding_episodes(pts: list[Point], limit_kmh: float, min_seconds: int) -> list[list[float]]:
    """Continuous stretches above ``limit_kmh`` lasting at least ``min_seconds``: ``[start_s, end_s, max_kmh]``.
    Used to colour the route; alerts and stored overspeed events come from the processor (Phase 7)."""
    out: list[list[float]] = []
    i, n = 0, len(pts)
    while i < n:
        if pts[i].speed <= limit_kmh:
            i += 1
            continue
        j = i
        while j + 1 < n and pts[j + 1].speed > limit_kmh:
            j += 1
        if (pts[j].t - pts[i].t).total_seconds() >= min_seconds:
            out.append([int(pts[i].t.timestamp()), int(pts[j].t.timestamp()),
                        round(max(p.speed for p in pts[i:j + 1]), 1)])
        i = j + 1
    return out


def summarize(pts: list[Point], stops: list[Stop], moving_kmh: float = 3.0) -> dict[str, object]:
    """Distance, driving / idle / stopped time, top and average moving speed (spec A12 summary)."""
    if not pts:
        return {"points": 0, "distance_km": 0.0, "max_speed_kmh": 0.0, "avg_moving_speed_kmh": 0.0,
                "driving_s": 0, "idle_s": 0, "stopped_s": 0, "start": None, "end": None}
    distance = moving_distance = driving = idle = 0.0
    for a, b in zip(pts, pts[1:], strict=False):
        d, dt = distance_m(a, b), (b.t - a.t).total_seconds()
        moving = a.speed >= moving_kmh or b.speed >= moving_kmh
        if moving:
            distance += d          # no distance while standing still: GPS drift is not travel (spec A4 rule 8)
            moving_distance += d
            driving += dt
        elif a.ignition is True:
            idle += dt             # engine on, not moving
    stopped = sum((pts[s.end].t - pts[s.start].t).total_seconds() for s in stops)
    avg = (moving_distance / 1000) / (driving / 3600) if driving else 0.0
    return {"points": len(pts), "distance_km": round(distance / 1000, 2),
            "max_speed_kmh": round(max(p.speed for p in pts), 1), "avg_moving_speed_kmh": round(avg, 1),
            "driving_s": int(driving), "idle_s": int(idle), "stopped_s": int(stopped),
            "start": pts[0].t.isoformat(), "end": pts[-1].t.isoformat()}


SPEED_BUCKETS = 600


def speed_series(pts: list[Point], buckets: int = SPEED_BUCKETS) -> list[list[float]]:
    """Speed over time, sampled by time (not by route geometry): the maximum speed in each of ~``buckets`` equal
    time slices, so peaks such as overspeeding are never smoothed away. Rows are ``[epoch_s, kmh]``."""
    if not pts:
        return []
    t0, t1 = pts[0].t.timestamp(), pts[-1].t.timestamp()
    width = max(1.0, (t1 - t0) / buckets)
    out: dict[int, float] = {}
    for p in pts:
        b = int((p.t.timestamp() - t0) // width)
        out[b] = max(out.get(b, 0.0), p.speed)
    return [[int(t0 + b * width), round(v, 1)] for b, v in sorted(out.items())]


def forced_points(pts: list[Point], stops: list[Stop], budget: int) -> set[int]:
    """Points that must survive simplification, limited to half the budget. In order of importance: stop starts,
    stop ends, ignition changes. Busy long ranges (thousands of stops) fall back to evenly spaced stop starts."""
    cap = budget // 2
    starts = [s.start for s in stops]
    ends = [s.end for s in stops]
    ignition = [i for i in range(1, len(pts)) if pts[i].ignition != pts[i - 1].ignition]
    keep: set[int] = set()
    for group in (starts, ends, ignition):
        if len(keep) + len(group) > cap:
            break
        keep.update(group)
    if not keep and starts:                # even stop starts exceed the cap: keep an even sample of them
        step = max(1, len(starts) // cap)
        keep = set(starts[::step][:cap])
    return keep


def build(vehicle_id: int, start: datetime, end: datetime, zoom: int, stop_minutes: float) -> dict[str, object]:
    pts = fetch(vehicle_id, start, end)
    budget = point_budget()
    stops = detect_stops(pts, speed_kmh=settings.STOP_SPEED_KMH, min_seconds=int(stop_minutes * 60))
    keep = forced_points(pts, stops, budget)
    tolerance = tolerance_for_zoom(zoom, pts[0].lat) if pts else 0.0
    kept = simplify(pts, tolerance, keep)
    for _ in range(40):                    # coarsen until it fits; forced points are capped, so this converges
        if len(kept) <= budget:
            break
        tolerance *= 2
        kept = simplify(pts, tolerance, keep)
    return {
        "points": [[int(pts[i].t.timestamp()), round(pts[i].lat, 6), round(pts[i].lon, 6), round(pts[i].speed, 1),
                    None if pts[i].heading is None else round(pts[i].heading), pts[i].ignition] for i in kept],
        "stops": [s.as_dict(pts) for s in stops],
        "speed": speed_series(pts),
        "geofence_events": geofence_events(vehicle_id, start, end),
        "speeding": speeding_episodes(pts, settings.OVERSPEED_WARNING_KMH, settings.OVERSPEED_MIN_SECONDS),
        "summary": {**summarize(pts, stops, settings.STOP_SPEED_KMH), "returned_points": len(kept),
                    "point_budget": budget, "tolerance_m": round(tolerance, 2),
                    "speeding_limit_kmh": settings.OVERSPEED_WARNING_KMH},
    }


def detail(vehicle_id: int, start: datetime, end: datetime, zoom: int,
           bbox: tuple[float, float, float, float]) -> dict[str, object]:
    """The route inside the visible area at this zoom's tolerance (spec A12: higher resolution only for the
    zoomed-in segment). Runs of consecutive points inside the box are simplified separately and returned as
    ``segments``, so a line never jumps across a part of the route that left the box."""
    south, west, north, east = bbox
    pts = fetch(vehicle_id, start, end)
    budget = point_budget()
    runs: list[list[Point]] = []
    current: list[Point] = []
    for p in pts:
        if south <= p.lat <= north and west <= p.lon <= east:
            current.append(p)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    tolerance = tolerance_for_zoom(zoom, (south + north) / 2)
    for _ in range(40):
        segments = [[run[i] for i in simplify(run, tolerance, set())] for run in runs]
        if sum(len(seg) for seg in segments) <= budget:
            break
        tolerance *= 2
    return {"segments": [[[int(p.t.timestamp()), round(p.lat, 6), round(p.lon, 6)] for p in seg] for seg in segments],
            "summary": {"points_in_area": sum(len(r) for r in runs), "returned_points": sum(len(s) for s in segments),
                        "tolerance_m": round(tolerance, 2)}}


def geofence_events(vehicle_id: int, start: datetime, end: datetime) -> list[dict[str, object]]:
    """Entry/exit/dwell markers for the route (spec A12)."""
    from apps.geofences.models import GeofenceEvent

    rows = (GeofenceEvent.objects.filter(vehicle_id=vehicle_id, occurred_at__gte=start, occurred_at__lt=end)
            .select_related("geofence").order_by("occurred_at")[:500])
    return [{"event": e.event, "geofence": e.geofence.name, "at": int(e.occurred_at.timestamp()),
             "lat": e.lat, "lon": e.lon, "delayed": e.delayed} for e in rows]
