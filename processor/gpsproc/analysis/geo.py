"""Small geodesy helpers for the analysis engine (pure Python, no database)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

EARTH_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(h)))


def point_in_polygon(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    """Ray casting on (lon, lat); adequate for city-scale geofences away from the poles and antimeridian."""
    inside = False
    n = len(ring)
    for i in range(n):
        y1, x1 = ring[i]
        y2, x2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


@dataclass(frozen=True)
class Fence:
    """A geofence as the engine sees it. ``polygon`` is a ring of (lat, lon); circles use center and radius."""

    id: int
    kind: str                                   # "circle" | "polygon"
    center: tuple[float, float] | None = None
    radius_m: float = 0.0
    polygon: tuple[tuple[float, float], ...] = ()
    dwell_s: int | None = None
    bbox: tuple[float, float, float, float] = field(init=False)   # south, west, north, east

    def __post_init__(self) -> None:
        if self.kind == "circle":
            assert self.center is not None
            lat, lon = self.center
            dlat = self.radius_m / 111_320
            dlon = self.radius_m / (111_320 * max(0.01, math.cos(math.radians(lat))))
            box = (lat - dlat, lon - dlon, lat + dlat, lon + dlon)
        else:
            lats = [p[0] for p in self.polygon]
            lons = [p[1] for p in self.polygon]
            box = (min(lats), min(lons), max(lats), max(lons))
        object.__setattr__(self, "bbox", box)

    def maybe_contains(self, lat: float, lon: float) -> bool:
        s, w, n, e = self.bbox
        return s <= lat <= n and w <= lon <= e

    def contains(self, lat: float, lon: float) -> bool:
        if not self.maybe_contains(lat, lon):
            return False
        if self.kind == "circle":
            assert self.center is not None
            return haversine_m(lat, lon, *self.center) <= self.radius_m
        return point_in_polygon(lat, lon, list(self.polygon))
