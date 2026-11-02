"""IMEI handshake parsing and validation helpers."""
from __future__ import annotations

from gpsgw.protocols.base import ProtocolError

IMEI_HANDSHAKE_MAX = 2 + 17  # length prefix + up to 17 ASCII digits (IMEISV)


def parse_imei_handshake(payload: bytes) -> str:
    """Parse ``<u16 length><ASCII digits>`` and return the identifier."""
    if len(payload) < 2:
        raise ProtocolError("truncated", "imei length")
    length = int.from_bytes(payload[:2], "big")
    if not 8 <= length <= 17:
        raise ProtocolError("bad_imei_length", str(length))
    body = payload[2 : 2 + length]
    if len(body) != length:
        raise ProtocolError("truncated", "imei body")
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError("bad_imei", "non-ascii") from exc
    if not text.isdigit():
        raise ProtocolError("bad_imei", "non-digit")
    return text


def luhn_valid(digits: str) -> bool:
    """Luhn check used when an administrator registers a 15-digit IMEI."""
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def is_valid_imei(value: str) -> bool:
    return len(value) == 15 and luhn_valid(value)
