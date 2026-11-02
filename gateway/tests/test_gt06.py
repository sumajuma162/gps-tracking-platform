"""GT06 plugin: framing, CRC, login, location, heartbeat, alarm, real TCP sessions and fuzzing (spec A6, A16)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gpsgw.protocols.base import DeviceIdentity, FrameKind, ProtocolError, SessionContext, Transport, default_registry
from gpsgw.protocols.gt06.crc_itu import crc_itu
from gpsgw.protocols.gt06.decoder import Gt06Decoder, encode_packet
from tests.fixtures import gt06_official as F

D = Gt06Decoder()


def ctx(logged_in: bool = True) -> SessionContext:
    c = SessionContext(Transport.TCP, "1.2.3.4:5")
    if logged_in:
        c.identity = DeviceIdentity("123456789012345")
    return c


def position_content(lat: float = -6.8, lon: float = 39.28, speed: int = 42, course: int = 90, valid: bool = True,
                     when: datetime = datetime(2026, 10, 3, 8, 30, 15, tzinfo=UTC)) -> bytes:
    flags = course | (1 << 12 if valid else 0) | (1 << 10 if lat >= 0 else 0) | (1 << 11 if lon < 0 else 0)
    return (bytes([when.year - 2000, when.month, when.day, when.hour, when.minute, when.second, 0xC9])
            + round(abs(lat) * 1_800_000).to_bytes(4, "big") + round(abs(lon) * 1_800_000).to_bytes(4, "big")
            + bytes([speed]) + flags.to_bytes(2, "big"))


LBS = (640).to_bytes(2, "big") + bytes([4]) + (0x1234).to_bytes(2, "big") + (0xABCDE).to_bytes(3, "big")


def test_crc_and_registry():
    assert crc_itu(b"123456789") == 0x906E                     # CRC-16/X-25 check value
    assert "gt06" in default_registry().names()                # loaded through the entry-point plugin group
    assert D.can_handle(b"\x78\x78\x0d") and not D.can_handle(b"\x00\x0f")


def test_official_login_and_reply():
    c = ctx(logged_in=False)
    frame = D.extract_frame(bytearray(F.LOGIN), c)
    assert frame is not None and frame.kind is FrameKind.HANDSHAKE
    assert D.identify_device(frame) == DeviceIdentity("123456789012345")
    assert D.handshake_response(True, frame) == F.LOGIN_REPLY
    assert D.handshake_response(False, frame) is None          # unregistered: no ACK


def test_official_location_packet():
    frame = D.extract_frame(bytearray(F.LOCATION), ctx())
    assert frame is not None and frame.kind is FrameKind.DATA
    [r] = D.decode(frame, ctx())
    assert r.device_time == datetime(2011, 8, 29, 17, 46, 16, tzinfo=UTC)
    assert (round(r.latitude, 6), round(r.longitude, 6)) == (23.111668, 114.409285)
    assert (r.satellites, r.speed_kmh, r.heading, r.gps_valid) == (15, 0.0, 143.0, True)
    assert r.io[10] == 460 and r.io[12] == 0x287D and r.io[13] == 0x001FB8
    assert D.acknowledge(frame, 1, ctx()) is None               # location packets have no ACK


def test_hemispheres_invalid_fix_and_0x22():
    pkt = encode_packet(0x22, position_content(lat=-6.8, lon=-39.2, valid=False) + LBS + bytes([1, 0, 1])
                        + (123456).to_bytes(4, "big"), 7)
    [r] = D.decode(D.extract_frame(bytearray(pkt), ctx()), ctx())    # type: ignore[arg-type]
    assert r.latitude == pytest.approx(-6.8) and r.longitude == pytest.approx(-39.2) and r.gps_valid is False
    assert (r.io[1], r.io[7], r.io[6]) == (1, 1, 123456)         # ACC on, re-uploaded, mileage


def test_heartbeat_is_acked_and_feeds_ignition():
    c = ctx()
    hb = encode_packet(0x13, bytes([0b0100_0110, 4, 3, 0, 1]), 9)
    frame = D.extract_frame(bytearray(hb), c)
    assert frame is not None and frame.kind is FrameKind.DATA and D.decode(frame, c) == []
    assert D.acknowledge(frame, 0, c) == encode_packet(0x13, b"", 9)
    [r] = D.decode(D.extract_frame(bytearray(encode_packet(0x12, position_content() + LBS, 10)), c), c)  # type: ignore[arg-type]
    assert r.io[1] == 1                                          # ACC from the heartbeat's terminal info


def test_alarm_packet():
    content = position_content() + bytes([9]) + LBS + bytes([0b0000_0010, 5, 4, 0x02, 0x01])
    pkt = encode_packet(0x16, content, 0x0102)
    frame = D.extract_frame(bytearray(pkt), ctx())
    [r] = D.decode(frame, ctx())                                 # type: ignore[arg-type]
    assert (r.io[5], r.io[1], r.io[3], r.io[4], r.event_io_id) == (2, 1, 5, 4, 5)
    assert D.acknowledge(frame, 1, ctx()) == encode_packet(0x16, b"", 0x0102)   # type: ignore[arg-type]


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p[:-3] + bytes([p[-3] ^ 1]) + p[-2:], "crc_mismatch"),
    (lambda p: b"\x00\x00" + p[2:], "bad_start"),
    (lambda p: p[:-2] + b"\x00\x00", "bad_stop"),
])
def test_corrupt_frames(mutate, code):
    with pytest.raises(ProtocolError) as exc:
        D.extract_frame(bytearray(mutate(F.LOCATION)), ctx())
    assert exc.value.code == code


def test_partial_reads_and_session_rules():
    buf = bytearray(F.LOCATION[:10])
    assert D.extract_frame(buf, ctx()) is None and len(buf) == 10   # waits for more bytes
    buf += F.LOCATION[10:] + F.LOCATION
    assert D.extract_frame(buf, ctx()) is not None and D.extract_frame(buf, ctx()) is not None and not buf
    with pytest.raises(ProtocolError, match="not_logged_in"):
        D.extract_frame(bytearray(F.LOCATION), ctx(logged_in=False))
    with pytest.raises(ProtocolError, match="oversized"):
        D.extract_frame(bytearray(b"\x79\x79\xff\xff"), ctx())
    other = D.extract_frame(bytearray(encode_packet(0x15, b"hello", 1)), ctx())
    assert other is not None and other.kind is FrameKind.HEARTBEAT                 # skipped, session continues


@settings(max_examples=1500, deadline=None)
@given(st.binary(max_size=300))
def test_fuzz_never_raises_anything_but_protocol_error(data):
    for logged in (False, True):
        c = ctx(logged)
        buf = bytearray(data)
        try:
            while (frame := D.extract_frame(buf, c)) is not None:
                if frame.kind is FrameKind.DATA:
                    D.decode(frame, c)
        except ProtocolError:
            pass


@settings(max_examples=800, deadline=None)
@given(st.integers(0x10, 0x30), st.binary(min_size=0, max_size=60), st.integers(0, 0xFFFF))
def test_fuzz_valid_crc_random_content(protocol, content, serial):
    c = ctx()
    try:
        frame = D.extract_frame(bytearray(encode_packet(protocol, content, serial)), c)
        if frame is not None and frame.kind is FrameKind.DATA:
            D.decode(frame, c)
    except ProtocolError:
        pass


async def test_real_tcp_session():
    """Login -> accepted with ACK, location stored, heartbeat ACKed, unknown IMEI rejected without an ACK."""
    from gpsgw.listeners.tcp import TcpLimits, TcpListener
    from gpsgw.normalization.io_mapper import IoMappingProfile
    from gpsgw.services.memory import MemoryInbox, MemoryPersistence, MemoryPublisher, MemoryRegistry
    from gpsgw.services.pipeline import IngestPipeline
    from gpsgw.services.ports import DeviceInfo

    profile = IoMappingProfile.model_validate({"name": "gt06", "mappings": [
        {"io_id": 1, "kind": "field", "target": "ignition"}, {"io_id": 4, "kind": "field", "target": "gsm_signal"}]})
    registry = MemoryRegistry([DeviceInfo(device_id=1, identifier="123456789012345", protocol="gt06",
                                          organization_id=1, vehicle_id=2, profile=profile)])
    storage, inbox = MemoryPersistence(), MemoryInbox()
    listener = TcpListener(Gt06Decoder(), registry, IngestPipeline(storage, MemoryPublisher()), inbox,
                           limits=TcpLimits(handshake_timeout=2, idle_timeout=2))
    port = await listener.start("127.0.0.1")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(F.LOGIN)
        assert await asyncio.wait_for(reader.readexactly(10), 2) == F.LOGIN_REPLY
        writer.write(encode_packet(0x13, bytes([2, 4, 3, 0, 1]), 3))
        assert await asyncio.wait_for(reader.readexactly(10), 2) == encode_packet(0x13, b"", 3)
        now = datetime.now(UTC).replace(microsecond=0)
        writer.write(encode_packet(0x12, position_content(when=now) + LBS, 4))
        burst = b"".join(encode_packet(0x12, position_content(when=now - timedelta(seconds=10 * (200 - i))) + LBS,
                                       10 + i) for i in range(200))       # a 9 KB backlog burst in one write
        writer.write(burst)
        writer.write(encode_packet(0x13, bytes([2, 4, 3, 0, 1]), 5))     # ACK proves the locations were handled
        assert await asyncio.wait_for(reader.readexactly(10), 2) == encode_packet(0x13, b"", 5)
        writer.close()
        assert len(storage.rows) == 201
        stored = max(storage.rows.values(), key=lambda r: r.device_time)
        assert stored.protocol == "gt06" and stored.latitude == pytest.approx(-6.8) and stored.ignition is True
        assert stored.gsm_signal is None and stored.speed_kmh == 42
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        w2.write(encode_packet(0x01, bytes.fromhex("0352093086403655"), 1))
        assert await asyncio.wait_for(r2.read(10), 2) == b""          # closed without an ACK
        assert inbox.attempts[-1].identifier == "352093086403655" and inbox.attempts[-1].protocol == "gt06"
    finally:
        await listener.stop()

