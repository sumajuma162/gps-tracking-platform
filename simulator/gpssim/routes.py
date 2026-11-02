"""Demo routes through Dar es Salaam. Coordinates are APPROXIMATE waypoints along real roads, good enough for
demos and tests; they are not survey data. Every simulated record is flagged by its tracker (``is_simulated``)."""
from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]  # (latitude, longitude)

ROUTES: dict[str, list[Point]] = {
    # Kariakoo -> Magomeni -> Ubungo -> Kimara -> Mbezi along Morogoro Road
    "morogoro_road": [(-6.8183, 39.2747), (-6.8105, 39.2681), (-6.8013, 39.2523), (-6.7948, 39.2340),
                      (-6.7893, 39.2096), (-6.7862, 39.1657), (-6.7563, 39.1171)],
    # Port gate -> Kurasini -> Chang'ombe -> TAZARA -> Julius Nyerere airport (Terminal 3)
    "port_airport": [(-6.8358, 39.2875), (-6.8505, 39.2833), (-6.8565, 39.2650), (-6.8593, 39.2400),
                     (-6.8735, 39.2026)],
    # City centre -> Kinondoni -> Mwenge -> Tegeta along Bagamoyo / Ali Hassan Mwinyi Road
    "bagamoyo_road": [(-6.8160, 39.2891), (-6.7900, 39.2700), (-6.7690, 39.2290), (-6.6510, 39.1900)],
}


def distance_km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


@dataclass(frozen=True)
class Route:
    """A polyline that can be walked by distance; stops are at its waypoints."""

    name: str
    points: list[Point]

    @classmethod
    def named(cls, name: str, reverse: bool = False) -> Route:
        pts = ROUTES[name]
        return cls(name, list(reversed(pts)) if reverse else list(pts))

    @property
    def legs_km(self) -> list[float]:
        return [distance_km(a, b) for a, b in zip(self.points, self.points[1:], strict=False)]

    @property
    def length_km(self) -> float:
        return sum(self.legs_km)

    def locate(self, km: float) -> tuple[Point, float, int]:
        """Point and heading ``km`` along the route, and the index of the leg it is on."""
        km = max(0.0, min(km, self.length_km))
        for i, leg in enumerate(self.legs_km):
            if km <= leg or i == len(self.legs_km) - 1:
                a, b = self.points[i], self.points[i + 1]
                f = 0.0 if leg == 0 else min(1.0, km / leg)
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), bearing_deg(a, b), i
            km -= leg
        raise AssertionError("unreachable")

    def stop_offsets_km(self) -> list[float]:
        """Distance of each intermediate waypoint (where the vehicle stops) from the start."""
        out, total = [], 0.0
        for leg in self.legs_km[:-1]:
            total += leg
            out.append(total)
        return out
