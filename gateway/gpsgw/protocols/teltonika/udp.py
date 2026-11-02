"""Teltonika UDP channel framing (differs from TCP: no preamble, no CRC)."""
from __future__ import annotations

from dataclasses import dataclass

from gpsgw.protocols.base import ProtocolError
from gpsgw.protocols.reader import ByteReader

MAX_DATAGRAM = 1500


@dataclass(frozen=True)
class UdpPacket:
    packet_id: int
    avl_packet_id: int
    imei: str
    avl_array: bytes


def parse_datagram(datagram: bytes) -> UdpPacket:
    if len(datagram) > MAX_DATAGRAM:
        raise ProtocolError("oversized", str(len(datagram)))
    r = ByteReader(datagram)
    length = r.u16()
    if length != len(datagram) - 2:
        raise ProtocolError("length_mismatch", f"header {length}, actual {len(datagram) - 2}")
    packet_id = r.u16()
    r.u8()  # not usable byte
    avl_packet_id = r.u8()
    imei_len = r.u16()
    if not 8 <= imei_len <= 17:
        raise ProtocolError("bad_imei_length", str(imei_len))
    imei_raw = r.take(imei_len)
    try:
        imei = imei_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError("bad_imei", "non-ascii") from exc
    if not imei.isdigit():
        raise ProtocolError("bad_imei", "non-digit")
    return UdpPacket(packet_id, avl_packet_id, imei, r.take(r.remaining))


def build_ack(packet_id: int, avl_packet_id: int, accepted: int) -> bytes:
    """Server response: length 0x0005, packet id, 0x01, avl packet id, count."""
    return (
        (5).to_bytes(2, "big")
        + packet_id.to_bytes(2, "big")
        + b"\x01"
        + bytes([avl_packet_id & 0xFF, min(accepted, 255)])
    )
