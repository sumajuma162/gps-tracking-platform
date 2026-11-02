"""Generic protocols (spec A7): delimited text over TCP/UDP, and JSON over HTTP.

Both are configured by a ``GenericFormat`` defined in the UI. A text listener is bound to one format (set on its
listener port); the HTTP listener serves every JSON format at ``/<format slug>`` (``gpsgw.listeners.http``).
"""
from __future__ import annotations

from typing import Any, ClassVar

from gpsgw.protocols.base import (
    BaseProtocolDecoder,
    DeviceIdentity,
    Frame,
    FrameKind,
    ProtocolError,
    RawRecord,
    SessionContext,
    Transport,
)
from gpsgw.protocols.generic.format import GenericFormat, parse_record, split_line


class GenericTextDecoder(BaseProtocolDecoder):
    """One record per line, ``separator``-delimited, ``terminator``-ended. Every line carries the identifier."""

    name: ClassVar[str] = "generic_text"
    transports: ClassVar[frozenset[Transport]] = frozenset({Transport.TCP, Transport.UDP})
    max_frame_size: ClassVar[int] = 65536

    def __init__(self, fmt: GenericFormat | None = None) -> None:
        self.format = fmt

    def configure(self, options: dict[str, Any]) -> GenericTextDecoder:
        """A decoder bound to the listener port's format (``options["format"]``)."""
        definition = options.get("format")
        if not definition:
            raise ValueError("a generic_text listener needs a format")
        fmt = GenericFormat.model_validate(definition)
        if fmt.kind != "delimited":
            raise ValueError("generic_text listeners need a delimited format")
        return GenericTextDecoder(fmt)

    def _fmt(self) -> GenericFormat:
        if self.format is None:
            raise ProtocolError("not_configured")
        return self.format

    def can_handle(self, first_bytes: bytes) -> bool:
        return bool(first_bytes) and all(32 <= b < 127 or b in (9, 10, 13) for b in first_bytes[:32])

    def extract_frame(self, buffer: bytearray, ctx: SessionContext) -> Frame | None:
        fmt = self._fmt()
        term = fmt.terminator.encode()
        end = buffer.find(term)
        if end < 0:
            if len(buffer) > fmt.max_message_bytes:
                raise ProtocolError("oversized", str(len(buffer)))
            return None
        line = bytes(buffer[:end])
        if not line.strip():
            del buffer[:end + len(term)]
            return Frame(FrameKind.HEARTBEAT, line)
        columns = split_line(fmt, line)
        identifier, raw = parse_record(fmt, columns, ctx.state.get("now") or _now())
        if ctx.identity is None:      # the first line identifies the device; it is decoded right after
            return Frame(FrameKind.HANDSHAKE, line, {"identifier": identifier})
        if identifier != ctx.identity.identifier:
            raise ProtocolError("identifier_changed")
        del buffer[:end + len(term)]
        return Frame(FrameKind.DATA, line, {"record": raw})

    def identify_device(self, frame: Frame) -> DeviceIdentity | None:
        return DeviceIdentity(str(frame.meta["identifier"]), "custom")

    def handshake_response(self, accepted: bool, frame: Frame) -> bytes | None:
        return None

    def decode(self, frame: Frame, ctx: SessionContext) -> list[RawRecord]:
        return [frame.meta["record"]]

    def acknowledge(self, frame: Frame, accepted: int, ctx: SessionContext) -> bytes | None:
        fmt = self._fmt()
        return (fmt.ack + fmt.terminator).encode() if fmt.ack else None

    def parse_datagram(self, data: bytes) -> tuple[DeviceIdentity, Frame]:
        """UDP: one datagram, one line."""
        fmt = self._fmt()
        line = data.rstrip(fmt.terminator.encode()).rstrip(b"\r\n")
        identifier, raw = parse_record(fmt, split_line(fmt, line), _now())
        return DeviceIdentity(identifier, "custom"), Frame(FrameKind.DATA, line, {"record": raw})


class GenericJsonProtocol(BaseProtocolDecoder):
    """Registers ``generic_json`` (HTTP) with the protocol registry; requests are handled by the HTTP listener."""

    name: ClassVar[str] = "generic_json"
    transports: ClassVar[frozenset[Transport]] = frozenset({Transport.HTTP})

    def can_handle(self, first_bytes: bytes) -> bool:
        return first_bytes[:4] in (b"POST", b"GET ")

    def extract_frame(self, buffer: bytearray, ctx: SessionContext) -> Frame | None:
        raise ProtocolError("http_only")

    def identify_device(self, frame: Frame) -> DeviceIdentity | None:
        return None

    def handshake_response(self, accepted: bool, frame: Frame) -> bytes | None:
        return None

    def decode(self, frame: Frame, ctx: SessionContext) -> list[RawRecord]:
        return []

    def acknowledge(self, frame: Frame, accepted: int, ctx: SessionContext) -> bytes | None:
        return None


def _now() -> Any:
    from datetime import UTC, datetime
    return datetime.now(UTC)
