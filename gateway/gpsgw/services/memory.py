"""In-memory implementations of the gateway ports (tests and local demos)."""
from __future__ import annotations

from gpsgw.normalization.normalized_location import NormalizedLocation
from gpsgw.services.ports import DeviceInfo, LastFix, UnknownAttempt


class MemoryRegistry:
    def __init__(self, devices: list[DeviceInfo] | None = None) -> None:
        self._devices = {(d.identifier, d.protocol): d for d in devices or []}

    def upsert(self, device: DeviceInfo) -> None:
        self._devices[(device.identifier, device.protocol)] = device

    async def lookup(self, identifier: str, protocol: str) -> DeviceInfo | None:
        device = self._devices.get((identifier, protocol))
        return device if device and device.active else None

    async def lookup_token(self, token: str) -> DeviceInfo | None:
        """HTTP trackers authenticate with a per-device token (generic protocol)."""
        device = self.tokens.get(token)
        return device if device and device.active else None

    @property
    def tokens(self) -> dict[str, DeviceInfo]:
        if not hasattr(self, "_tokens"):
            self._tokens: dict[str, DeviceInfo] = {}
        return self._tokens


class MemoryPersistence:
    """Mimics the UNIQUE(device_id, device_time, record_hash) constraint."""

    def __init__(self) -> None:
        self.rows: dict[tuple[int, str], NormalizedLocation] = {}
        self.raw: list[tuple[int, str, str, bytes, str]] = []
        self.fail = False

    async def save(self, device: DeviceInfo, records: list[NormalizedLocation]) -> int:
        if self.fail:
            raise ConnectionError("database unavailable")
        inserted = 0
        for rec in records:
            key = (device.device_id, rec.record_hash)
            if key not in self.rows:
                self.rows[key] = rec
                inserted += 1
        return inserted

    async def last_fix(self, device_id: int) -> LastFix | None:
        recs = [r for (dev, _), r in self.rows.items() if dev == device_id]
        if not recs:
            return None
        valid = [r for r in recs if r.gps_valid and not r.is_outlier and not r.is_late]
        best = max(valid, key=lambda r: r.device_time) if valid else None
        return LastFix(max(r.device_time for r in recs), best.device_time if best else None,
                       best.latitude if best else None, best.longitude if best else None)

    async def record_raw(self, device: DeviceInfo, direction: str, transport: str, payload: bytes,
                         result: str) -> None:
        self.raw.append((device.device_id, direction, transport, payload, result))


class MemoryPublisher:
    def __init__(self) -> None:
        self.published: list[NormalizedLocation] = []

    async def publish(self, device: DeviceInfo, records: list[NormalizedLocation]) -> None:
        self.published.extend(records)


class MemoryFormats:
    """Generic formats by slug (tests and memory mode)."""

    def __init__(self, formats: dict[str, dict[str, object]] | None = None) -> None:
        self.formats = dict(formats or {})

    async def get(self, slug: str) -> dict[str, object] | None:
        return self.formats.get(slug)


class MemoryInbox:
    def __init__(self) -> None:
        self.attempts: list[UnknownAttempt] = []

    async def record(self, attempt: UnknownAttempt) -> None:
        self.attempts.append(attempt)
