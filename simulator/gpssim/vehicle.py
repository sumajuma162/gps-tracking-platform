"""A simulated vehicle: drives a route with realistic speed changes, stops and engine state."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from gpsgw.protocols.teltonika.encoder import AvlRecordSpec

from gpssim.routes import Route


@dataclass
class Behaviour:
    cruise_kmh: float = 50.0
    top_kmh: float = 70.0
    stop_minutes: float = 2.0
    idle_minutes: float = 0.5          # engine running at each stop before switching off
    #: Leg index driven at ``overspeed_kmh`` (None = never).
    overspeed_leg: int | None = None
    overspeed_kmh: float = 112.0
    #: Extra engine-on idling at the first stop (long_idle scenario).
    long_idle_minutes: float = 0.0
    jump_at_step: int | None = None     # one teleported fix (gps_jump)
    invalid_every: int = 0              # every Nth fix has no satellites (invalid_fix)
    #: At the end of the route: park (engine off) for this long, then drive back. 0 = stay parked.
    park_minutes: float = 20.0


@dataclass
class VehicleSim:
    route: Route
    start: datetime
    interval_s: float = 10.0
    behaviour: Behaviour = field(default_factory=Behaviour)
    seed: int = 1
    odometer_m: int = 152_000_000

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.time = self.start
        self.km = 0.0
        self.speed = 0.0
        self.step = 0
        self.ignition = True
        self.stop_left_s = 0.0
        self.idle_left_s = 0.0
        self.stops = self.route.stop_offsets_km()
        self.next_stop = 0
        self.done = False
        self.long_idle_used = False

    def _target_speed(self, leg: int) -> float:
        if self.behaviour.overspeed_leg == leg:
            return self.behaviour.overspeed_kmh
        return min(self.behaviour.top_kmh, self.behaviour.cruise_kmh + self.rng.uniform(-8, 12))

    def tick(self) -> AvlRecordSpec:
        """Advance one reporting interval and return the record the tracker would send."""
        dt = self.interval_s
        self.time += timedelta(seconds=dt)
        self.step += 1
        if self.stop_left_s > 0:  # stopped at a waypoint: idle with the engine on, then switch it off
            self.speed = 0.0
            self.stop_left_s -= dt
            self.idle_left_s -= dt
            self.ignition = self.idle_left_s > 0
            if self.stop_left_s <= 0:
                self.ignition = True
        else:
            point_km = self.km
            _, _, leg = self.route.locate(point_km)
            target = self._target_speed(leg)
            dist_to_stop = (self.stops[self.next_stop] - self.km) if self.next_stop < len(self.stops) \
                else self.route.length_km - self.km
            if dist_to_stop < 0.25:      # brake for the stop
                target = min(target, max(8.0, dist_to_stop * 160))
            accel = 18.0 * dt / 10      # km/h per interval
            self.speed = max(0.0, min(target, self.speed + accel)) if self.speed < target \
                else max(target, self.speed - accel * 1.5)
            self.km += self.speed * dt / 3600
            self.odometer_m += int(self.speed * dt / 3.6)
            if self.next_stop < len(self.stops) and self.km >= self.stops[self.next_stop]:
                self.km = self.stops[self.next_stop]
                self.next_stop += 1
                extra = 0.0
                if self.behaviour.long_idle_minutes and not self.long_idle_used:
                    extra, self.long_idle_used = self.behaviour.long_idle_minutes * 60, True
                self.stop_left_s = self.behaviour.stop_minutes * 60 + extra
                self.idle_left_s = self.behaviour.idle_minutes * 60 + extra
                self.speed = 0.0
            elif self.km >= self.route.length_km:
                self.speed = 0.0
                if self.behaviour.park_minutes <= 0:
                    self.done = True
                    self.ignition = False
                else:   # park at the destination, then drive the route back (a realistic working day)
                    end = self.route.points[-1]
                    self.route = Route(self.route.name, list(reversed(self.route.points)))
                    self.stops = self.route.stop_offsets_km()
                    self.km, self.next_stop = 0.0, 0
                    assert self.route.points[0] == end
                    self.stop_left_s = self.behaviour.park_minutes * 60
                    self.idle_left_s = 60.0
        (lat, lon), heading, _ = self.route.locate(self.km)
        sats = self.rng.randint(9, 14)
        if self.behaviour.jump_at_step == self.step:
            lat += 0.45   # ~50 km north in one interval: physically impossible
        if self.behaviour.invalid_every and self.step % self.behaviour.invalid_every == 0:
            lat, lon, sats = 0.0, 0.0, 0
        moving = self.speed > 2
        volts = 27_600 + self.rng.randint(-150, 150) if self.ignition else 25_100 + self.rng.randint(-80, 80)
        return AvlRecordSpec(
            time=self.time, latitude=lat, longitude=lon, altitude=self.rng.randint(5, 60),
            heading=int(heading) % 360, satellites=sats, speed_kmh=int(round(self.speed)),
            event_io_id=239 if self.step == 1 else 0,
            io={239: (1, int(self.ignition)), 240: (1, int(moving)), 21: (1, self.rng.randint(3, 5)),
                66: (2, volts), 67: (2, 4_050 + self.rng.randint(-20, 20)), 16: (4, self.odometer_m)},
        )
