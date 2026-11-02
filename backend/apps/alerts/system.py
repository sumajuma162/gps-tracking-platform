"""Alerts raised by scheduled checks in the web app (maintenance due, SIM expiry; spec A11).

Signal-driven alerts are raised by the processor (``processor/gpsproc/alerts.py``), which has no Django runtime.
These two types come from the calendar and service history instead, so they are evaluated by Celery here, with the
same rules semantics: only active rules of the type raise alerts, scope by vehicle/group, one open alert per rule,
vehicle and type (repeats update the counter), per-rule cooldown, recipients by role or user, per-user channel
preferences and quiet hours (critical bypasses them). See docs/DECISIONS.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.alerts.models import Alert, AlertRule, Notification, NotificationPreference

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}

MESSAGES = {
    "en": {"maintenance_due": "{vehicle}: {task} {state}", "sim_expiry": "SIM {sim} {state}",
           "due_soon": "due soon", "overdue": "overdue", "expires_soon": "expires on {date}",
           "expired": "expired on {date}"},
    "sw": {"maintenance_due": "{vehicle}: {task} {state}", "sim_expiry": "SIM {sim} {state}",
           "due_soon": "inakaribia kufanyika", "overdue": "imepitwa na muda", "expires_soon": "inaisha {date}",
           "expired": "imeisha {date}"},
}


def render(lang: str, kind: str, params: dict[str, Any]) -> str:
    table = MESSAGES.get(lang) or MESSAGES["en"]
    p = {**params, "state": table.get(params.get("state", ""), params.get("state", "")).format(**params)}
    return table[kind].format(**p)


def _in_schedule(schedule: dict[str, Any], at: datetime, tz: str) -> bool:
    if not schedule:
        return True
    local = at.astimezone(ZoneInfo(tz))
    days = schedule.get("days")
    return days is None or local.weekday() in days


def _quiet(start: dtime | None, end: dtime | None, at: datetime, tz: str) -> bool:
    if start is None or end is None or start == end:
        return False
    now = at.astimezone(ZoneInfo(tz or "Africa/Dar_es_Salaam")).time()
    return start <= now < end if start < end else now >= start or now < end


def _allowed(prefs: dict[tuple[str, str], tuple[bool, str]], channel: str, kind: str, severity: str,
             email_min: str) -> bool:
    enabled, minimum = prefs.get((channel, kind)) or prefs.get((channel, "")) or (
        (True, "info") if channel == "in_app" else (True, email_min))
    return enabled and SEVERITY_RANK[severity] >= SEVERITY_RANK[minimum]


def rules_for(kind: str, vehicle: Any) -> list[AlertRule]:
    """Active rules of this type whose scope includes the vehicle (or all vehicles when there is none)."""
    rules = AlertRule.objects.filter(type=kind, is_active=True).prefetch_related("vehicles", "groups")
    out = []
    groups = set(vehicle.groups.values_list("id", flat=True)) if vehicle is not None else set()
    for rule in rules:
        if rule.applies_to_all or (vehicle is not None and (
                any(v.pk == vehicle.pk for v in rule.vehicles.all()) or groups & {g.pk for g in rule.groups.all()})):
            out.append(rule)
    return out


def raise_alert(*, organization: Any, kind: str, subject: str, params: dict[str, Any], severity: str | None = None,
                vehicle: Any = None, device: Any = None, now: datetime | None = None) -> int:
    """Create or update alerts of ``kind`` for every matching rule and notify. Must run in the organization's scope.
    ``subject`` identifies what the alert is about (e.g. ``sim:12``) for deduplication when there is no vehicle.
    Returns how many alerts were created or updated."""
    from apps.settings_app.models import effective

    now = now or timezone.now()
    touched = 0
    for rule in rules_for(kind, vehicle):
        if not _in_schedule(rule.schedule, now, organization.timezone):
            continue
        sev = severity if severity and SEVERITY_RANK[severity] > SEVERITY_RANK[rule.severity] else rule.severity
        full = {**params, "subject": subject}
        with transaction.atomic():
            open_alert = (Alert.objects.select_for_update()
                          .filter(rule=rule, type=kind, vehicle=vehicle).exclude(status="resolved")
                          .filter(Q(vehicle__isnull=False) | Q(message_params__subject=subject)).first())
            if open_alert is None:
                alert = Alert.objects.create(organization=organization, rule=rule, vehicle=vehicle, device=device,
                                             type=kind, severity=sev, message=render("en", kind, full)[:300],
                                             message_params=full, occurred_at=now, last_occurred_at=now)
            else:
                alert = open_alert
                alert.occurrences += 1
                alert.last_occurred_at = now
                alert.message, alert.message_params = render("en", kind, full)[:300], full
                if SEVERITY_RANK[sev] > SEVERITY_RANK[alert.severity]:
                    alert.severity = sev
                alert.save(update_fields=["occurrences", "last_occurred_at", "message", "message_params", "severity"])
            touched += 1
            if alert.last_notified_at and now - alert.last_notified_at < timedelta(minutes=rule.cooldown_minutes):
                continue
            Alert.objects.filter(pk=alert.pk).update(last_notified_at=now)
            _notify(organization, alert, rule, kind, alert.severity, full, now,
                    effective(organization.pk)["email_min_severity"])
    _emit_webhook(organization, touched, kind, subject, params)
    return touched


def _emit_webhook(organization: Any, touched: int, kind: str, subject: str, params: dict[str, Any]) -> None:
    if not touched:
        return
    from apps.integrations.events import emit

    emit(organization.pk, "alert.created", f"system:{kind}:{subject}:{timezone.now():%Y%m%d%H}",
         {"type": kind, "subject": subject, **params})


def _notify(organization: Any, alert: Alert, rule: AlertRule, kind: str, severity: str, params: dict[str, Any],
            now: datetime, email_min: str) -> None:
    from apps.organizations.models import OrganizationMembership

    members = (OrganizationMembership.objects.filter(is_active=True, user__is_active=True)
               .filter(Q(role__slug__in=rule.recipient_roles or []) | Q(user__in=rule.recipient_users.all()))
               .select_related("user").distinct())
    users = [m.user for m in members]
    prefs: dict[int, dict[tuple[str, str], tuple[bool, str]]] = {}
    for p in NotificationPreference.objects.filter(user__in=users):
        prefs.setdefault(p.user_id, {})[(p.channel, p.alert_type)] = (p.enabled, p.min_severity)
    pushes = []
    for u in users:
        text = render(u.language, kind, params)
        quiet = severity != "critical" and _quiet(u.quiet_hours_start, u.quiet_hours_end, now, u.time_zone)
        for channel in ("in_app", "email"):
            if not _allowed(prefs.get(u.pk, {}), channel, kind, severity, email_min):
                continue
            if channel == "email" and not u.email:
                continue
            status = "suppressed" if quiet else "sent" if channel == "in_app" else "queued"
            n = Notification.objects.create(
                organization=organization, user=u, alert=alert, channel=channel, title=text[:200], body=text,
                severity=severity, status=status, reason="quiet_hours" if quiet else "",
                sent_at=now if status == "sent" else None, next_attempt_at=now if status == "queued" else None)
            if status == "sent" and channel == "in_app":
                pushes.append((u.pk, {"id": n.pk, "title": n.title, "severity": severity, "alert_id": alert.pk,
                                      "type": kind, "created_at": now.isoformat()}))
    if pushes:
        transaction.on_commit(lambda: _push(pushes))


def _push(pushes: list[tuple[int, dict[str, Any]]]) -> None:
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if layer is None:
        return
    for user_id, payload in pushes:
        async_to_sync(layer.group_send)(f"notify.user.{user_id}", {"type": "notify", "notification": payload})


def resolve(kind: str, *, subject: str, vehicle: Any = None, note: str) -> int:
    """System resolution when the condition clears (service done, SIM renewed)."""
    qs = Alert.objects.filter(type=kind).exclude(status="resolved")
    qs = qs.filter(vehicle=vehicle) if vehicle is not None else qs.filter(message_params__subject=subject)
    return qs.update(status="resolved", resolved_at=timezone.now(), notes=note)
