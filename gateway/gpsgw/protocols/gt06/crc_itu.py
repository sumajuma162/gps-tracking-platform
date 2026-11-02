"""CRC-ITU used by GT06 (CRC-16/X-25: reflected poly 0x1021, init 0xFFFF, final XOR 0xFFFF)."""
from __future__ import annotations


def _table() -> list[int]:
    out = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
        out.append(crc)
    return out


_TABLE = _table()


def crc_itu(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _TABLE[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFF
