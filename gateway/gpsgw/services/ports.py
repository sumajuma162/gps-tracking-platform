"""Interfaces between the gateway core and its infrastructure.

The listeners depend only on these protocols. Production implementations
(asyncpg persistence, Redis registry/publisher) plug in without touching
listener or decoder code; in-memory implementations back the tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from gpsgw.normalization.io_mapper import IoMappingProfile
from gpsgw.normalization.normalized_location import NormalizedLocation


@dataclass(frozen=True)
class DeviceInfo:
    device_id: int
    identifier: str
    protocol: str
    organization_id: int
    vehicle_id: int | None
    profile: IoMappingProfile | None
    active: bool = True
    log_raw_packets: bool = False
    #: Minutes to subtract from device clocks that report local time instead of UTC (GT06 clones).
    time_offset_minutes: int = 0
    #: Generic protocols: the slug of the device model's format (HTTP requests must use that format).
    format_slug: str | None = None


@dataclass(frozen=True)
class UnknownAttempt:
    identifier: str
    protocol: str
    transport: str
    port: int
    peer: str
    seen_at: datetime
    sample_hex: str


class DeviceRegistry(Protocol):
    async def lookup(self, identifier: str, protocol: str) -> DeviceInfo | None: ...


@dataclass(frozen=True)
class LastFix:
    """What the gateway last stored for a device; seeds late/outlier detection after a restart."""

    newest_time: datetime | None
    valid_time: datetime | None
    latitude: float | None
    longitude: float | None


class Persistence(Protocol):
    async def save(self, device: DeviceInfo, records: list[NormalizedLocation]) -> int:
        """Durably store records (idempotent on record_hash). Return rows inserted."""

    async def last_fix(self, device_id: int) -> LastFix | None:
        """Latest stored fix for a device, or None if the device never reported."""

    async def record_raw(self, device: DeviceInfo, direction: str, transport: str, payload: bytes,
                         result: str) -> None:
        """Store a raw packet for troubleshooting (only called when raw logging is enabled)."""


class ConnectionLog(Protocol):
    async def opened(self, device: DeviceInfo, transport: str, port: int, peer: str) -> int | None: ...

    async def closed(self, connection_id: int, reason: str, packets: int, records: int) -> None: ...


class Publisher(Protocol):
    async def publish(self, device: DeviceInfo, records: list[NormalizedLocation]) -> None: ...


class UnknownDeviceInbox(Protocol):
    async def record(self, attempt: UnknownAttempt) -> None: ...
