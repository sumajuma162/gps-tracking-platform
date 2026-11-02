"""Record validation rules from spec A4 (coordinates, time sanity, GPS validity)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from gpsgw.protocols.base import RawRecord


class InvalidRecord(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class ValidationConfig:
    max_future: timedelta = timedelta(minutes=10)
    min_time: datetime = datetime(2015, 1, 1, tzinfo=UTC)


def check_record(raw: RawRecord, server_time: datetime, cfg: ValidationConfig) -> None:
    """Raise ``InvalidRecord`` for data that must be rejected outright."""
    if not -90 <= raw.latitude <= 90:
        raise InvalidRecord("bad_latitude", str(raw.latitude))
    if not -180 <= raw.longitude <= 180:
        raise InvalidRecord("bad_longitude", str(raw.longitude))
    if raw.device_time > server_time + cfg.max_future:
        raise InvalidRecord("future_timestamp", raw.device_time.isoformat())
    if raw.device_time < cfg.min_time:
        raise InvalidRecord("ancient_timestamp", raw.device_time.isoformat())
    if raw.speed_kmh is not None and raw.speed_kmh < 0:
        raise InvalidRecord("negative_speed", str(raw.speed_kmh))


def gps_fix_is_valid(raw: RawRecord) -> bool:
    """A record without a usable fix keeps IO data but is excluded from routes."""
    if raw.gps_valid is not None:
        return raw.gps_valid and not (raw.latitude == 0 and raw.longitude == 0)
    if raw.latitude == 0 and raw.longitude == 0:
        return False
    return raw.satellites is None or raw.satellites > 0
