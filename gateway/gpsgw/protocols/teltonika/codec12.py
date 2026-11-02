"""Codec 12 GPRS command encoding and response decoding."""
from __future__ import annotations

from gpsgw.protocols.base import CommandResponse, ProtocolError
from gpsgw.protocols.reader import ByteReader
from gpsgw.protocols.teltonika.avl import CODEC_12
from gpsgw.protocols.teltonika.crc import crc16_ibm

TYPE_COMMAND = 0x05
TYPE_RESPONSE = 0x06
MAX_COMMAND_LENGTH = 512


def encode_packet(text: str, msg_type: int) -> bytes:
    """A full TCP Codec 12 packet: commands (0x05) are sent by the server, responses (0x06) by the device."""
    try:
        body = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ProtocolError("bad_command", "non-ascii") from exc
    if not body or len(body) > MAX_COMMAND_LENGTH:
        raise ProtocolError("bad_command", "length")
    data = bytes([CODEC_12, 1, msg_type]) + len(body).to_bytes(4, "big") + body + b"\x01"
    return b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big") + data + crc16_ibm(data).to_bytes(4, "big")


def encode_command(text: str) -> bytes:
    """Build a full TCP Codec 12 command packet for ``text`` (ASCII)."""
    return encode_packet(text, TYPE_COMMAND)


def encode_response(text: str) -> bytes:
    """What a device sends back (used by the simulator and tests)."""
    return encode_packet(text, TYPE_RESPONSE)


def decode_command(data: bytes) -> str:
    """Decode the data section of a Codec 12 *command* (the simulator's side of the exchange)."""
    r = ByteReader(data)
    if r.u8() != CODEC_12:
        raise ProtocolError("unsupported_codec", "expected codec 12")
    r.u8()
    if r.u8() != TYPE_COMMAND:
        raise ProtocolError("bad_command_type", "expected a command")
    return r.take(r.u32()).decode("ascii", errors="replace")


def decode_response(data: bytes) -> CommandResponse:
    """Decode the data section (codec id .. quantity 2) of a Codec 12 response."""
    r = ByteReader(data)
    if r.u8() != CODEC_12:
        raise ProtocolError("unsupported_codec", "expected codec 12")
    r.u8()  # quantity 1, ignored per spec
    msg_type = r.u8()
    if msg_type != TYPE_RESPONSE:
        raise ProtocolError("bad_command_type", f"0x{msg_type:02X}")
    size = r.u32()
    body = r.take(size)
    r.u8()  # quantity 2
    if r.remaining:
        raise ProtocolError("trailing_bytes", str(r.remaining))
    return CommandResponse(text=body.decode("ascii", errors="replace"), raw=body)
