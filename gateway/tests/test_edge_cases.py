"""Error branches and the protocol registry."""
import pytest

from gpsgw.protocols.base import (
    Frame,
    FrameKind,
    ProtocolError,
    ProtocolRegistry,
    SessionContext,
    Transport,
    default_registry,
)
from gpsgw.protocols.teltonika import codec12, udp
from gpsgw.protocols.teltonika.avl import parse_avl_array
from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder
from gpsgw.protocols.teltonika.imei import parse_imei_handshake
from tests.fixtures import teltonika_official as fx


def avl_array(hex_str: str) -> bytes:
    raw = bytes.fromhex(hex_str)
    return raw[8 : 8 + int.from_bytes(raw[4:8], "big")]


def test_registry_contract():
    reg = default_registry()
    assert reg.names() == ["generic_json", "generic_text", "gt06", "teltonika"]   # gt06 via the entry point
    assert {"name": "teltonika", "transports": ["tcp", "udp"], "supports_commands": True} in reg.describe()
    assert {"name": "generic_json", "transports": ["http"], "supports_commands": False} in reg.describe()
    with pytest.raises(ValueError):
        reg.register(TeltonikaDecoder())
    with pytest.raises(KeyError):
        ProtocolRegistry().get("gt06")


def test_can_handle(decoder):
    assert decoder.can_handle(bytes.fromhex(fx.IMEI_HANDSHAKE))
    assert not decoder.can_handle(b"\x78\x78\x0d\x01")
    assert not decoder.can_handle(b"\x00")


@pytest.mark.parametrize("mutate,code", [
    (lambda a: a[:1] + b"\x00" + a[2:], "empty_packet"),
    (lambda a: a + b"\x00", "trailing_bytes"),
    (lambda a: a[:1] + a[1:2] + b"\xff" * 8 + a[10:], "bad_timestamp"),
])
def test_avl_error_branches(mutate, code):
    with pytest.raises(ProtocolError) as e:
        parse_avl_array(mutate(avl_array(fx.CODEC8_EX2)))
    assert e.value.code == code


def test_io_count_mismatch():
    a = bytearray(avl_array(fx.CODEC8_EX2))
    a[2 + 8 + 1 + 15 + 1] = 9  # N of total IO
    with pytest.raises(ProtocolError) as e:
        parse_avl_array(bytes(a))
    assert e.value.code == "io_count_mismatch"


def test_codec8e_variable_length_nx_section():
    a = bytearray(avl_array(fx.CODEC8E_EX1))
    # replace NX count 0x0000 (just before final count byte) with one 3-byte element id 0x0100
    nx_pos = len(a) - 3
    assert a[nx_pos : nx_pos + 2] == b"\x00\x00"
    a[nx_pos : nx_pos + 2] = b"\x00\x01" + b"\x01\x00" + b"\x00\x03" + b"abc"
    a[2 + 8 + 1 + 15 + 2 : 2 + 8 + 1 + 15 + 4] = (6).to_bytes(2, "big")  # N total 5 -> 6
    _, (rec,) = parse_avl_array(bytes(a))
    assert rec.io[0x100] == b"abc"


def test_codec12_response_errors():
    with pytest.raises(ProtocolError):
        codec12.decode_response(b"\x08\x01\x06\x00\x00\x00\x00\x01")
    with pytest.raises(ProtocolError):
        codec12.decode_response(b"\x0c\x01\x05\x00\x00\x00\x00\x01")
    with pytest.raises(ProtocolError):
        codec12.decode_response(b"\x0c\x01\x06\x00\x00\x00\x00\x01\x00")
    with pytest.raises(ProtocolError):
        codec12.encode_command("")


def test_decoder_ignores_non_data_frames(decoder, ctx):
    hb = Frame(FrameKind.HEARTBEAT, b"\xff")
    assert decoder.decode(hb, ctx) == [] and decoder.acknowledge(hb, 1, ctx) is None
    assert decoder.decode_command_response(hb) is None


@pytest.mark.parametrize("payload", [b"\x00", b"\x00\x0f12", b"\x00\x05123", b"\x00\x0f" + bytes([0xC3]) * 15])
def test_imei_parse_errors(payload):
    with pytest.raises(ProtocolError):
        parse_imei_handshake(payload)


@pytest.mark.parametrize("imei", [b"ABCDEFGHIJKLMNO", bytes([0xC3]) * 15])
def test_udp_bad_imei(imei):
    body = b"\xca\xfe\x01\x05\x00\x0f" + imei + avl_array(fx.CODEC8_EX2)
    with pytest.raises(ProtocolError):
        udp.parse_datagram(len(body).to_bytes(2, "big") + body)


def test_udp_oversized_and_short_imei():
    with pytest.raises(ProtocolError):
        udp.parse_datagram(b"\x00" * 2000)
    body = b"\xca\xfe\x01\x05\x00\x02" + b"12"
    with pytest.raises(ProtocolError):
        udp.parse_datagram(len(body).to_bytes(2, "big") + body)


def test_short_buffers_wait(decoder):
    ctx = SessionContext(Transport.TCP, "t")
    assert decoder.extract_frame(bytearray(b"\x00"), ctx) is None
    assert decoder.extract_frame(bytearray(b"\x00\x0f123"), ctx) is None


def test_bad_length_field(decoder, ctx):
    with pytest.raises(ProtocolError) as e:
        decoder.extract_frame(bytearray(b"\x00\x00\x00\x00\x00\x00\x00\x01"), ctx)
    assert e.value.code == "bad_length"
    assert decoder.extract_frame(bytearray(), ctx) is None
    assert decoder.extract_frame(bytearray(b"\x00\x00"), ctx) is None
