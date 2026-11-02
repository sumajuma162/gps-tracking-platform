"""Encoder for Teltonika packets.

Used by the simulator (which must speak the real protocol over the network,
spec A16) and by tests for round-trip checks. Not used on the ingest path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from gpsgw.protocols.teltonika.avl import CODEC_8, CODEC_8E
from gpsgw.protocols.teltonika.crc import crc16_ibm


@dataclass
class AvlRecordSpec:
    time: datetime
    latitude: float
    longitude: float
    altitude: int = 0
    heading: int = 0
    satellites: int = 0
    speed_kmh: int = 0
    priority: int = 0
    event_io_id: int = 0
    io: dict[int, tuple[int, int]] = field(default_factory=dict)  # id -> (width, value)


def encode_imei(imei: str) -> bytes:
    return len(imei).to_bytes(2, "big") + imei.encode("ascii")


def encode_avl_array(records: list[AvlRecordSpec], codec: int = CODEC_8E) -> bytes:
    if codec not in (CODEC_8, CODEC_8E):
        raise ValueError("codec must be 8 or 8E")
    ext = codec == CODEC_8E
    idw = 2 if ext else 1
    out = bytearray([codec, len(records)])
    for rec in records:
        out += int(rec.time.timestamp() * 1000).to_bytes(8, "big")
        out.append(rec.priority)
        out += round(rec.longitude * 10_000_000).to_bytes(4, "big", signed=True)
        out += round(rec.latitude * 10_000_000).to_bytes(4, "big", signed=True)
        out += rec.altitude.to_bytes(2, "big", signed=True)
        out += rec.heading.to_bytes(2, "big")
        out.append(rec.satellites)
        out += rec.speed_kmh.to_bytes(2, "big")
        out += rec.event_io_id.to_bytes(idw, "big")
        out += len(rec.io).to_bytes(idw, "big")
        for width in (1, 2, 4, 8):
            group = [(i, v) for i, (w, v) in rec.io.items() if w == width]
            out += len(group).to_bytes(idw, "big")
            for io_id, value in group:
                out += io_id.to_bytes(idw, "big") + value.to_bytes(width, "big")
        if ext:
            out += (0).to_bytes(2, "big")  # NX section empty
    out.append(len(records))
    return bytes(out)


def encode_tcp_packet(records: list[AvlRecordSpec], codec: int = CODEC_8E) -> bytes:
    data = encode_avl_array(records, codec)
    return b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big") + data + crc16_ibm(data).to_bytes(4, "big")


def encode_udp_packet(imei: str, records: list[AvlRecordSpec], packet_id: int = 0xCAFE,
                      avl_packet_id: int = 1, codec: int = CODEC_8E) -> bytes:
    body = (
        packet_id.to_bytes(2, "big") + b"\x01" + bytes([avl_packet_id])
        + len(imei).to_bytes(2, "big") + imei.encode("ascii") + encode_avl_array(records, codec)
    )
    return len(body).to_bytes(2, "big") + body
