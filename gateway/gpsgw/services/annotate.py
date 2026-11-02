"""Late-record and GPS-jump detection per device (spec A4 rules 4 and 7).

State advances only when ``commit`` is called after the database write succeeded, so a failed write
followed by the device's resend never marks the resent records as late.
"""
from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from gpsgw.normalization.normalized_location import NormalizedLocation
from gpsgw.services.ports import LastFix

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class DeviceTrack:
    newest_time: datetime | None = None
    valid_time: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None


class PositionAnnotator:
    def __init__(self, loader: Callable[[int], Awaitable[LastFix | None]], max_speed_kmh: float = 300.0,
                 min_jump_km: float = 0.5) -> None:
        self._loader = loader
        self._tracks: dict[int, DeviceTrack] = {}
        self.max_speed_kmh = max_speed_kmh
        #: Small jumps between near-simultaneous points are noise, not teleports.
        self.min_jump_km = min_jump_km

    async def _track(self, device_id: int) -> DeviceTrack:
        if device_id not in self._tracks:
            fix = await self._loader(device_id)
            self._tracks[device_id] = DeviceTrack(*(fix.newest_time, fix.valid_time, fix.latitude, fix.longitude)) \
                if fix else DeviceTrack()
        return self._tracks[device_id]

    def _is_jump(self, track: DeviceTrack, rec: NormalizedLocation) -> bool:
        if rec.raw_metadata.get("manual_position"):
            return False
        if track.valid_time is None or track.latitude is None or track.longitude is None:
            return False
        km = haversine_km(track.latitude, track.longitude, rec.latitude, rec.longitude)
        if km < self.min_jump_km:
            return False
        hours = (rec.device_time - track.valid_time).total_seconds() / 3600
        return hours <= 0 or km / hours > self.max_speed_kmh

    async def annotate(self, device_id: int, records: list[NormalizedLocation]
                       ) -> tuple[list[NormalizedLocation], DeviceTrack]:
        """Return flagged records (oldest first) and the track to ``commit`` once they are stored."""
        track = await self._track(device_id)
        out: list[NormalizedLocation] = []
        for rec in sorted(records, key=lambda r: r.device_time):
            late = track.newest_time is not None and rec.device_time < track.newest_time
            outlier = rec.gps_valid and not late and self._is_jump(track, rec)
            if not late:
                track = DeviceTrack(rec.device_time, track.valid_time, track.latitude, track.longitude)
            if rec.gps_valid and not late and not outlier:
                track = DeviceTrack(track.newest_time, rec.device_time, rec.latitude, rec.longitude)
            out.append(rec.model_copy(update={"is_late": late, "is_outlier": outlier}) if late or outlier else rec)
        return out, track

    def commit(self, device_id: int, track: DeviceTrack) -> None:
        self._tracks[device_id] = track

    def forget(self, device_id: int) -> None:
        self._tracks.pop(device_id, None)
