from datetime import UTC, datetime

import pytest

from gpsgw.protocols.teltonika.avl import CODEC_8, CODEC_8E
from gpsgw.protocols.teltonika.encoder import AvlRecordSpec, encode_tcp_packet, encode_udp_packet
from gpsgw.protocols.teltonika.imei import is_valid_imei, luhn_valid


@pytest.mark.parametrize("codec", [CODEC_8, CODEC_8E])
def test_round_trip_with_negative_coordinates(decoder, ctx, codec):
    spec = AvlRecordSpec(time=datetime(2026, 9, 30, 8, 0, tzinfo=UTC), latitude=-6.8160123,
                         longitude=39.2803456, altitude=-3, heading=275, satellites=11, speed_kmh=63,
                         event_io_id=239, io={239: (1, 1), 66: (2, 12600), 16: (4, 987654)})
    buf = bytearray(encode_tcp_packet([spec, spec], codec))
    recs = decoder.decode(decoder.extract_frame(buf, ctx), ctx)
    assert len(recs) == 2
    r = recs[0]
    assert r.latitude == pytest.approx(-6.8160123) and r.longitude == pytest.approx(39.2803456)
    assert r.altitude == -3 and r.heading == 275 and r.satellites == 11 and r.speed_kmh == 63
    assert r.io == {239: 1, 66: 12600, 16: 987654} and r.event_io_id == 239


def test_udp_round_trip(decoder, ctx):
    spec = AvlRecordSpec(time=datetime(2026, 9, 30, tzinfo=UTC), latitude=-6.8, longitude=39.2, satellites=9)
    identity, frame = decoder.parse_datagram(encode_udp_packet("356307042441013", [spec], avl_packet_id=9))
    assert identity.identifier == "356307042441013"
    assert len(decoder.decode(frame, ctx)) == 1
    assert decoder.acknowledge(frame, 1, ctx)[-2:] == bytes([9, 1])


def test_luhn():
    assert luhn_valid("79927398713")
    assert not luhn_valid("79927398710")
    assert is_valid_imei("490154203237518")
    assert not is_valid_imei("490154203237519")
    assert not is_valid_imei("12345")
