"""Alert decisions (spec A11): rule scope and parameters, schedules, unauthorized movement, severity, quiet hours,
preferences and messages. The database workflow is tested end to end in the backend suite."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import time as dtime

import pytest

from gpsproc.alerts import (
    Rule,
    Signal,
    channel_allowed,
    in_quiet_hours,
    in_schedule,
    render,
    rule_matches,
    severity_for,
    signals_from_items,
)

# Wednesday 2026-09-30, 10:00 in Dar es Salaam (UTC+3) = 07:00 UTC
WED_10 = datetime(2026, 9, 30, 7, 0, tzinfo=UTC)


def rule(**kw) -> Rule:  # type: ignore[no-untyped-def]
    base = dict(id=1, type="overspeed", severity="warning", params={}, applies_to_all=True, vehicles=set(),
                groups=set(), schedule={}, recipient_roles=["fleet_manager"], recipient_users=set(),
                cooldown=timedelta(minutes=10))
    base.update(kw)
    return Rule(**base)  # type: ignore[arg-type]


def test_schedules_including_overnight_windows():
    weekdays = {"days": [0, 1, 2, 3, 4], "from": "06:00", "to": "18:00"}
    assert in_schedule(weekdays, WED_10) and not in_schedule(weekdays, WED_10 + timedelta(hours=9))   # 19:00
    assert not in_schedule(weekdays, WED_10 + timedelta(days=3))                                     # Saturday
    night = {"days": [4], "from": "22:00", "to": "06:00"}                                            # Friday night
    fri_23 = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)
    sat_03 = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)
    assert in_schedule(night, fri_23) and in_schedule(night, sat_03)        # after midnight still Friday's window
    assert not in_schedule(night, sat_03 + timedelta(hours=4))              # 07:00 Saturday
    assert in_schedule({}, WED_10)


def test_scope_by_vehicle_and_group():
    sig = Signal("overspeed", WED_10, value=95)
    assert rule_matches(rule(), sig, 7, set(), True)[0]
    only = rule(applies_to_all=False, vehicles={3}, groups={9})
    assert not rule_matches(only, sig, 7, set(), True)[0]
    assert rule_matches(only, sig, 3, set(), True)[0] and rule_matches(only, sig, 7, {9}, True)[0]
    assert not rule_matches(only, sig, None, set(), True)[0]                # a tracker with no vehicle


@pytest.mark.parametrize("r,sig,expected", [
    (rule(params={"limit_kmh": 100}), Signal("overspeed", WED_10, value=95), False),
    (rule(params={"limit_kmh": 90}), Signal("overspeed", WED_10, value=95), True),
    (rule(type="geofence_enter", params={"geofences": [5]}), Signal("geofence_enter", WED_10, geofence_id=6), False),
    (rule(type="geofence_enter", params={"geofences": [5]}), Signal("geofence_enter", WED_10, geofence_id=5), True),
    (rule(type="geofence_enter"), Signal("geofence_exit", WED_10, geofence_id=5), False),
    (rule(type="long_idle", params={"minutes": 20}), Signal("long_idle", WED_10, value=12), False),
    (rule(type="long_stop"), Signal("long_stop", WED_10, value=130), True),      # default 120 min
    (rule(type="low_battery"), Signal("low_battery", WED_10, value=3.5), True),  # default 3.6 V
    (rule(type="low_battery"), Signal("low_battery", WED_10, value=3.7), False),
    (rule(schedule={"days": [5, 6]}), Signal("overspeed", WED_10, value=95), False),   # weekend-only rule
])
def test_parameters(r, sig, expected):
    assert rule_matches(r, sig, 7, set(), True)[0] is expected


def test_unauthorized_movement():
    allowed = rule(type="unauthorized_movement", schedule={"days": [0, 1, 2, 3, 4], "from": "06:00", "to": "18:00"},
                   params={"require_driver": True})
    on = Signal("ignition_on", WED_10)
    assert rule_matches(allowed, on, 7, set(), has_driver=True) == (False, {})
    assert rule_matches(allowed, on, 7, set(), has_driver=False) == (True, {"reason": "no_driver"})
    assert rule_matches(allowed, Signal("ignition_on", WED_10 + timedelta(hours=12)), 7, set(), True) == (
        True, {"reason": "outside_hours"})
    assert not rule_matches(allowed, Signal("ignition_off", WED_10 + timedelta(hours=12)), 7, set(), True)[0]


def test_severity_keeps_the_higher_one():
    assert severity_for(rule(), Signal("overspeed", WED_10, severity="critical")) == "critical"
    assert severity_for(rule(severity="critical"), Signal("overspeed", WED_10, severity="warning")) == "critical"
    assert severity_for(rule(), Signal("overspeed", WED_10)) == "warning"


def test_quiet_hours_in_the_users_timezone():
    q = (dtime(22, 0), dtime(6, 0))
    night = datetime(2026, 9, 30, 20, 30, tzinfo=UTC)               # 23:30 in Dar es Salaam
    assert in_quiet_hours(*q, night, "Africa/Dar_es_Salaam")
    assert not in_quiet_hours(*q, night, "UTC")                       # 20:30 in UTC
    assert not in_quiet_hours(*q, WED_10, "Africa/Dar_es_Salaam")
    assert not in_quiet_hours(None, None, night, "Africa/Dar_es_Salaam")


def test_preferences_and_defaults():
    assert channel_allowed({}, "in_app", "overspeed", "info")
    assert not channel_allowed({}, "email", "overspeed", "warning")
    assert channel_allowed({}, "email", "crash", "critical")
    prefs = {("email", ""): (True, "warning"), ("in_app", "ignition_on"): (False, "info")}
    assert channel_allowed(prefs, "email", "overspeed", "warning")
    assert not channel_allowed(prefs, "in_app", "ignition_on", "critical")
    assert channel_allowed(prefs, "in_app", "crash", "info")
    assert not channel_allowed({}, "sms", "crash", "critical")       # prepared channels are off unless configured


def test_messages_in_each_language():
    p = {"vehicle": "T 123 ABC", "limit": 80, "value": 95.0}
    assert render("en", "overspeed", p) == "T 123 ABC exceeded 80 km/h (top 95.0 km/h)"
    assert render("sw", "overspeed", p).startswith("T 123 ABC imezidi km/h 80")
    moved = render("sw", "unauthorized_movement", {"vehicle": "X", "reason": "no_driver"})
    assert moved == "X imetembea bila dereva aliyepangwa"
    assert "?" in render("en", "geofence_enter", {"vehicle": "X"})    # a missing value never crashes rendering


def test_signals_from_analysis_results():
    t = WED_10
    items = [{"kind": "overspeed", "start": t, "lat": 1, "lon": 2, "severity": "critical", "max_speed_kmh": 104},
             {"kind": "geofence", "event": "dwell", "at": t, "lat": 1, "lon": 2, "geofence_id": 5, "dwell_s": 1800},
             {"kind": "behavior", "type": "crash", "at": t, "lat": 1, "lon": 2},
             {"kind": "behavior", "type": "harsh_braking", "at": t, "lat": 1, "lon": 2},
             {"kind": "behavior", "type": "device_overspeed", "at": t, "lat": 1, "lon": 2},
             {"kind": "idle", "start": t, "lat": 1, "lon": 2, "duration_s": 1200},
             {"kind": "trip", "start": t}]
    sigs = signals_from_items(items, delayed=True)
    assert [(s.type, s.severity) for s in sigs] == [("overspeed", "critical"), ("geofence_dwell", None),
                                                    ("crash", "critical"), ("harsh_driving", None), ("long_idle", None)]
    assert sigs[1].value == 30 and sigs[4].value == 20 and all(s.delayed for s in sigs)


def test_rule_changes_take_effect_at_once():
    """The web app's change event clears the rule cache as well as the geofence cache."""
    import time as _time

    from gpsproc.app import Processor, ProcessorConfig
    proc = Processor(ProcessorConfig(database_url="postgresql://unused", redis_url="redis://unused"))
    proc.alerts._rules[7] = (_time.monotonic() + 30, [])
    proc.alerts._rules[8] = (_time.monotonic() + 30, [])
    proc.analysis._fences[7] = (_time.monotonic() + 60, [])
    proc.on_config_changed('{"organization_id": 7}')
    assert 7 not in proc.alerts._rules and 7 not in proc.analysis._fences and 8 in proc.alerts._rules
    proc.on_config_changed("not json")
    assert proc.alerts._rules == {}
