"""Bounds-checked big-endian reader. Every overrun becomes ProtocolError."""
from __future__ import annotations

from gpsgw.protocols.base import ProtocolError


class ByteReader:
    __slots__ = ("_data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self._data = data
        self.pos = pos

    @property
    def remaining(self) -> int:
        return len(self._data) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self._data):
            raise ProtocolError("truncated", f"need {n} bytes at offset {self.pos}")
        chunk = self._data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def i16(self) -> int:
        return int.from_bytes(self.take(2), "big", signed=True)

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def i32(self) -> int:
        return int.from_bytes(self.take(4), "big", signed=True)

    def u64(self) -> int:
        return int.from_bytes(self.take(8), "big")
