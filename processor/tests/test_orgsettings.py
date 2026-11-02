"""Per-organization settings mapping and the webhook outbox filter (spec A10, A13)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from gpsproc import orgsettings
from gpsproc.alerts import Rule, Signal, channel_allowed, rule_matches
from gpsproc.analysis.engine import Config
from gpsproc.outbox import Outbox
from gpsproc.status import Thresholds


def test_values_override_only_what_is_set() -> None:
    base = Config(min_stop_s=180, overspeed_warning_kmh=80.0)
    cfg = orgsettings.analysis(base, {"min_stop_minutes": 5, "overspeed_warning_kmh": 90, "unknown": 1})
    assert (cfg.min_stop_s, cfg.overspeed_warning_kmh, cfg.idle_s) == (300, 90.0, base.idle_s)
    assert isinstance(cfg.min_stop_s, int)
    assert orgsettings.analysis(base, {}) is base
    t = orgsettings.thresholds(Thresholds(), {"offline_minutes": 15})
    assert t.offline_after == timedelta(minutes=15) and t.no_gps_after == Thresholds().no_gps_after


def test_org_defaults_reach_alert_rules() -> None:
    rule = Rule(id=1, type="long_stop", severity="info", params={}, applies_to_all=True, vehicles=set(), groups=set(),
                schedule={}, recipient_roles=[], recipient_users=set(), cooldown=timedelta(0))
    sig = Signal("long_stop", datetime.now(UTC), value=60)
    assert not rule_matches(rule, sig, 1, set(), True)[0]                         # default 120 min
    assert rule_matches(rule, sig, 1, set(), True, defaults={"long_stop_minutes": 45})[0]
    assert not channel_allowed({}, "email", "crash", "warning")                  # default: critical only
    assert channel_allowed({}, "email", "crash", "warning", email_min="warning")


class FakeConn:
    def __init__(self, subscribed: list[str]) -> None:
        self.subscribed, self.inserted = subscribed, []

    async def fetchval(self, sql: str, *args: object) -> list[str]:
        return self.subscribed

    async def execute(self, sql: str, *args: object) -> str:
        self.inserted.append(args)
        return "INSERT 0 1"


def test_outbox_writes_only_subscribed_events() -> None:
    conn, box = FakeConn(["trip.completed"]), Outbox()
    now = datetime.now(UTC)
    assert asyncio.run(box.emit(conn, 1, "trip.completed", "k", {"at": now}, now))
    assert not asyncio.run(box.emit(conn, 1, "device.offline", "k2", {}, now))
    assert len(conn.inserted) == 1 and box.emitted == 1
