"""Malformed-input handling: every failure is a ProtocolError, never a crash."""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gpsgw.protocols.base import ProtocolError, SessionContext, Transport
from gpsgw.protocols.teltonika import udp
from tests.fixtures import teltonika_official as fx


def extract_and_decode(decoder, ctx, data: bytes):
    buf = bytearray(data)
    out = []
    while (frame := decoder.extract_frame(buf, ctx)) is not None:
        out.append(decoder.decode(frame, ctx))
    return out, buf


def test_bad_crc(decoder, ctx):
    raw = bytearray(bytes.fromhex(fx.CODEC8_EX2))
    raw[-1] ^= 0xFF
    with pytest.raises(ProtocolError) as e:
        extract_and_decode(decoder, ctx, bytes(raw))
    assert e.value.code == "bad_crc"


def test_corrupted_body_detected_by_crc(decoder, ctx):
    raw = bytearray(bytes.fromhex(fx.CODEC8_EX2))
    raw[20] ^= 0x01
    with pytest.raises(ProtocolError) as e:
        extract_and_decode(decoder, ctx, bytes(raw))
    assert e.value.code == "bad_crc"


def test_truncated_packet_waits_for_more_bytes(decoder, ctx):
    raw = bytes.fromhex(fx.CODEC8_EX1)
    frames, buf = extract_and_decode(decoder, ctx, raw[:-5])
    assert frames == [] and len(buf) == len(raw) - 5


def test_partial_reads_byte_by_byte(decoder, ctx):
    raw = bytes.fromhex(fx.CODEC8_EX3) + bytes.fromhex(fx.CODEC8_EX2)
    buf = bytearray()
    decoded = []
    for b in raw:
        buf.append(b)
        while (frame := decoder.extract_frame(buf, ctx)) is not None:
            decoded.extend(decoder.decode(frame, ctx))
    assert len(decoded) == 3 and not buf


def test_oversized_length_rejected_before_buffering(decoder, ctx):
    data = b"\x00\x00\x00\x00" + (10_000_000).to_bytes(4, "big")
    with pytest.raises(ProtocolError) as e:
        decoder.extract_frame(bytearray(data), ctx)
    assert e.value.code == "oversized"


def test_bad_preamble(decoder, ctx):
    with pytest.raises(ProtocolError) as e:
        decoder.extract_frame(bytearray(b"\x01\x02\x03\x04\x00\x00\x00\x10"), ctx)
    assert e.value.code == "bad_preamble"


def test_count_mismatch(decoder, ctx):
    from gpsgw.protocols.teltonika.crc import crc16_ibm
    raw = bytearray(bytes.fromhex(fx.CODEC8_EX2))
    length = int.from_bytes(raw[4:8], "big")
    raw[8 + length - 1] = 2  # second record count no longer matches
    raw[8 + length : 12 + length] = crc16_ibm(bytes(raw[8 : 8 + length])).to_bytes(4, "big")
    with pytest.raises(ProtocolError) as e:
        extract_and_decode(decoder, ctx, bytes(raw))
    assert e.value.code in {"count_mismatch", "truncated", "trailing_bytes"}


def test_bad_imei_handshake(decoder):
    ctx = SessionContext(Transport.TCP, "t")
    with pytest.raises(ProtocolError):
        decoder.extract_frame(bytearray(b"\x00\x0fABCDEFGHIJKLMNO"), ctx)
    with pytest.raises(ProtocolError):
        decoder.extract_frame(bytearray(b"\xff\xff"), ctx)


def test_udp_length_mismatch():
    raw = bytes.fromhex(fx.UDP_CODEC8)[:-1]
    with pytest.raises(ProtocolError):
        udp.parse_datagram(raw)


def test_ping_byte_is_heartbeat(decoder, ctx):
    from gpsgw.protocols.base import FrameKind
    buf = bytearray(b"\xff" + bytes.fromhex(fx.CODEC8_EX2))
    assert decoder.extract_frame(buf, ctx).kind is FrameKind.HEARTBEAT
    assert decoder.extract_frame(buf, ctx).kind is FrameKind.DATA


@settings(max_examples=1500, deadline=None)
@given(st.binary(max_size=600))
def test_fuzz_tcp_never_crashes(data):
    from gpsgw.protocols.base import DeviceIdentity
    from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder
    dec = TeltonikaDecoder()
    for identity in (None, DeviceIdentity("356307042441013")):
        ctx = SessionContext(Transport.TCP, "fuzz", identity=identity)
        try:
            extract_and_decode(dec, ctx, data)
        except ProtocolError:
            pass


@settings(max_examples=1500, deadline=None)
@given(st.binary(max_size=600))
def test_fuzz_mutated_valid_packet(data):
    """Random corruption of a real packet must only ever raise ProtocolError."""
    from gpsgw.protocols.base import DeviceIdentity
    from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder
    base = bytearray(bytes.fromhex(fx.CODEC8E_EX1))
    for i, b in enumerate(data[:40]):
        base[(b * 7 + i) % len(base)] ^= b
    ctx = SessionContext(Transport.TCP, "fuzz", identity=DeviceIdentity("1" * 15))
    try:
        extract_and_decode(TeltonikaDecoder(), ctx, bytes(base))
    except ProtocolError:
        pass


@settings(max_examples=1500, deadline=None)
@given(st.binary(max_size=600))
def test_fuzz_udp_never_crashes(data):
    from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder
    dec = TeltonikaDecoder()
    try:
        identity, frame = dec.parse_datagram(data)
        dec.decode(frame, SessionContext(Transport.UDP, "fuzz", identity=identity))
    except ProtocolError:
        pass
