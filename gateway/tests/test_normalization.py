"""IO mapping, validation and record hashing (spec A4, A5, A7)."""
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from gpsgw.normalization.io_mapper import IoMapping, IoMappingProfile, normalize, record_hash
from gpsgw.normalization.validators import InvalidRecord
from gpsgw.protocols.base import RawRecord

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def raw(**kw):
    base = dict(device_time=NOW - timedelta(seconds=5), latitude=-6.8160, longitude=39.2803,
                satellites=12, speed_kmh=42.0, heading=90.0, altitude=14.0)
    base.update(kw)
    return RawRecord(**base)


def test_fmc920_profile_maps_fields_sensors_and_events(fmc920_profile):
    r = raw(io={239: 1, 240: 1, 21: 4, 66: 12650, 67: 4012, 16: 123456, 182: 9, 69: 1,
                253: 2, 247: 1, 9999: 7})
    n = normalize(r, identifier="356307042441013", protocol="teltonika", profile=fmc920_profile, server_time=NOW)
    assert n.ignition is True and n.motion is True and n.gsm_signal == 4
    assert n.external_voltage == pytest.approx(12.65)
    assert n.battery_voltage == pytest.approx(4.012)
    assert n.odometer_km == pytest.approx(123.456)
    assert n.hdop == pytest.approx(0.9)
    assert n.sensors["gnss_status"] == 1
    assert set(n.events) == {"harsh_braking", "crash"}
    assert n.raw_metadata["io"]["9999"] == 7  # unmapped IO preserved, nothing lost
    assert n.gps_valid is True


def test_official_example_has_no_fix(decoder, ctx, fmc920_profile):
    from tests.fixtures import teltonika_official as fx
    buf = bytearray(bytes.fromhex(fx.CODEC8_EX1))
    (rec,) = decoder.decode(decoder.extract_frame(buf, ctx), ctx)
    n = normalize(rec, identifier="356307042441013", protocol="teltonika",
                  profile=fmc920_profile, server_time=datetime(2019, 6, 10, 10, 5, tzinfo=UTC))
    assert n.gps_valid is False  # 0 satellites at 0,0: kept for IO, excluded from routes
    assert n.gsm_signal == 3 and n.external_voltage == pytest.approx(24.079)


@pytest.mark.parametrize("kw,code", [
    (dict(latitude=95.0), "bad_latitude"),
    (dict(longitude=-181.0), "bad_longitude"),
    (dict(device_time=NOW + timedelta(hours=1)), "future_timestamp"),
    (dict(device_time=datetime(2010, 1, 1, tzinfo=UTC)), "ancient_timestamp"),
])
def test_invalid_records_rejected(kw, code):
    with pytest.raises(InvalidRecord) as e:
        normalize(raw(**kw), identifier="x", protocol="teltonika", profile=None, server_time=NOW)
    assert e.value.code == code


def test_zero_satellites_marks_invalid_fix():
    n = normalize(raw(satellites=0), identifier="x", protocol="p", profile=None, server_time=NOW)
    assert n.gps_valid is False


def test_record_hash_stable_and_sensitive():
    a, b = raw(io={1: 1}), raw(io={1: 1})
    assert record_hash("i", a) == record_hash("i", b)
    assert record_hash("i", a) != record_hash("i", raw(io={1: 0}))
    assert record_hash("i", a) != record_hash("j", a)


def test_profile_rejects_unknown_field_and_duplicates():
    with pytest.raises(ValidationError):
        IoMapping(io_id=1, kind="field", target="not_a_field")
    with pytest.raises(ValidationError):
        IoMappingProfile(name="x", mappings=[IoMapping(io_id=1, kind="sensor", target="a"),
                                            IoMapping(io_id=1, kind="sensor", target="b")])


def test_edited_profile_changes_mapping_without_code(fmc920_profile):
    """A7: an admin edits a mapping in the UI -> next record reflects it."""
    data = fmc920_profile.model_dump()
    for m in data["mappings"]:
        if m["io_id"] == 66:
            m["scale"] = 0.01
    edited = IoMappingProfile.model_validate(data)
    n = normalize(raw(io={66: 1265}), identifier="x", protocol="teltonika", profile=edited, server_time=NOW)
    assert n.external_voltage == pytest.approx(12.65)
