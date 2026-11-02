"""GT06 decoder plugin (Concox GT06 and compatible clones), from the published GT06 protocol document (spec A6).

Frame: ``78 78`` + 1-byte length (or ``79 79`` + 2-byte length), protocol number, content, 2-byte serial number,
CRC-ITU over length..serial, then ``0D 0A``. The length counts protocol number..CRC.

Handled packets:

- ``0x01`` login: 8-byte BCD IMEI. The login ACK echoes the serial, and is sent only for registered devices.
- ``0x12`` / ``0x22`` location: 6-byte date-time, GPS info length + satellites, latitude and longitude
  (unsigned, /1,800,000 degrees), 1-byte speed (km/h), course/status word (bit 12 GPS positioned, bit 10 north
  latitude, bit 11 west longitude, bits 0-9 course), cell info; ``0x22`` adds ACC (ignition), upload mode,
  real-time/re-upload flag and, on some firmware, a 4-byte mileage. No ACK (the protocol defines none).
- ``0x13`` heartbeat (status: terminal info, voltage level, GSM signal) and ``0x16`` / ``0x26`` alarm (a location
  plus status and an alarm code): ACKed with the protocol number and serial. A heartbeat carries no position, so
  it is decoded as a data frame with no records: the listener's normal path then stores nothing and sends the ACK.

IO values for the device model's mapping profile (``gt06-generic``; ids are this plugin's own):

=====  ====================================================
IO id  Meaning
=====  ====================================================
1      ACC / ignition (0/1)
2      terminal information byte (raw)
3      voltage level 0..6
4      GSM signal level 0..4
5      alarm code (0x01 SOS, 0x02 power cut, 0x03 shock, 0x04 fence in, 0x05 fence out, 0x06 overspeed, ...)
6      mileage in metres (0x22 with the optional field)
7      re-upload flag (1 = buffered data sent late)
8      charging (terminal info bit 2)
9      GPS tracking on (terminal info bit 6)
10     MCC, 11 MNC, 12 LAC, 13 cell id
=====  ====================================================

Some clones send local time instead of UTC; the device model's ``time_offset_minutes`` corrects that in the
pipeline. Decoders stay pure: no database or network access.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

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
from gpsgw.protocols.gt06.crc_itu import crc_itu

LOGIN, LOCATION, HEARTBEAT, ALARM, LOCATION_2, ALARM_2 = 0x01, 0x12, 0x13, 0x16, 0x22, 0x26
LOCATION_TYPES = {LOCATION, LOCATION_2}
ALARM_TYPES = {ALARM, ALARM_2}
ACKED = {LOGIN, HEARTBEAT, *ALARM_TYPES}
SHORT, LONG, STOP = b"\x78\x78", b"\x79\x79", b"\x0d\x0a"
COORD_DIVISOR = 1_800_000.0


def encode_packet(protocol: int, content: bytes, serial: int, long: bool = False) -> bytes:
    """Build a complete frame (used for ACKs, tests and the simulator)."""
    body = bytes([protocol]) + content + serial.to_bytes(2, "big")
    length = len(body) + 2
    head = LONG + length.to_bytes(2, "big") if long else SHORT + bytes([length])
    crc = crc_itu(head[2:] + body)
    return head + body + crc.to_bytes(2, "big") + STOP


def _bcd_imei(data: bytes) -> str:
    digits = data.hex()
    return digits.lstrip("0") if len(digits.lstrip("0")) >= 15 else digits[-15:]


def _datetime(b: bytes) -> datetime:
    try:
        return datetime(2000 + b[0], b[1], b[2], b[3], b[4], b[5], tzinfo=UTC)
    except ValueError as exc:
        raise ProtocolError("bad_datetime", b.hex()) from exc


class Gt06Decoder(BaseProtocolDecoder):
    name: ClassVar[str] = "gt06"
    transports: ClassVar[frozenset[Transport]] = frozenset({Transport.TCP})
    supports_commands: ClassVar[bool] = False
    #: Bytes the listener may buffer: a backlog burst holds many small packets (one GT06 packet is <= 1 KB).
    max_frame_size: ClassVar[int] = 16 * 1024
    max_packet: ClassVar[int] = 1024

    def can_handle(self, first_bytes: bytes) -> bool:
        return first_bytes[:2] in (SHORT, LONG)

    # ---- framing -----------------------------------------------------------------------------------------
    def extract_frame(self, buffer: bytearray, ctx: SessionContext) -> Frame | None:
        if len(buffer) < 2:
            if buffer and buffer[0] not in (0x78, 0x79):
                raise ProtocolError("bad_start")
            return None
        start = bytes(buffer[:2])
        if start not in (SHORT, LONG):
            raise ProtocolError("bad_start", start.hex())
        header = 3 if start == SHORT else 4
        if len(buffer) < header:
            return None
        length = buffer[2] if start == SHORT else int.from_bytes(buffer[2:4], "big")
        if length < 5:
            raise ProtocolError("bad_length", str(length))
        total = header + length + 2
        if total > self.max_packet:
            raise ProtocolError("oversized", str(total))
        if len(buffer) < total:
            return None
        raw = bytes(buffer[:total])
        del buffer[:total]
        self._validate(raw, header)
        protocol = raw[header]
        serial = int.from_bytes(raw[total - 6:total - 4], "big")
        meta = {"protocol": protocol, "serial": serial, "content": raw[header + 1:total - 6], "long": start == LONG}
        if protocol == LOGIN:
            if ctx.identity is not None:
                raise ProtocolError("repeated_login")
            content = meta["content"]
            if len(content) < 8:  # type: ignore[arg-type]
                raise ProtocolError("bad_login", raw.hex())
            meta["imei"] = _bcd_imei(content[:8])  # type: ignore[index]
            return Frame(FrameKind.HANDSHAKE, raw, meta)
        if ctx.identity is None:
            raise ProtocolError("not_logged_in", f"0x{protocol:02x}")
        if protocol in LOCATION_TYPES | ALARM_TYPES | {HEARTBEAT}:
            return Frame(FrameKind.DATA, raw, meta)
        # Other GT06 packets (string info, GPS address requests, ...) are not needed for tracking: skipped
        # without breaking the session.
        return Frame(FrameKind.HEARTBEAT, raw, meta)

    @staticmethod
    def _validate(raw: bytes, header: int) -> None:
        if raw[-2:] != STOP:
            raise ProtocolError("bad_stop")
        expected = int.from_bytes(raw[-4:-2], "big")
        if crc_itu(raw[2:-4]) != expected:
            raise ProtocolError("crc_mismatch")

    # ---- session -----------------------------------------------------------------------------------------
    def identify_device(self, frame: Frame) -> DeviceIdentity | None:
        imei = frame.meta.get("imei")
        return DeviceIdentity(str(imei)) if imei else None

    def handshake_response(self, accepted: bool, frame: Frame) -> bytes | None:
        # Unregistered devices get no ACK, so they keep retrying and the inbox sees them (spec A7).
        return encode_packet(LOGIN, b"", int(frame.meta["serial"])) if accepted else None

    def acknowledge(self, frame: Frame, accepted: int, ctx: SessionContext) -> bytes | None:
        protocol = int(frame.meta["protocol"])
        if protocol in ACKED and protocol != LOGIN:
            return encode_packet(protocol, b"", int(frame.meta["serial"]), long=bool(frame.meta["long"]))
        return None

    # ---- decoding ----------------------------------------------------------------------------------------
    def decode(self, frame: Frame, ctx: SessionContext) -> list[RawRecord]:
        protocol = int(frame.meta["protocol"])
        content: bytes = frame.meta["content"]
        if protocol == HEARTBEAT:
            if len(content) < 3:
                raise ProtocolError("truncated", "heartbeat")
            ctx.state["gt06_status"] = self._status(content[0], content[1], content[2])
            return []
        if len(content) < 18:
            raise ProtocolError("truncated", f"0x{protocol:02x}")
        record = self._position(content)
        rest = content[18:]
        io = record.io
        if protocol in LOCATION_TYPES:
            if len(rest) >= 8:
                io.update({10: int.from_bytes(rest[0:2], "big"), 11: rest[2], 12: int.from_bytes(rest[3:5], "big"),
                           13: int.from_bytes(rest[5:8], "big")})
            if protocol == LOCATION_2 and len(rest) >= 11:
                io[1] = 1 if rest[8] else 0
                io[7] = 1 if rest[10] == 1 else 0
                if len(rest) >= 15:
                    io[6] = int.from_bytes(rest[11:15], "big")
        else:  # alarm: an LBS block whose first byte is its length (including itself), then status + alarm code
            lbs_len = rest[0] if rest else 0
            if lbs_len >= 9:
                lbs = rest[1:9]
                io.update({10: int.from_bytes(lbs[0:2], "big"), 11: lbs[2], 12: int.from_bytes(lbs[3:5], "big"),
                           13: int.from_bytes(lbs[5:8], "big")})
                status = rest[lbs_len:]
            else:
                status = rest[1:]
            if len(status) < 4:
                raise ProtocolError("truncated", "alarm status")
            io.update(self._status(status[0], status[1], status[2]))
            io[5] = status[3]
            record.event_io_id = 5
        if 1 not in io and "gt06_status" in ctx.state:     # 0x12 has no ACC; use the last heartbeat
            io[1] = int(ctx.state["gt06_status"][1])
        return [record]

    @staticmethod
    def _status(info: int, voltage: int, gsm: int) -> dict[int, int]:
        return {1: (info >> 1) & 1, 2: info, 3: voltage, 4: gsm, 8: (info >> 2) & 1, 9: (info >> 6) & 1}

    @staticmethod
    def _position(c: bytes) -> RawRecord:
        when = _datetime(c[0:6])
        satellites = c[6] & 0x0F
        lat = int.from_bytes(c[7:11], "big") / COORD_DIVISOR
        lon = int.from_bytes(c[11:15], "big") / COORD_DIVISOR
        speed = float(c[15])
        flags = int.from_bytes(c[16:18], "big")
        if not flags & (1 << 10):
            lat = -lat
        if flags & (1 << 11):
            lon = -lon
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ProtocolError("bad_coordinates")
        return RawRecord(device_time=when, latitude=round(lat, 7), longitude=round(lon, 7), speed_kmh=speed,
                         heading=float(flags & 0x03FF), satellites=satellites, gps_valid=bool(flags & (1 << 12)))
