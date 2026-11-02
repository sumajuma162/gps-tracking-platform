"""Vehicle status rules (spec A9 device_state.status, A10 offline / no-GPS detection)."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

MOVING = "ONLINE_MOVING"
IDLE = "ONLINE_IDLE"
STOPPED = "ONLINE_STOPPED"
OFFLINE = "OFFLINE"
NO_GPS = "NO_GPS"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Thresholds:
    moving_kmh: float = 5.0
    offline_after: timedelta = timedelta(minutes=5)
    no_gps_after: timedelta = timedelta(minutes=10)


def status_from_record(*, speed_kmh: float | None, ignition: bool | None, motion: bool | None, gps_valid: bool,
                       last_valid_fix: datetime | None, now: datetime, t: Thresholds) -> str:
    """Status implied by a freshly received record."""
    if not gps_valid and (last_valid_fix is None or now - last_valid_fix > t.no_gps_after):
        return NO_GPS
    speed = speed_kmh or 0.0
    if speed >= t.moving_kmh:
        return MOVING
    if ignition is True:
        return IDLE
    if ignition is False:
        return STOPPED
    return MOVING if motion else STOPPED   # trackers without an ignition input


def status_from_silence(current: str, *, last_packet_at: datetime | None, last_valid_fix: datetime | None,
                        now: datetime, t: Thresholds) -> str:
    """Status after time passes with no new records (periodic sweep)."""
    if last_packet_at is None:
        return UNKNOWN
    if now - last_packet_at > t.offline_after:
        return OFFLINE
    if current != NO_GPS and last_valid_fix is not None and now - last_valid_fix > t.no_gps_after:
        return NO_GPS
    return current


class SeenRecords:
    """Remembers recent record hashes per device: the stream may carry a resend twice (at-least-once)."""

    def __init__(self, per_device: int = 512) -> None:
        self.per_device = per_device
        self._seen: dict[int, OrderedDict[str, None]] = {}

    def first_time(self, device_id: int, record_hash: str) -> bool:
        seen = self._seen.setdefault(device_id, OrderedDict())
        if record_hash in seen:
            return False
        seen[record_hash] = None
        if len(seen) > self.per_device:
            seen.popitem(last=False)
        return True
