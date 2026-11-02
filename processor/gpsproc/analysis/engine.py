"""Per-device analysis state machine (spec A10): trips in three modes, stops, idling, overspeed episodes,
geofence entry/exit/dwell with hysteresis, and driving-behaviour events, with drift and outlier filtering.

Pure and deterministic: the same records in the same order always give the same outputs. Records must arrive
in device-time order; late (backlog) records are handled by replaying a window with a fresh engine (recompute).
State is JSON-serializable so it survives restarts (checkpointed per device).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gpsproc.analysis.geo import Fence, haversine_m

BEHAVIOR_EVENTS = frozenset({"harsh_acceleration", "harsh_braking", "harsh_cornering", "crash", "towing",
                             "gsm_jamming", "power_disconnected", "device_overspeed"})


@dataclass(frozen=True)
class Sample:
    t: datetime
    lat: float
    lon: float
    speed: float = 0.0
    heading: float | None = None
    ignition: bool | None = None
    motion: bool | None = None
    gps_valid: bool = True
    is_outlier: bool = False
    odometer_km: float | None = None
    events: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Valid fixes only drive geometry: distance, stops, overspeed and geofences (spec A4 rules 5 and 7)."""
        return self.gps_valid and not self.is_outlier


@dataclass(frozen=True)
class Config:
    mode: str = "ignition"                 # ignition | motion | speed (from the device model, per vehicle override)
    trip_start_kmh: float = 5.0
    stop_kmh: float = 3.0
    min_stop_s: int = 180
    min_trip_km: float = 0.5
    min_trip_s: int = 120
    idle_s: int = 300
    radius_m: float = 30.0
    overspeed_warning_kmh: float = 80.0
    overspeed_critical_kmh: float = 100.0
    overspeed_min_s: int = 15
    hysteresis_points: int = 2
    hysteresis_s: int = 20
    use_odometer: bool = False             # the device model's capabilities include an odometer


def _ts(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


@dataclass
class Engine:
    config: Config = field(default_factory=Config)
    fences: list[Fence] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)

    # --- helpers --------------------------------------------------------------------------------------

    def _moving(self, s: Sample) -> bool:
        return s.usable and s.speed >= self.config.stop_kmh

    def _point(self, s: Sample) -> dict[str, Any]:
        return {"t": _ts(s.t), "lat": s.lat, "lon": s.lon}

    def _last_fix(self) -> dict[str, Any] | None:
        return self.state.get("last_fix")

    # --- main entry -----------------------------------------------------------------------------------

    def process(self, s: Sample) -> list[dict[str, Any]]:
        """Feed one record (device-time order). Returns finished items: trips, stops, idles, overspeeds,
        geofence events and behaviour events."""
        out: list[dict[str, Any]] = []
        last_t = _dt(self.state.get("last_t"))
        if last_t is not None and s.t <= last_t:
            return out                                   # duplicate or out of order: never double counted
        out += self._behavior(s)
        if s.usable:
            out += self._stop_and_idle(s)
            out += self._overspeed(s)
            out += self._geofences(s)
        out += self._trip(s)
        if s.usable:
            self.state["last_fix"] = {**self._point(s), "speed": s.speed}
        self.state["last_t"] = _ts(s.t)
        self.state["last_ignition"] = s.ignition
        return out

    # --- behaviour ------------------------------------------------------------------------------------

    def _behavior(self, s: Sample) -> list[dict[str, Any]]:
        fix = self._point(s) if s.usable else (self._last_fix() or {})
        return [{"kind": "behavior", "type": e, "at": s.t, "lat": fix.get("lat"), "lon": fix.get("lon"),
                 "speed_kmh": s.speed if s.usable else None} for e in s.events if e in BEHAVIOR_EVENTS]

    # --- stops and idling -----------------------------------------------------------------------------

    def _stop_and_idle(self, s: Sample) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        c = self.config
        stationary = s.speed <= c.stop_kmh
        stop = self.state.get("stop")
        if stationary:
            if stop is None:
                self.state["stop"] = {"start": _ts(s.t), "end": _ts(s.t), "lat_sum": s.lat, "lon_sum": s.lon, "n": 1,
                                      "engine_off": s.ignition is False}
            else:
                stop.update(end=_ts(s.t), lat_sum=stop["lat_sum"] + s.lat, lon_sum=stop["lon_sum"] + s.lon,
                            n=stop["n"] + 1, engine_off=stop["engine_off"] or s.ignition is False)
        elif stop is not None:
            start, end = _dt(stop["start"]), s.t           # the stop lasts until the vehicle moves again
            assert start is not None
            if (end - start).total_seconds() >= c.min_stop_s:
                out.append({"kind": "stop", "start": start, "end": end,
                            "duration_s": int((end - start).total_seconds()),
                            "lat": stop["lat_sum"] / stop["n"], "lon": stop["lon_sum"] / stop["n"],
                            "engine_off": stop["engine_off"]})
            self.state["stop"] = None
        idle = self.state.get("idle")
        idling = stationary and s.ignition is True
        if idling:
            if idle is None:
                self.state["idle"] = {"start": _ts(s.t), "lat": s.lat, "lon": s.lon}
        elif idle is not None:
            start = _dt(idle["start"])
            assert start is not None
            if (s.t - start).total_seconds() >= c.idle_s:
                out.append({"kind": "idle", "start": start, "end": s.t,
                            "duration_s": int((s.t - start).total_seconds()), "lat": idle["lat"], "lon": idle["lon"]})
            self.state["idle"] = None
        return out

    # --- overspeed ------------------------------------------------------------------------------------

    def _overspeed(self, s: Sample) -> list[dict[str, Any]]:
        c = self.config
        ep = self.state.get("overspeed")
        if s.speed > c.overspeed_warning_kmh:
            if ep is None:
                self.state["overspeed"] = {"start": _ts(s.t), "end": _ts(s.t), "max": s.speed, "lat": s.lat,
                                           "lon": s.lon, "end_lat": s.lat, "end_lon": s.lon}
            else:
                ep.update(end=_ts(s.t), max=max(ep["max"], s.speed), end_lat=s.lat, end_lon=s.lon)
            return []
        if ep is None:
            return []
        self.state["overspeed"] = None
        start, end = _dt(ep["start"]), _dt(ep["end"])
        assert start is not None and end is not None
        if (end - start).total_seconds() < c.overspeed_min_s:
            return []                                    # a short spike, not an episode
        return [{"kind": "overspeed", "start": start, "end": end, "duration_s": int((end - start).total_seconds()),
                 "max_speed_kmh": ep["max"], "threshold_kmh": c.overspeed_warning_kmh,
                 "severity": "critical" if ep["max"] >= c.overspeed_critical_kmh else "warning",
                 "lat": ep["lat"], "lon": ep["lon"], "end_lat": ep["end_lat"], "end_lon": ep["end_lon"]}]

    # --- geofences ------------------------------------------------------------------------------------

    def _geofences(self, s: Sample) -> list[dict[str, Any]]:
        c = self.config
        out: list[dict[str, Any]] = []
        members: dict[str, Any] = self.state.setdefault("geofences", {})
        for fence in self.fences:
            key = str(fence.id)
            inside = fence.contains(s.lat, s.lon)
            m = members.get(key)
            if m is None:                                # first sight: establish the state silently
                members[key] = {"inside": inside, "pending": None, "entered_at": _ts(s.t) if inside else None,
                                "dwell_sent": False}
                continue
            if inside == m["inside"]:
                m["pending"] = None
            else:
                p = m["pending"]
                if p is None or p["inside"] != inside:
                    p = m["pending"] = {"inside": inside, "since": _ts(s.t), "lat": s.lat, "lon": s.lon, "count": 0}
                p["count"] += 1
                since = _dt(p["since"])
                assert since is not None
                if p["count"] >= c.hysteresis_points or (s.t - since).total_seconds() >= c.hysteresis_s:
                    m.update(inside=inside, pending=None, dwell_sent=False, entered_at=p["since"] if inside else None)
                    out.append({"kind": "geofence", "event": "enter" if inside else "exit", "geofence_id": fence.id,
                                "at": since, "lat": p["lat"], "lon": p["lon"]})
            if m["inside"] and fence.dwell_s and not m["dwell_sent"] and m["entered_at"]:
                entered = _dt(m["entered_at"])
                assert entered is not None
                if (s.t - entered).total_seconds() >= fence.dwell_s:
                    m["dwell_sent"] = True
                    out.append({"kind": "geofence", "event": "dwell", "geofence_id": fence.id, "at": s.t,
                                "lat": s.lat, "lon": s.lon, "dwell_s": int((s.t - entered).total_seconds())})
        return out

    # --- trips ----------------------------------------------------------------------------------------

    def _starts(self, s: Sample) -> bool:
        c = self.config
        if c.mode == "ignition":
            return s.ignition is True
        if not s.usable:
            return False
        if c.mode == "speed":
            return s.speed > c.trip_start_kmh
        anchor = self.state.get("anchor")           # motion: moved beyond the radius at more than the start speed
        moved = anchor is None or haversine_m(anchor["lat"], anchor["lon"], s.lat, s.lon) > c.radius_m
        return (s.speed > c.trip_start_kmh or bool(s.motion)) and moved

    def _trip(self, s: Sample) -> list[dict[str, Any]]:
        c = self.config
        trip = self.state.get("trip")
        if trip is None:
            if s.usable and self.state.get("anchor") is None:
                self.state["anchor"] = {"lat": s.lat, "lon": s.lon}
            if not self._starts(s):
                if s.usable and s.speed <= c.stop_kmh:
                    self.state["anchor"] = self.state.get("anchor") or {"lat": s.lat, "lon": s.lon}
                return []
            start_fix = self._point(s) if s.usable else (self._last_fix() or {"lat": None, "lon": None})
            self.state["trip"] = trip = {
                "start": _ts(s.t), "start_lat": start_fix["lat"], "start_lon": start_fix["lon"],
                "start_odo": s.odometer_km, "end_odo": s.odometer_km, "distance_m": 0.0, "max_speed": 0.0,
                "driving_s": 0.0, "idle_s": 0.0, "stopped_s": 0.0, "moving_m": 0.0, "points": 0,
                "prev": self._point(s) if s.usable else None, "prev_t": _ts(s.t), "prev_moving": self._moving(s),
                "prev_ignition": s.ignition, "anchor": {"lat": s.lat, "lon": s.lon} if s.usable else None,
                "still_since": None, "still_at": None}
        self._accumulate(trip, s)
        ended_at = self._trip_end(trip, s)
        if ended_at is None:
            return []
        return self._finish(trip, ended_at)

    def _accumulate(self, trip: dict[str, Any], s: Sample) -> None:
        c = self.config
        prev_t = _dt(trip["prev_t"])
        assert prev_t is not None
        dt = (s.t - prev_t).total_seconds()
        if trip["prev_moving"]:
            trip["driving_s"] += dt
        elif trip["prev_ignition"] is True:
            trip["idle_s"] += dt
        else:
            trip["stopped_s"] += dt
        trip["prev_t"], trip["prev_moving"], trip["prev_ignition"] = _ts(s.t), self._moving(s), s.ignition
        trip["points"] += 1
        if s.odometer_km is not None:
            trip["end_odo"] = s.odometer_km
            trip["start_odo"] = trip["start_odo"] if trip["start_odo"] is not None else s.odometer_km
        if not s.usable:
            return
        trip["max_speed"] = max(trip["max_speed"], s.speed)
        prev, anchor = trip["prev"], trip["anchor"]
        if prev is not None:
            d = haversine_m(prev["lat"], prev["lon"], s.lat, s.lon)
            if s.speed >= c.stop_kmh:                    # moving: real travel
                trip["distance_m"] += d
                trip["moving_m"] += d
                trip["anchor"] = {"lat": s.lat, "lon": s.lon}
            elif anchor is not None:                     # standing: only movement beyond the radius counts (drift)
                away = haversine_m(anchor["lat"], anchor["lon"], s.lat, s.lon)
                if away > c.radius_m:
                    trip["distance_m"] += away
                    trip["anchor"] = {"lat": s.lat, "lon": s.lon}
        trip["prev"] = self._point(s)
        trip["last_lat"], trip["last_lon"] = s.lat, s.lon
        if s.speed <= c.stop_kmh:
            if trip["still_since"] is None:
                trip["still_since"], trip["still_at"] = _ts(s.t), {"lat": s.lat, "lon": s.lon}
        else:
            trip["still_since"] = trip["still_at"] = None

    def _trip_end(self, trip: dict[str, Any], s: Sample) -> datetime | None:
        c = self.config
        if c.mode == "ignition":
            return s.t if s.ignition is False else None
        since = _dt(trip["still_since"])
        if since is not None and (s.t - since).total_seconds() >= c.min_stop_s:
            return since                                  # the trip ended when the vehicle came to rest
        return None

    def _finish(self, trip: dict[str, Any], ended_at: datetime) -> list[dict[str, Any]]:
        c = self.config
        self.state["trip"] = None
        end_lat = (trip.get("still_at") or {}).get("lat", trip.get("last_lat", trip["start_lat"]))
        end_lon = (trip.get("still_at") or {}).get("lon", trip.get("last_lon", trip["start_lon"]))
        self.state["anchor"] = {"lat": end_lat, "lon": end_lon} if end_lat is not None else None
        start = _dt(trip["start"])
        assert start is not None
        duration = (ended_at - start).total_seconds()
        odo_ok = c.use_odometer and trip["start_odo"] is not None and trip["end_odo"] is not None \
            and trip["end_odo"] >= trip["start_odo"]
        distance_km = (trip["end_odo"] - trip["start_odo"]) if odo_ok else trip["distance_m"] / 1000
        if distance_km < c.min_trip_km or duration < c.min_trip_s:
            return []                                     # noise: a manoeuvre in a yard, a brief ignition test
        driving = trip["driving_s"]
        return [{"kind": "trip", "start": start, "end": ended_at, "duration_s": int(duration),
                 "start_lat": trip["start_lat"], "start_lon": trip["start_lon"], "end_lat": end_lat, "end_lon": end_lon,
                 "distance_km": round(distance_km, 3), "distance_source": "odometer" if odo_ok else "gps",
                 "driving_s": int(driving), "idle_s": int(trip["idle_s"]), "stopped_s": int(trip["stopped_s"]),
                 "max_speed_kmh": trip["max_speed"],
                 "avg_moving_speed_kmh": round((trip["moving_m"] / 1000) / (driving / 3600), 1) if driving else 0.0,
                 "points": trip["points"]}]

    def open_trip_started(self) -> datetime | None:
        trip = self.state.get("trip")
        return _dt(trip["start"]) if trip else None
