"""CRC-16/IBM (a.k.a. CRC-16/ARC): poly 0xA001 reflected, init 0x0000."""
from __future__ import annotations


def crc16_ibm(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc
