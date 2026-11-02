"""Protocol plugin contract shared by every tracker decoder.

Decoders are pure: they turn bytes into ``RawRecord`` objects and build the
byte responses a device expects. They never touch the database, Django or the
network, which keeps them fully unit-testable with byte fixtures (spec A6).
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import entry_points
from typing import Any, ClassVar

ENTRY_POINT_GROUP = "gps_platform.protocols"


class Transport(enum.StrEnum):
    TCP = "tcp"
    UDP = "udp"
    HTTP = "http"


class ProtocolError(Exception):
    """Raised for malformed, truncated, oversized or unsupported input.

    ``code`` is a short machine-readable reason used for metrics and logs.
    Listeners catch only this exception type; anything else is a bug.
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class FrameKind(enum.StrEnum):
    HANDSHAKE = "handshake"
    DATA = "data"
    COMMAND_RESPONSE = "command_response"
    HEARTBEAT = "heartbeat"


@dataclass(frozen=True)
class Frame:
    """One complete protocol message cut from the byte stream."""

    kind: FrameKind
    raw: bytes
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceIdentity:
    identifier: str
    id_type: str = "imei"


@dataclass
class RawRecord:
    """Protocol-native record before IO mapping and normalization."""

    device_time: datetime
    latitude: float
    longitude: float
    altitude: float | None = None
    heading: float | None = None
    satellites: int | None = None
    speed_kmh: float | None = None
    priority: int | None = None
    event_io_id: int | None = None
    gps_valid: bool | None = None  # None = let the validator decide
    io: dict[int, int | bytes] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionContext:
    """Per-connection state owned by the listener and passed to the decoder."""

    transport: Transport
    peer: str
    identity: DeviceIdentity | None = None
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandResponse:
    text: str
    raw: bytes


class BaseProtocolDecoder(ABC):
    name: ClassVar[str]
    transports: ClassVar[frozenset[Transport]]
    supports_commands: ClassVar[bool] = False
    #: Largest frame accepted before the connection is dropped.
    max_frame_size: ClassVar[int] = 64 * 1024

    @abstractmethod
    def can_handle(self, first_bytes: bytes) -> bool:
        """Cheap check used for optional shared-port auto-detection."""

    @abstractmethod
    def extract_frame(self, buffer: bytearray, ctx: SessionContext) -> Frame | None:
        """Cut one complete frame off the front of ``buffer`` (stream transports).

        Returns ``None`` when more bytes are needed. Consumed bytes are removed
        from ``buffer``. Raises ``ProtocolError`` for garbage or oversize data.
        """

    @abstractmethod
    def identify_device(self, frame: Frame) -> DeviceIdentity | None:
        """Return the identity carried by a handshake/login frame, if any."""

    @abstractmethod
    def handshake_response(self, accepted: bool, frame: Frame) -> bytes | None:
        """Bytes to send after the registry accepted or rejected the device."""

    @abstractmethod
    def decode(self, frame: Frame, ctx: SessionContext) -> list[RawRecord]:
        """Decode a validated data frame into raw records."""

    @abstractmethod
    def acknowledge(self, frame: Frame, accepted: int, ctx: SessionContext) -> bytes | None:
        """Bytes confirming ``accepted`` records were durably stored."""

    def encode_command(self, command_text: str) -> bytes:
        raise ProtocolError("commands_unsupported", self.name)

    def decode_command_response(self, frame: Frame) -> CommandResponse | None:
        return None


class ProtocolRegistry:
    """Name -> decoder lookup, filled from built-ins and entry-point plugins."""

    def __init__(self) -> None:
        self._decoders: dict[str, BaseProtocolDecoder] = {}

    def register(self, decoder: BaseProtocolDecoder) -> None:
        if decoder.name in self._decoders:
            raise ValueError(f"protocol already registered: {decoder.name}")
        self._decoders[decoder.name] = decoder

    def get(self, name: str) -> BaseProtocolDecoder:
        try:
            return self._decoders[name]
        except KeyError as exc:
            raise KeyError(f"unknown protocol: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._decoders)

    def describe(self) -> list[dict[str, Any]]:
        """Capabilities synced to the ``device_protocols`` table for the UI."""
        return [
            {
                "name": d.name,
                "transports": sorted(t.value for t in d.transports),
                "supports_commands": d.supports_commands,
            }
            for d in sorted(self._decoders.values(), key=lambda d: d.name)
        ]

    def load_entry_points(self) -> None:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            decoder_cls = ep.load()
            if decoder_cls.name not in self._decoders:
                self.register(decoder_cls())


def default_registry() -> ProtocolRegistry:
    from gpsgw.protocols.generic.decoder import GenericJsonProtocol, GenericTextDecoder
    from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder

    registry = ProtocolRegistry()
    registry.register(TeltonikaDecoder())
    registry.register(GenericTextDecoder())     # configured per listener port from a UI-defined format
    registry.register(GenericJsonProtocol())    # served by the HTTP listener
    registry.load_entry_points()
    return registry
