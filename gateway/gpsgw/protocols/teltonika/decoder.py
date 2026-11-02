"""Teltonika FMxxx decoder plugin (FMC920 and other Codec 8/8E devices).

TCP session: IMEI handshake -> 0x01/0x00 -> AVL packets (preamble, length,
codec data, CRC) -> 4-byte record-count ACK sent only after persistence.
UDP: every datagram carries the IMEI; see ``udp.py``.
"""
from __future__ import annotations

from typing import ClassVar

from gpsgw.protocols.base import (
    BaseProtocolDecoder,
    CommandResponse,
    DeviceIdentity,
    Frame,
    FrameKind,
    ProtocolError,
    RawRecord,
    SessionContext,
    Transport,
)
from gpsgw.protocols.teltonika import codec12, udp
from gpsgw.protocols.teltonika.avl import CODEC_12, parse_avl_array
from gpsgw.protocols.teltonika.crc import crc16_ibm
from gpsgw.protocols.teltonika.imei import parse_imei_handshake

PREAMBLE = b"\x00\x00\x00\x00"
PING = 0xFF  # single-byte keepalive some firmware sends on an open link


class TeltonikaDecoder(BaseProtocolDecoder):
    name: ClassVar[str] = "teltonika"
    transports: ClassVar[frozenset[Transport]] = frozenset({Transport.TCP, Transport.UDP})
    supports_commands: ClassVar[bool] = True
    max_frame_size: ClassVar[int] = 64 * 1024

    # ---- detection -------------------------------------------------------
    def can_handle(self, first_bytes: bytes) -> bool:
        if len(first_bytes) < 3:
            return False
        length = int.from_bytes(first_bytes[:2], "big")
        return 8 <= length <= 17 and chr(first_bytes[2]).isdigit()

    # ---- TCP framing -----------------------------------------------------
    def extract_frame(self, buffer: bytearray, ctx: SessionContext) -> Frame | None:
        if ctx.identity is None:
            return self._extract_handshake(buffer)
        if not buffer:
            return None
        if buffer[0] == PING:
            del buffer[:1]
            return Frame(FrameKind.HEARTBEAT, b"\xff")
        if len(buffer) < 8:
            if bytes(buffer[: min(4, len(buffer))]) != PREAMBLE[: min(4, len(buffer))]:
                raise ProtocolError("bad_preamble")
            return None
        if bytes(buffer[:4]) != PREAMBLE:
            raise ProtocolError("bad_preamble")
        length = int.from_bytes(buffer[4:8], "big")
        if length < 3 or length > self.max_frame_size:
            raise ProtocolError("oversized" if length > self.max_frame_size else "bad_length", str(length))
        total = 8 + length + 4
        if len(buffer) < total:
            return None
        raw = bytes(buffer[:total])
        del buffer[:total]
        kind = FrameKind.COMMAND_RESPONSE if raw[8] == CODEC_12 else FrameKind.DATA
        return Frame(kind, raw, {"transport": "tcp"})

    def _extract_handshake(self, buffer: bytearray) -> Frame | None:
        if len(buffer) < 2:
            return None
        length = int.from_bytes(buffer[:2], "big")
        if not 8 <= length <= 17:
            raise ProtocolError("bad_imei_length", str(length))
        if len(buffer) < 2 + length:
            return None
        raw = bytes(buffer[: 2 + length])
        del buffer[: 2 + length]
        return Frame(FrameKind.HANDSHAKE, raw, {"imei": parse_imei_handshake(raw)})

    # ---- UDP framing -----------------------------------------------------
    def parse_datagram(self, datagram: bytes) -> tuple[DeviceIdentity, Frame]:
        pkt = udp.parse_datagram(datagram)
        frame = Frame(
            FrameKind.DATA,
            datagram,
            {
                "transport": "udp",
                "packet_id": pkt.packet_id,
                "avl_packet_id": pkt.avl_packet_id,
                "avl_array": pkt.avl_array,
                "imei": pkt.imei,
            },
        )
        return DeviceIdentity(pkt.imei), frame

    # ---- identity ----------------------------------------------------------
    def identify_device(self, frame: Frame) -> DeviceIdentity | None:
        imei = frame.meta.get("imei")
        return DeviceIdentity(imei) if imei else None

    def handshake_response(self, accepted: bool, frame: Frame) -> bytes | None:
        return b"\x01" if accepted else b"\x00"

    # ---- data --------------------------------------------------------------
    def _verified_tcp_data(self, frame: Frame) -> bytes:
        raw = frame.raw
        length = int.from_bytes(raw[4:8], "big")
        data = raw[8 : 8 + length]
        crc_field = int.from_bytes(raw[8 + length : 12 + length], "big")
        if crc_field != crc16_ibm(data):
            raise ProtocolError("bad_crc", f"got 0x{crc_field:08X}")
        return data

    def decode(self, frame: Frame, ctx: SessionContext) -> list[RawRecord]:
        if frame.kind is not FrameKind.DATA:
            return []
        if frame.meta.get("transport") == "udp":
            data = frame.meta["avl_array"]
        else:
            data = self._verified_tcp_data(frame)
        _codec, records = parse_avl_array(data)
        return records

    def acknowledge(self, frame: Frame, accepted: int, ctx: SessionContext) -> bytes | None:
        if frame.kind is not FrameKind.DATA:
            return None
        if frame.meta.get("transport") == "udp":
            return udp.build_ack(frame.meta["packet_id"], frame.meta["avl_packet_id"], accepted)
        return accepted.to_bytes(4, "big")

    # ---- commands (Codec 12) -------------------------------------------------
    def encode_command(self, command_text: str) -> bytes:
        return codec12.encode_command(command_text)

    def decode_command_response(self, frame: Frame) -> CommandResponse | None:
        if frame.kind is not FrameKind.COMMAND_RESPONSE:
            return None
        return codec12.decode_response(self._verified_tcp_data(frame))
