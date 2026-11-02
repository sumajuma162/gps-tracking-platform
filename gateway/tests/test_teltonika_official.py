"""Decoding Teltonika's own documented example packets (spec A5 fixtures)."""
from datetime import UTC, datetime

import pytest

from gpsgw.protocols.base import FrameKind, ProtocolError
from gpsgw.protocols.teltonika import codec12
from gpsgw.protocols.teltonika.crc import crc16_ibm
from tests.fixtures import teltonika_official as fx


def frame_of(decoder, ctx, hex_str):
    buf = bytearray(bytes.fromhex(hex_str))
    frame = decoder.extract_frame(buf, ctx)
    assert frame is not None and not buf
    return frame


@pytest.mark.parametrize("hex_str,expected_crc", [
    (fx.CODEC8_EX1, 0xC7CF), (fx.CODEC8_EX2, 0xF22A), (fx.CODEC8_EX3, 0x252C), (fx.CODEC8E_EX1, 0x2994),
])
def test_crc16_ibm_matches_documented_crc(hex_str, expected_crc):
    raw = bytes.fromhex(hex_str)
    length = int.from_bytes(raw[4:8], "big")
    assert crc16_ibm(raw[8 : 8 + length]) == expected_crc


def test_imei_handshake(decoder):
    from gpsgw.protocols.base import SessionContext, Transport
    ctx = SessionContext(Transport.TCP, "t")
    buf = bytearray(bytes.fromhex(fx.IMEI_HANDSHAKE))
    frame = decoder.extract_frame(buf, ctx)
    assert frame.kind is FrameKind.HANDSHAKE
    assert decoder.identify_device(frame).identifier == "356307042441013"
    assert decoder.handshake_response(True, frame) == b"\x01"
    assert decoder.handshake_response(False, frame) == b"\x00"


def test_codec8_example1_all_io_widths(decoder, ctx):
    frame = frame_of(decoder, ctx, fx.CODEC8_EX1)
    (rec,) = decoder.decode(frame, ctx)
    assert rec.device_time == datetime(2019, 6, 10, 10, 4, 46, tzinfo=UTC)
    assert rec.priority == 1 and rec.event_io_id == 1
    assert rec.io == {21: 3, 1: 1, 66: 0x5E0F, 241: 0x601A, 78: 0}
    assert decoder.acknowledge(frame, 1, ctx) == bytes.fromhex("00000001")


def test_codec8_example2(decoder, ctx):
    (rec,) = decoder.decode(frame_of(decoder, ctx, fx.CODEC8_EX2), ctx)
    assert rec.device_time == datetime(2019, 6, 10, 10, 5, 36, tzinfo=UTC)
    assert rec.io == {21: 3, 1: 1, 66: 0x5E10}


def test_codec8_example3_two_records(decoder, ctx):
    frame = frame_of(decoder, ctx, fx.CODEC8_EX3)
    recs = decoder.decode(frame, ctx)
    assert [r.io[1] for r in recs] == [0, 1]
    assert recs[0].device_time == datetime(2019, 6, 10, 10, 1, 1, tzinfo=UTC)
    assert recs[1].device_time == datetime(2019, 6, 10, 10, 1, 19, tzinfo=UTC)
    assert decoder.acknowledge(frame, 2, ctx) == bytes.fromhex("00000002")


def test_codec8_extended_example(decoder, ctx):
    (rec,) = decoder.decode(frame_of(decoder, ctx, fx.CODEC8E_EX1), ctx)
    assert rec.device_time == datetime(2019, 6, 10, 11, 36, 32, tzinfo=UTC)
    assert rec.io == {1: 1, 17: 0x1D, 16: 0x015E2C88, 11: 0x3544C87A, 14: 0x1DD7E06A}
    assert rec.extra["codec"] == "8E"


def test_codec16_rejected_cleanly(decoder, ctx):
    frame = frame_of(decoder, ctx, fx.CODEC16_EX1)
    with pytest.raises(ProtocolError) as e:
        decoder.decode(frame, ctx)
    assert e.value.code == "unsupported_codec"


def test_udp_codec8_and_ack(decoder, ctx):
    identity, frame = decoder.parse_datagram(bytes.fromhex(fx.UDP_CODEC8))
    assert identity.identifier == "352093086403655"
    (rec,) = decoder.decode(frame, ctx)
    assert rec.device_time == datetime(2019, 6, 13, 6, 23, 26, tzinfo=UTC)
    assert rec.io[66] == 0x5DBC
    assert decoder.acknowledge(frame, 1, ctx) == bytes.fromhex(fx.UDP_CODEC8_ACK)


def test_udp_codec8e_and_ack(decoder, ctx):
    identity, frame = decoder.parse_datagram(bytes.fromhex(fx.UDP_CODEC8E))
    (rec,) = decoder.decode(frame, ctx)
    assert rec.io[16] == 0x015E2C88
    assert decoder.acknowledge(frame, 1, ctx) == bytes.fromhex(fx.UDP_CODEC8E_ACK)


@pytest.mark.parametrize("text,expected", [("getinfo", fx.CODEC12_GETINFO_CMD), ("getio", fx.CODEC12_GETIO_CMD)])
def test_codec12_command_encoding_matches_docs(decoder, text, expected):
    assert decoder.encode_command(text) == bytes.fromhex(expected)


def test_codec12_response_decoding(decoder, ctx):
    frame = frame_of(decoder, ctx, fx.CODEC12_GETIO_RESP)
    assert frame.kind is FrameKind.COMMAND_RESPONSE
    resp = decoder.decode_command_response(frame)
    assert resp.text == "DI1:1 DI2:0 DI3:0 AIN1:0 AIN2:16924 DO1:0 DO2:1"


def test_codec12_rejects_non_ascii():
    with pytest.raises(ProtocolError):
        codec12.encode_command("getinfo\u00e9")
