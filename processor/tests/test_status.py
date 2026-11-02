"""Status rules and deduplication (spec A10 offline / no-GPS detection)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gpsproc.app import group_name, live_update
from gpsproc.status import (
    IDLE,
    MOVING,
    NO_GPS,
    OFFLINE,
    STOPPED,
    UNKNOWN,
    SeenRecords,
    Thresholds,
    status_from_record,
    status_from_silence,
)

NOW = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
T = Thresholds()


@pytest.mark.parametrize("speed,ignition,motion,expected", [
    (42, True, True, MOVING), (5, True, True, MOVING), (4.9, True, False, IDLE), (0, False, False, STOPPED),
    (0, None, True, MOVING), (0, None, False, STOPPED), (None, None, None, STOPPED),
])
def test_status_from_a_valid_record(speed, ignition, motion, expected):
    assert status_from_record(speed_kmh=speed, ignition=ignition, motion=motion, gps_valid=True,
                              last_valid_fix=NOW, now=NOW, t=T) == expected


def test_no_gps_only_after_the_grace_period():
    recent = NOW - timedelta(minutes=3)
    stale = NOW - timedelta(minutes=11)
    kw = dict(speed_kmh=0, ignition=True, motion=False, gps_valid=False, now=NOW, t=T)
    assert status_from_record(last_valid_fix=recent, **kw) == IDLE        # brief loss: keep the engine state
    assert status_from_record(last_valid_fix=stale, **kw) == NO_GPS
    assert status_from_record(last_valid_fix=None, **kw) == NO_GPS


def test_silence_sweep():
    assert status_from_silence(MOVING, last_packet_at=NOW - timedelta(minutes=6), last_valid_fix=NOW, now=NOW,
                               t=T) == OFFLINE
    assert status_from_silence(MOVING, last_packet_at=NOW - timedelta(minutes=1), last_valid_fix=NOW, now=NOW,
                               t=T) == MOVING
    assert status_from_silence(IDLE, last_packet_at=NOW, last_valid_fix=NOW - timedelta(minutes=20), now=NOW,
                               t=T) == NO_GPS
    assert status_from_silence(NO_GPS, last_packet_at=NOW - timedelta(minutes=9), last_valid_fix=None, now=NOW,
                               t=T) == OFFLINE                               # offline beats no GPS
    assert status_from_silence(UNKNOWN, last_packet_at=None, last_valid_fix=None, now=NOW, t=T) == UNKNOWN


def test_thresholds_are_configurable():
    strict = Thresholds(moving_kmh=10, offline_after=timedelta(minutes=1))
    assert status_from_record(speed_kmh=8, ignition=True, motion=True, gps_valid=True, last_valid_fix=NOW,
                              now=NOW, t=strict) == IDLE
    assert status_from_silence(MOVING, last_packet_at=NOW - timedelta(seconds=90), last_valid_fix=NOW, now=NOW,
                               t=strict) == OFFLINE


def test_seen_records_dedupes_per_device_and_is_bounded():
    seen = SeenRecords(per_device=3)
    assert seen.first_time(1, "a") and not seen.first_time(1, "a")
    assert seen.first_time(2, "a")                                           # other device, same hash
    for h in "bcd":
        seen.first_time(1, h)
    assert seen.first_time(1, "a")                                           # evicted after 3 newer hashes


def test_live_update_shape():
    state = {"device_id": 7, "organization_id": 3, "vehicle_id": 11, "status": MOVING, "device_time": NOW,
             "last_packet_at": NOW, "latitude": -6.8, "longitude": 39.28, "speed_kmh": 40.0, "heading": 90.0,
             "satellites": 11, "ignition": True, "gps_valid": True, "external_voltage": 27.6, "battery_voltage": 4.0,
             "gsm_signal": 4}
    update = live_update(state, IDLE)
    assert update["status"] == IDLE and update["fix_time"] == NOW.isoformat() and "organization_id" not in update
    assert group_name(3) == "tracking.org.3"


def test_metrics_text_format() -> None:
    from gpsproc.main import render_metrics

    text = render_metrics({"stats": {"entries": 5, "last_lag_ms": 812.5, "consumer": "x"}}).decode()
    assert "# TYPE gpsproc_entries_total counter\ngpsproc_entries_total 5.0" in text
    assert "# TYPE gpsproc_last_lag_ms gauge\ngpsproc_last_lag_ms 812.5" in text and "consumer" not in text
