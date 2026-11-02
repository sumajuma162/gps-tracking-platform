"""Alert engine (spec A11): signals -> alerts with deduplication, cooldown, delayed flag, recipients, preferences
and quiet hours; in-app delivery is pushed at once, email is queued for the web app's Celery worker.

The rule-matching and notification decisions are pure functions (unit-tested); ``AlertEngine`` applies them to the
database in the caller's transaction.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("gpsproc.alerts")
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
BEHAVIOR_ALERTS = {"crash": "crash", "towing": "towing", "gsm_jamming": "gsm_jamming",
                   "power_disconnected": "power_disconnected", "harsh_acceleration": "harsh_driving",
                   "harsh_braking": "harsh_driving", "harsh_cornering": "harsh_driving"}
DEFAULT_TZ = "Africa/Dar_es_Salaam"

MESSAGES = {
    "en": {
        "overspeed": "{vehicle} exceeded {limit} km/h (top {value} km/h)",
        "geofence_enter": "{vehicle} entered {geofence}", "geofence_exit": "{vehicle} left {geofence}",
        "geofence_dwell": "{vehicle} has stayed in {geofence} for {minutes} min",
        "device_offline": "{vehicle}: tracker offline", "gps_lost": "{vehicle}: GPS signal lost",
        "unauthorized_movement": "{vehicle} moved {reason}", "ignition_on": "{vehicle}: ignition on",
        "ignition_off": "{vehicle}: ignition off", "low_battery": "{vehicle}: tracker battery low ({value} V)",
        "power_disconnected": "{vehicle}: external power disconnected", "gsm_jamming": "{vehicle}: GSM jamming",
        "towing": "{vehicle}: towing detected", "crash": "{vehicle}: possible crash",
        "harsh_driving": "{vehicle}: harsh driving ({detail})", "long_idle": "{vehicle} idled for {minutes} min",
        "long_stop": "{vehicle} stopped for {minutes} min", "tampering": "{vehicle}: tracker tampering",
        "outside_hours": "outside allowed hours", "no_driver": "without an assigned driver",
    },
    "sw": {
        "overspeed": "{vehicle} imezidi km/h {limit} (juu kabisa km/h {value})",
        "geofence_enter": "{vehicle} imeingia {geofence}", "geofence_exit": "{vehicle} imetoka {geofence}",
        "geofence_dwell": "{vehicle} imekaa {geofence} kwa dakika {minutes}",
        "device_offline": "{vehicle}: kifaa hakiko hewani", "gps_lost": "{vehicle}: mawimbi ya GPS yamepotea",
        "unauthorized_movement": "{vehicle} imetembea {reason}", "ignition_on": "{vehicle}: injini imewashwa",
        "ignition_off": "{vehicle}: injini imezimwa", "low_battery": "{vehicle}: betri ya kifaa iko chini ({value} V)",
        "power_disconnected": "{vehicle}: umeme wa gari umekatwa", "gsm_jamming": "{vehicle}: mawimbi ya GSM yanazuiwa",
        "towing": "{vehicle}: inavutwa", "crash": "{vehicle}: huenda imepata ajali",
        "harsh_driving": "{vehicle}: uendeshaji mkali ({detail})",
        "long_idle": "{vehicle} imewaka bila kutembea dakika {minutes}",
        "long_stop": "{vehicle} imesimama dakika {minutes}", "tampering": "{vehicle}: kifaa kimechezewa",
        "outside_hours": "nje ya saa zinazoruhusiwa", "no_driver": "bila dereva aliyepangwa",
    },
}


def render(lang: str, kind: str, params: dict[str, Any]) -> str:
    table = MESSAGES.get(lang) or MESSAGES["en"]
    p = dict(params)
    if "reason" in p:
        p["reason"] = table.get(p["reason"], p["reason"])
    try:
        return table.get(kind, kind).format(**p)
    except (KeyError, IndexError):
        return MESSAGES["en"].get(kind, kind).format_map(_Default(p))


class _Default(dict):  # type: ignore[type-arg]
    def __missing__(self, key: str) -> str:
        return "?"


@dataclass(frozen=True)
class Signal:
    type: str
    at: datetime
    lat: float | None = None
    lon: float | None = None
    severity: str | None = None             # the source's own severity (e.g. a critical overspeed)
    value: float | None = None              # top speed, voltage, minutes...
    geofence_id: int | None = None
    detail: str = ""
    delayed: bool = False


@dataclass
class Rule:
    id: int
    type: str
    severity: str
    params: dict[str, Any]
    applies_to_all: bool
    vehicles: set[int]
    groups: set[int]
    schedule: dict[str, Any]
    recipient_roles: list[str]
    recipient_users: set[int]
    cooldown: timedelta
    name: str = ""


def in_schedule(schedule: dict[str, Any], at: datetime, tz: str = DEFAULT_TZ) -> bool:
    """``{"days": [0..6], "from": "HH:MM", "to": "HH:MM"}`` in local time; windows may cross midnight.
    An empty schedule means always."""
    if not schedule:
        return True
    local = at.astimezone(ZoneInfo(tz))
    days = schedule.get("days")
    start, end = schedule.get("from"), schedule.get("to")
    if not (start and end):
        return days is None or local.weekday() in days
    a, b = dtime.fromisoformat(start), dtime.fromisoformat(end)
    now = local.time()
    if a <= b:
        return (days is None or local.weekday() in days) and a <= now < b
    # Overnight window (e.g. 18:00-06:00): after midnight belongs to the previous day's window.
    if now >= a:
        return days is None or local.weekday() in days
    return now < b and (days is None or (local - timedelta(days=1)).weekday() in days)


def rule_matches(rule: Rule, sig: Signal, vehicle_id: int | None, vehicle_groups: set[int],
                 has_driver: bool, tz: str = DEFAULT_TZ,
                 defaults: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    """Whether a rule raises an alert for this signal, plus extra message parameters. ``defaults`` are the
    organization's settings (e.g. ``long_stop_minutes`` when the rule doesn't set its own)."""
    p = rule.params
    defaults = defaults or {}
    scoped = rule.applies_to_all or (vehicle_id is not None and (vehicle_id in rule.vehicles
                                                                  or bool(vehicle_groups & rule.groups)))
    if not scoped:
        return False, {}
    if rule.type == "unauthorized_movement":
        if sig.type != "ignition_on":
            return False, {}
        if p.get("require_driver") and not has_driver:
            return True, {"reason": "no_driver"}
        if rule.schedule and not in_schedule(rule.schedule, sig.at, tz):
            return True, {"reason": "outside_hours"}      # the schedule here is the *allowed* time
        return False, {}
    if sig.type != rule.type or not in_schedule(rule.schedule, sig.at, tz):
        return False, {}
    if rule.type == "overspeed" and p.get("limit_kmh") and (sig.value or 0) <= float(p["limit_kmh"]):
        return False, {}
    if rule.type.startswith("geofence_") and p.get("geofences") and sig.geofence_id not in p["geofences"]:
        return False, {}
    if rule.type in ("long_idle", "long_stop"):
        fallback = 15 if rule.type == "long_idle" else defaults.get("long_stop_minutes", 120)
        minimum = float(p.get("minutes", fallback))
        if (sig.value or 0) < minimum:
            return False, {}
    if rule.type == "low_battery" and (sig.value is None or sig.value >= float(p.get("volts", 3.6))):
        return False, {}
    return True, {}


def severity_for(rule: Rule, sig: Signal) -> str:
    """The rule's severity, raised to the source's own if that is higher (a critical overspeed stays critical)."""
    if sig.severity and SEVERITY_RANK.get(sig.severity, 0) > SEVERITY_RANK[rule.severity]:
        return sig.severity
    return rule.severity


def in_quiet_hours(start: dtime | None, end: dtime | None, at: datetime, tz: str) -> bool:
    if start is None or end is None or start == end:
        return False
    now = at.astimezone(ZoneInfo(tz or DEFAULT_TZ)).time()
    return start <= now < end if start < end else now >= start or now < end


def channel_allowed(prefs: dict[tuple[str, str], tuple[bool, str]], channel: str, alert_type: str,
                    severity: str, email_min: str = "critical") -> bool:
    """User preference for this channel and type ('' = all types). Defaults: in-app everything, email from the
    organization's ``email_min_severity`` (critical unless changed)."""
    enabled, minimum = prefs.get((channel, alert_type)) or prefs.get((channel, "")) or (
        (True, "info") if channel == "in_app" else (True, email_min) if channel == "email" else (False, "info"))
    return enabled and SEVERITY_RANK[severity] >= SEVERITY_RANK[minimum]


# --- database side --------------------------------------------------------------------------------------

RULES_SQL = """
SELECT r.id, r.name, r.type, r.severity, r.params, r.applies_to_all, r.schedule, r.recipient_roles,
       r.cooldown_minutes,
       coalesce((SELECT array_agg(vehicle_id) FROM alert_rule_vehicles WHERE alertrule_id = r.id), '{}') AS vehicles,
       coalesce((SELECT array_agg(vehiclegroup_id) FROM alert_rule_groups WHERE alertrule_id = r.id), '{}') AS groups,
       coalesce((SELECT array_agg(user_id) FROM alert_rule_recipients WHERE alertrule_id = r.id), '{}') AS users
FROM alert_rules r WHERE r.organization_id = $1 AND r.is_active
"""

UPSERT_ALERT = """
INSERT INTO alerts (organization_id, rule_id, vehicle_id, device_id, driver_id, type, severity, message, message_params,
                    lat, lon, occurred_at, last_occurred_at, occurrences, delayed, status, notes, created_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$12,1,$13,'new','',now())
ON CONFLICT (rule_id, vehicle_id, type) WHERE status <> 'resolved' DO UPDATE SET
    occurrences = alerts.occurrences + 1,
    last_occurred_at = GREATEST(alerts.last_occurred_at, EXCLUDED.last_occurred_at),
    severity = CASE WHEN EXCLUDED.severity = 'critical' THEN 'critical' ELSE alerts.severity END,
    lat = EXCLUDED.lat, lon = EXCLUDED.lon, message = EXCLUDED.message, message_params = EXCLUDED.message_params
RETURNING id, last_notified_at, (xmax = 0) AS created
"""

RECIPIENTS_SQL = """
SELECT u.id, u.language, u.time_zone, u.quiet_hours_start, u.quiet_hours_end, u.email
FROM organization_users m JOIN users u ON u.id = m.user_id JOIN roles ro ON ro.id = m.role_id
WHERE m.organization_id = $1 AND m.is_active AND u.is_active AND (ro.slug = ANY($2::text[]) OR u.id = ANY($3::bigint[]))
"""


@dataclass
class AlertEngine:
    rules_ttl: float = 30.0
    layer: Any = None
    settings: Any = None        # gpsproc.orgsettings.OrgSettings
    outbox: Any = None          # gpsproc.outbox.Outbox
    _rules: dict[int, tuple[float, list[Rule]]] = field(default_factory=dict)

    async def rules(self, conn: Any, organization_id: int) -> list[Rule]:
        cached = self._rules.get(organization_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        rules = []
        for r in await conn.fetch(RULES_SQL, organization_id):
            rules.append(Rule(id=r["id"], name=r["name"], type=r["type"], severity=r["severity"],
                              params=_json(r["params"]) or {}, applies_to_all=r["applies_to_all"],
                              vehicles=set(r["vehicles"] or []), groups=set(r["groups"] or []),
                              schedule=_json(r["schedule"]) or {},
                              recipient_roles=list(_json(r["recipient_roles"]) or []),
                              recipient_users=set(r["users"] or []),
                              cooldown=timedelta(minutes=r["cooldown_minutes"])))
        self._rules[organization_id] = (time.monotonic() + self.rules_ttl, rules)
        return rules

    def invalidate(self, organization_id: int | None = None) -> None:
        if organization_id is None:
            self._rules.clear()
        else:
            self._rules.pop(organization_id, None)

    async def evaluate(self, conn: Any, *, organization_id: int, vehicle_id: int | None, device_id: int,
                       driver_id: int | None, signals: list[Signal], now: datetime) -> int:
        """Create or update alerts for these signals and deliver notifications. Returns alerts touched."""
        if not signals:
            return 0
        rules = await self.rules(conn, organization_id)
        if not rules:
            return 0
        vehicle = await conn.fetchrow("SELECT registration_number, (SELECT coalesce(array_agg(vehiclegroup_id), '{}') "
                                      "FROM vehicle_group_members WHERE vehicle_id = v.id) AS groups FROM vehicles v "
                                      "WHERE v.id = $1", vehicle_id) if vehicle_id else None
        label = vehicle["registration_number"] if vehicle else f"tracker {device_id}"
        groups = set(vehicle["groups"] or []) if vehicle else set()
        org_values = await self.settings.values(conn, organization_id) if self.settings is not None else {}
        touched = 0
        for sig in signals:
            for rule in rules:
                ok, extra = rule_matches(rule, sig, vehicle_id, groups, driver_id is not None, defaults=org_values)
                if not ok:
                    continue
                kind = rule.type if rule.type == "unauthorized_movement" else sig.type
                params = {"vehicle": label, "value": round(sig.value, 1) if sig.value is not None else None,
                          "limit": rule.params.get("limit_kmh", 80), "minutes": int(sig.value or 0),
                          "geofence": await self._fence_name(conn, sig.geofence_id), "detail": sig.detail, **extra}
                severity = severity_for(rule, sig)
                row = await conn.fetchrow(UPSERT_ALERT, organization_id, rule.id, vehicle_id, device_id, driver_id,
                                          kind, severity, render("en", kind, params)[:300], json.dumps(params), sig.lat,
                                          sig.lon, sig.at, sig.delayed)
                touched += 1
                if row["created"] and self.outbox is not None:
                    await self.outbox.emit(conn, organization_id, "alert.created", f"alert:{row['id']}", {
                        "alert_id": row["id"], "type": kind, "severity": severity, "vehicle_id": vehicle_id,
                        "vehicle": label, "device_id": device_id, "driver_id": driver_id,
                        "message": render("en", kind, params), "position": [sig.lat, sig.lon],
                        "occurred_at": sig.at, "delayed": sig.delayed}, sig.at)
                if sig.delayed:
                    continue                    # backlog: stored with a "delayed" label, never pushed as live
                last = row["last_notified_at"]
                if last is not None and now - last < rule.cooldown:
                    continue                    # cooldown: the alert is updated, nobody is notified again yet
                await conn.execute("UPDATE alerts SET last_notified_at = $2 WHERE id = $1", row["id"], now)
                await self._notify(conn, organization_id, row["id"], rule, kind, severity, params, now,
                                   str(org_values.get("email_min_severity", "critical")))
        return touched

    async def _fence_name(self, conn: Any, geofence_id: int | None) -> str:
        if geofence_id is None:
            return ""
        return await conn.fetchval("SELECT name FROM geofences WHERE id = $1", geofence_id) or ""

    async def _notify(self, conn: Any, organization_id: int, alert_id: int, rule: Rule, kind: str, severity: str,
                      params: dict[str, Any], now: datetime, email_min: str = "critical") -> None:
        users = await conn.fetch(RECIPIENTS_SQL, organization_id, rule.recipient_roles, list(rule.recipient_users))
        if not users:
            return
        prefs: dict[int, dict[tuple[str, str], tuple[bool, str]]] = {}
        rows = await conn.fetch("SELECT user_id, channel, alert_type, enabled, min_severity FROM "
                                "notification_preferences WHERE organization_id = $1 AND user_id = ANY($2::bigint[])",
                                organization_id, [u["id"] for u in users])
        for p in rows:
            prefs.setdefault(p["user_id"], {})[(p["channel"], p["alert_type"])] = (p["enabled"], p["min_severity"])
        for u in users:
            text = render(u["language"], kind, params)
            quiet = severity != "critical" and in_quiet_hours(u["quiet_hours_start"], u["quiet_hours_end"], now,
                                                               u["time_zone"])
            for channel in ("in_app", "email"):
                if not channel_allowed(prefs.get(u["id"], {}), channel, kind, severity, email_min):
                    continue
                if channel == "email" and not u["email"]:
                    continue
                status = "suppressed" if quiet else "sent" if channel == "in_app" else "queued"
                nid = await conn.fetchval(
                    "INSERT INTO notifications (organization_id, user_id, alert_id, channel, title, body, severity, "
                    "status, reason, attempts, error, sent_at, next_attempt_at, created_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,0,'',$10,$11,now()) RETURNING id",
                    organization_id, u["id"], alert_id, channel, text[:200], text, severity, status,
                    "quiet_hours" if quiet else "", now if status == "sent" else None,
                    now if status == "queued" else None)
                if channel == "in_app" and status == "sent" and self.layer is not None:
                    await self.layer.group_send(f"notify.user.{u['id']}", {"type": "notify", "notification": {
                        "id": nid, "title": text[:200], "severity": severity, "alert_id": alert_id, "type": kind,
                        "created_at": now.isoformat()}})

    async def resolve(self, conn: Any, device_id: int, kind: str, note: str) -> int:
        """System resolution when the condition clears (device back online, GPS restored)."""
        status = await conn.execute(
            "UPDATE alerts SET status = 'resolved', resolved_at = now(), notes = trim(both from notes || ' ' || $3) "
            "WHERE device_id = $1 AND type = $2 AND status <> 'resolved'", device_id, kind, note)
        return int(status.rsplit(" ", 1)[-1])


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def signals_from_items(items: list[dict[str, Any]], delayed: bool = False) -> list[Signal]:
    """Analysis results that can raise alerts."""
    out: list[Signal] = []
    for it in items:
        k = it["kind"]
        if k == "overspeed":
            out.append(Signal("overspeed", it["start"], it["lat"], it["lon"], it["severity"], it["max_speed_kmh"],
                              delayed=delayed))
        elif k == "geofence":
            out.append(Signal(f"geofence_{it['event']}", it["at"], it["lat"], it["lon"], geofence_id=it["geofence_id"],
                              value=(it.get("dwell_s") or 0) / 60, delayed=delayed))
        elif k == "behavior" and it["type"] in BEHAVIOR_ALERTS:
            sev = "critical" if it["type"] in ("crash", "towing") else None
            out.append(Signal(BEHAVIOR_ALERTS[it["type"]], it["at"], it["lat"], it["lon"], sev,
                              detail=it["type"].replace("_", " "), delayed=delayed))
        elif k == "idle":
            out.append(Signal("long_idle", it["start"], it["lat"], it["lon"], value=it["duration_s"] / 60,
                              delayed=delayed))
        elif k == "stop":
            out.append(Signal("long_stop", it["start"], it["lat"], it["lon"], value=it["duration_s"] / 60,
                              delayed=delayed))
    return out
