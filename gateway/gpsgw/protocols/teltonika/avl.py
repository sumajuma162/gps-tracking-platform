"""Teltonika AVL data array parsing for Codec 8 (0x08) and Codec 8 Extended (0x8E).

The AVL data array is: codec id, record count, records, record count again.
It is shared by the TCP packet (inside preamble/length/CRC) and the UDP packet.
Layouts follow the official "Teltonika Data Sending Protocols" documentation.
"""
from __future__ import annotations

from datetime import UTC, datetime

from gpsgw.protocols.base import ProtocolError, RawRecord
from gpsgw.protocols.reader import ByteReader

CODEC_8 = 0x08
CODEC_8E = 0x8E
CODEC_12 = 0x0C
CODEC_16 = 0x10
DATA_CODECS = frozenset({CODEC_8, CODEC_8E})

#: Absolute cap on records per packet; Teltonika packets carry far fewer.
MAX_RECORDS = 255


def parse_avl_array(data: bytes) -> tuple[int, list[RawRecord]]:
    """Parse a complete AVL data array. Returns ``(codec_id, records)``."""
    r = ByteReader(data)
    codec_id = r.u8()
    if codec_id not in DATA_CODECS:
        raise ProtocolError("unsupported_codec", f"0x{codec_id:02X}")
    count1 = r.u8()
    if count1 == 0:
        raise ProtocolError("empty_packet")
    extended = codec_id == CODEC_8E
    records = [_parse_record(r, extended) for _ in range(count1)]
    count2 = r.u8()
    if count1 != count2:
        raise ProtocolError("count_mismatch", f"{count1} != {count2}")
    if r.remaining:
        raise ProtocolError("trailing_bytes", str(r.remaining))
    return codec_id, records


def _parse_record(r: ByteReader, extended: bool) -> RawRecord:
    ts_ms = r.u64()
    try:
        device_time = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ProtocolError("bad_timestamp", str(ts_ms)) from exc
    priority = r.u8()
    longitude = r.i32() / 10_000_000
    latitude = r.i32() / 10_000_000
    altitude = r.i16()
    angle = r.u16()
    satellites = r.u8()
    speed = r.u16()

    id_reader = r.u16 if extended else r.u8
    count_reader = r.u16 if extended else r.u8

    event_io_id = id_reader()
    total = count_reader()
    io: dict[int, int | bytes] = {}
    seen = 0
    for width in (1, 2, 4, 8):
        n = count_reader()
        for _ in range(n):
            io_id = id_reader()
            io[io_id] = int.from_bytes(r.take(width), "big")
        seen += n
    if extended:
        nx = r.u16()
        for _ in range(nx):
            io_id = r.u16()
            length = r.u16()
            io[io_id] = r.take(length)
        seen += nx
    if seen != total:
        raise ProtocolError("io_count_mismatch", f"declared {total}, found {seen}")

    return RawRecord(
        device_time=device_time,
        latitude=latitude,
        longitude=longitude,
        altitude=float(altitude),
        heading=float(angle),
        satellites=satellites,
        speed_kmh=float(speed),
        priority=priority,
        event_io_id=event_io_id or None,
        io=io,
        extra={"codec": "8E" if extended else "8"},
    )
