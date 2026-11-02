"""Due status of maintenance schedules and the periodic maintenance / SIM-expiry check (spec A11, A13).

Distance and engine hours since the last service come from detected trips (distance from the odometer or the
drift-filtered GPS track; engine hours = time with the ignition on during trips, an approximation documented in
docs/DECISIONS.md), so no absolute odometer reading is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import F, Sum
from django.utils import timezone

from apps.alerts import system
from apps.analysis.models import Trip
from apps.maintenance.models import MaintenanceSchedule

RANK = {"ok": 0, "due_soon": 1, "overdue": 2}


@dataclass(frozen=True)
class DueStatus:
    state: str
    km_since: float
    hours_since: float
    days_since: float
    km_left: float | None
    hours_left: float | None
    days_left: float | None

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def usage_since(vehicle_id: int, since: datetime) -> tuple[float, float]:
    agg = Trip.objects.filter(vehicle_id=vehicle_id, start_time__gte=since).aggregate(
        km=Sum("distance_km"), driving=Sum("driving_s"), idle=Sum("idle_s"))
    return float(agg["km"] or 0), ((agg["driving"] or 0) + (agg["idle"] or 0)) / 3600


def due_status(s: MaintenanceSchedule, now: datetime | None = None) -> DueStatus:
    now = now or timezone.now()
    km, hours = usage_since(s.vehicle_id, s.last_service_at)
    days = (now - s.last_service_at).total_seconds() / 86400
    checks = []
    left: dict[str, float | None] = {"km": None, "hours": None, "days": None}
    for name, interval, used, soon in (("km", s.interval_km, km, s.due_soon_km),
                                       ("hours", s.interval_engine_hours, hours, s.due_soon_engine_hours),
                                       ("days", s.interval_days, days, s.due_soon_days)):
        if interval is None:
            continue
        remaining = interval - used
        left[name] = remaining
        checks.append("overdue" if remaining <= 0 else "due_soon" if remaining <= soon else "ok")
    state = max(checks, key=RANK.__getitem__) if checks else "ok"
    return DueStatus(state, km, hours, days, left["km"], left["hours"], left["days"])


def check_schedules(organization: Any, now: datetime | None = None) -> int:
    """Raise ``maintenance_due`` alerts when a schedule becomes due soon or overdue. Runs in the org's scope."""
    now = now or timezone.now()
    raised = 0
    for s in MaintenanceSchedule.objects.filter(is_active=True, vehicle__deleted_at__isnull=True)\
            .select_related("vehicle"):
        status = due_status(s, now)
        if status.state != s.alert_state:
            if RANK[status.state] > RANK[s.alert_state]:
                raised += system.raise_alert(
                    organization=organization, kind="maintenance_due", subject=f"maintenance:{s.pk}",
                    vehicle=s.vehicle, now=now, severity="warning" if status.state == "overdue" else None,
                    params={"vehicle": s.vehicle.registration_number, "task": s.name, "state": status.state,
                            "schedule_id": s.pk, **status.as_dict()})
            MaintenanceSchedule.objects.filter(pk=s.pk).update(alert_state=status.state)
    return raised


def check_sims(organization: Any, now: datetime | None = None) -> int:
    """Raise ``sim_expiry`` alerts ``params.days`` (default 14) before a SIM's expiry date, once per date."""
    from apps.alerts.models import AlertRule
    from apps.fleet.models import SimCard

    now = now or timezone.now()
    rules = list(AlertRule.objects.filter(type="sim_expiry", is_active=True))
    if not rules:
        return 0
    horizon = max(int(r.params.get("days", 14)) for r in rules)
    today = timezone.localdate(now)
    raised = 0
    qs = SimCard.objects.filter(deleted_at__isnull=True, status="active", expiry_date__isnull=False,
                                expiry_date__lte=today + timedelta(days=horizon))
    for sim in qs.exclude(expiry_notified_for=F("expiry_date")):
        device = sim.devices.filter(deleted_at__isnull=True).first()
        vehicle = None
        if device is not None:
            from apps.devices.models import DeviceAssignment
            a = DeviceAssignment.objects.filter(device=device, unassigned_at__isnull=True).select_related(
                "vehicle").first()
            vehicle = a.vehicle if a else None
        state = "expired" if sim.expiry_date <= today else "expires_soon"
        raised += system.raise_alert(
            organization=organization, kind="sim_expiry", subject=f"sim:{sim.pk}", vehicle=vehicle, device=device,
            now=now, severity="warning" if state == "expired" else None,
            params={"sim": sim.phone_number, "date": sim.expiry_date.isoformat(), "state": state, "sim_id": sim.pk,
                    "vehicle": vehicle.registration_number if vehicle else ""})
        SimCard.objects.filter(pk=sim.pk).update(expiry_notified_for=sim.expiry_date)
    return raised


def after_service(record: Any) -> None:
    """A service resets its schedule and clears the vehicle's maintenance alert once nothing else is due."""
    if record.schedule_id:
        s = record.schedule
        if record.performed_at > s.last_service_at:
            s.last_service_at = record.performed_at
        s.alert_state = "ok"
        s.save(update_fields=["last_service_at", "alert_state", "updated_at"])
    others = MaintenanceSchedule.objects.filter(vehicle_id=record.vehicle_id, is_active=True)\
        .exclude(alert_state="ok")
    if not others.exists():
        system.resolve("maintenance_due", subject="", vehicle=record.vehicle,
                       note="Resolved automatically: service recorded.")
