"""Report definitions (spec A13): each report is a query returning rows plus typed columns. Runs inside a tenant
context, so every query is scoped to one organization (and RLS applies)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count, F, Max, Min, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.alerts.models import Alert
from apps.analysis.models import DrivingBehaviorEvent, IdleEvent, SpeedEvent, Stop, Trip
from apps.fleet.models import Vehicle
from apps.geofences.models import GeofenceEvent


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: str = "text"          # text | datetime | date | number | duration | km | kmh | percent


@dataclass(frozen=True)
class Filters:
    start: datetime
    end: datetime
    vehicle: int | None = None
    group: int | None = None
    driver: int | None = None
    status: str | None = None

    def vehicles(self) -> Q:
        q = Q()
        if self.vehicle:
            q &= Q(vehicle_id=self.vehicle)
        if self.group:
            q &= Q(vehicle__groups__id=self.group)
        if self.driver:
            q &= Q(driver_id=self.driver)
        return q


@dataclass(frozen=True)
class Report:
    key: str
    title: str
    columns: tuple[Column, ...]
    rows: Callable[[Filters], list[dict[str, Any]]]
    chart: tuple[str, str] | None = None          # (label column, value column) for a bar chart
    filters: tuple[str, ...] = ("vehicle", "group", "start", "end")
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _in_range(qs: Any, f: Filters, start: str = "start_time", end: str | None = None) -> Any:
    return qs.filter(**{f"{start}__gte": f.start, f"{start}__lt": f.end}).filter(f.vehicles())


def _reg(v: Any) -> str:
    return v.registration_number if v else ""


def trips(f: Filters) -> list[dict[str, Any]]:
    qs = _in_range(Trip.objects.select_related("vehicle", "driver"), f).order_by("start_time")
    return [{"vehicle": _reg(t.vehicle), "driver": t.driver.full_name if t.driver else "", "start": t.start_time,
             "end": t.end_time, "duration": t.duration_s, "distance": t.distance_km, "max_speed": t.max_speed_kmh,
             "avg_speed": t.avg_moving_speed_kmh, "source": t.distance_source, "delayed": "yes" if t.delayed else ""}
            for t in qs]


def daily_movement(f: Filters) -> list[dict[str, Any]]:
    tz = timezone.get_current_timezone()
    qs = (_in_range(Trip.objects, f).annotate(day=TruncDate("start_time", tzinfo=tz))
          .values("day", "vehicle__registration_number")
          .annotate(trips=Count("id"), distance=Sum("distance_km"), driving=Sum("driving_s"), idle=Sum("idle_s"),
                    first=Min("start_time"), last=Max("end_time"), top=Max("max_speed_kmh"))
          .order_by("day", "vehicle__registration_number"))
    return [{"day": r["day"], "vehicle": r["vehicle__registration_number"] or "", "trips": r["trips"],
             "distance": r["distance"] or 0, "driving": r["driving"] or 0, "idle": r["idle"] or 0, "first": r["first"],
             "last": r["last"], "max_speed": r["top"] or 0} for r in qs]


def _per_vehicle(f: Filters) -> dict[int, dict[str, Any]]:
    agg = (_in_range(Trip.objects, f).values("vehicle_id")
           .annotate(trips=Count("id"), distance=Sum("distance_km"), driving=Sum("driving_s"), idle=Sum("idle_s"),
                     top=Max("max_speed_kmh"), days=Count(TruncDate("start_time"), distinct=True)))
    return {r["vehicle_id"]: r for r in agg}


def _vehicles(f: Filters) -> list[Vehicle]:
    qs = Vehicle.objects.filter(deleted_at__isnull=True)
    if f.vehicle:
        qs = qs.filter(pk=f.vehicle)
    if f.group:
        qs = qs.filter(groups__id=f.group)
    return list(qs.order_by("registration_number"))


def distance(f: Filters) -> list[dict[str, Any]]:
    per = _per_vehicle(f)
    return [{"vehicle": v.registration_number, "trips": per.get(v.pk, {}).get("trips", 0),
             "distance": per.get(v.pk, {}).get("distance") or 0} for v in _vehicles(f)]


def fleet_summary(f: Filters) -> list[dict[str, Any]]:
    per = _per_vehicle(f)
    alerts = dict(Alert.objects.filter(occurred_at__gte=f.start, occurred_at__lt=f.end).values_list("vehicle_id")
                  .annotate(n=Count("id")))
    speeding = dict(_in_range(SpeedEvent.objects, f).values_list("vehicle_id").annotate(n=Count("id")))
    out = []
    for v in _vehicles(f):
        p = per.get(v.pk, {})
        out.append({"vehicle": v.registration_number, "trips": p.get("trips", 0), "distance": p.get("distance") or 0,
                    "driving": p.get("driving") or 0, "idle": p.get("idle") or 0, "max_speed": p.get("top") or 0,
                    "overspeed": speeding.get(v.pk, 0), "alerts": alerts.get(v.pk, 0)})
    return out


def utilization(f: Filters) -> list[dict[str, Any]]:
    per = _per_vehicle(f)
    period_s = max(1.0, (f.end - f.start).total_seconds())
    days = max(1, round(period_s / 86400))
    return [{"vehicle": v.registration_number, "driving": per.get(v.pk, {}).get("driving") or 0,
             "utilization": round(100 * (per.get(v.pk, {}).get("driving") or 0) / period_s, 2),
             "active_days": per.get(v.pk, {}).get("days", 0), "days": days} for v in _vehicles(f)]


def overspeed(f: Filters) -> list[dict[str, Any]]:
    qs = _in_range(SpeedEvent.objects.select_related("vehicle", "driver"), f).order_by("start_time")
    return [{"vehicle": _reg(e.vehicle), "driver": e.driver.full_name if e.driver else "", "start": e.start_time,
             "duration": e.duration_s, "max_speed": e.max_speed_kmh, "limit": e.threshold_kmh, "severity": e.severity,
             "position": f"{e.lat:.5f}, {e.lon:.5f}"} for e in qs]


def stops(f: Filters) -> list[dict[str, Any]]:
    qs = _in_range(Stop.objects.select_related("vehicle"), f).order_by("start_time")
    return [{"vehicle": _reg(s.vehicle), "start": s.start_time, "end": s.end_time, "duration": s.duration_s,
             "engine_off": "yes" if s.engine_off else "no", "position": f"{s.lat:.5f}, {s.lon:.5f}"} for s in qs]


def idling(f: Filters) -> list[dict[str, Any]]:
    qs = _in_range(IdleEvent.objects.select_related("vehicle", "driver"), f).order_by("start_time")
    return [{"vehicle": _reg(e.vehicle), "driver": e.driver.full_name if e.driver else "", "start": e.start_time,
             "duration": e.duration_s, "position": f"{e.lat:.5f}, {e.lon:.5f}"} for e in qs]


def offline(f: Filters) -> list[dict[str, Any]]:
    qs = _in_range(Alert.objects.filter(type="device_offline").select_related("vehicle"), f,
                   start="occurred_at").order_by("occurred_at")
    out = []
    for a in qs:
        back = a.resolved_at if a.status == "resolved" else None
        out.append({"vehicle": _reg(a.vehicle), "offline_at": a.occurred_at, "back_at": back,
                    "duration": int((back - a.occurred_at).total_seconds()) if back else None})
    return out


def geofence(f: Filters) -> list[dict[str, Any]]:
    qs = _in_range(GeofenceEvent.objects.select_related("vehicle", "geofence"), f, start="occurred_at")
    return [{"vehicle": _reg(e.vehicle), "geofence": e.geofence.name, "event": e.event, "at": e.occurred_at,
             "dwell": e.dwell_s, "delayed": "yes" if e.delayed else ""} for e in qs.order_by("occurred_at")]


def alerts(f: Filters) -> list[dict[str, Any]]:
    qs = _in_range(Alert.objects.select_related("vehicle", "acknowledged_by"), f, start="occurred_at")
    if f.status == "open":
        qs = qs.exclude(status="resolved")
    elif f.status:
        qs = qs.filter(status=f.status)
    return [{"at": a.occurred_at, "vehicle": _reg(a.vehicle), "type": a.type, "severity": a.severity,
             "message": a.message, "occurrences": a.occurrences, "status": a.status,
             "acknowledged_by": a.acknowledged_by.email if a.acknowledged_by else "",
             "delayed": "yes" if a.delayed else ""} for a in qs.order_by("occurred_at")]


#: Behaviour score (documented in DECISIONS.md): 100 minus penalty points per 100 km driven.
PENALTY = {"overspeed": 5, "harsh_acceleration": 3, "harsh_braking": 3, "harsh_cornering": 3, "crash": 20}


def driver_report(f: Filters) -> list[dict[str, Any]]:
    from apps.fleet.models import Driver
    trips_by = {r["driver_id"]: r for r in _in_range(Trip.objects, f).exclude(driver__isnull=True)
                .values("driver_id").annotate(trips=Count("id"), distance=Sum("distance_km"), driving=Sum("driving_s"))}
    events: dict[int, dict[str, int]] = {}
    for r in (_in_range(DrivingBehaviorEvent.objects, f, start="occurred_at").exclude(driver__isnull=True)
              .values("driver_id", "type").annotate(n=Count("id"))):
        events.setdefault(r["driver_id"], {})[r["type"]] = r["n"]
    for r in _in_range(SpeedEvent.objects, f).exclude(driver__isnull=True).values("driver_id").annotate(n=Count("id")):
        events.setdefault(r["driver_id"], {})["overspeed"] = r["n"]
    drivers = Driver.objects.filter(deleted_at__isnull=True)
    if f.driver:
        drivers = drivers.filter(pk=f.driver)
    out = []
    for d in drivers.order_by("last_name", "first_name"):
        t = trips_by.get(d.pk, {})
        ev = events.get(d.pk, {})
        km = t.get("distance") or 0
        penalty = sum(PENALTY.get(k, 0) * n for k, n in ev.items()) / max(km / 100, 1)
        harsh = sum(ev.get(k, 0) for k in ("harsh_acceleration", "harsh_braking", "harsh_cornering"))
        out.append({"driver": d.full_name, "trips": t.get("trips", 0), "distance": km, "driving": t.get("driving") or 0,
                    "overspeed": ev.get("overspeed", 0), "harsh": harsh, "crash": ev.get("crash", 0),
                    "score": max(0, round(100 - penalty)) if t else None})
    return out


def device_health(f: Filters) -> list[dict[str, Any]]:
    from apps.devices.models import Device
    from apps.tracking.models import DeviceState
    states = {s.device_id: s for s in DeviceState.objects.all()}
    out = []
    now = timezone.now()
    for d in Device.objects.filter(deleted_at__isnull=True).select_related("device_model").order_by("name"):
        s = states.get(d.pk)
        silent = int((now - s.last_packet_at).total_seconds()) if s and s.last_packet_at else None
        out.append({"tracker": d.name, "identifier": d.identifier, "model": d.device_model.name,
                    "enabled": "yes" if d.is_active else "no", "status": s.status if s else "NEVER_CONNECTED",
                    "last_seen": s.last_packet_at if s else None, "silent": silent,
                    "gsm": s.gsm_signal if s else None, "battery": s.battery_voltage if s else None,
                    "external": s.external_voltage if s else None})
    return out


def maintenance(f: Filters) -> list[dict[str, Any]]:
    """Service records in the period, plus every active schedule's current due status (spec A13)."""
    from apps.maintenance.models import MaintenanceSchedule, ServiceRecord
    from apps.maintenance.services import due_status

    out: list[dict[str, Any]] = []
    vq = Q(vehicle_id=f.vehicle) if f.vehicle else Q()
    if f.group:
        vq &= Q(vehicle__groups__id=f.group)
    for r in (ServiceRecord.objects.filter(vq, performed_at__gte=f.start, performed_at__lt=f.end)
              .select_related("vehicle", "schedule").order_by("performed_at")):
        out.append({"vehicle": _reg(r.vehicle), "item": r.title, "kind": "service", "at": r.performed_at,
                    "status": "done", "km_left": None, "days_left": None,
                    "cost": float(r.cost) if r.cost is not None else None, "vendor": r.vendor})
    for s in MaintenanceSchedule.objects.filter(vq, is_active=True).select_related("vehicle"):
        st = due_status(s)
        if f.status and st.state != f.status:
            continue
        out.append({"vehicle": _reg(s.vehicle), "item": s.name, "kind": "schedule", "at": s.last_service_at,
                    "status": st.state, "km_left": st.km_left, "days_left": st.days_left, "cost": None,
                    "vendor": ""})
    return out


C = Column
REPORTS: dict[str, Report] = {r.key: r for r in [
    Report("daily_movement", "Daily movement", (
        C("day", "Day", "date"), C("vehicle", "Vehicle"), C("trips", "Trips", "number"),
        C("distance", "Distance", "km"), C("driving", "Driving", "duration"), C("idle", "Idling", "duration"),
        C("first", "First move", "datetime"), C("last", "Last stop", "datetime"),
        C("max_speed", "Top speed", "kmh")), daily_movement),
    Report("trips", "Trips", (C("vehicle", "Vehicle"), C("driver", "Driver"), C("start", "Start", "datetime"),
           C("end", "End", "datetime"), C("duration", "Duration", "duration"), C("distance", "Distance", "km"),
           C("max_speed", "Top speed", "kmh"), C("avg_speed", "Average speed", "kmh"), C("source", "Distance from"),
           C("delayed", "Delayed")), trips, filters=("vehicle", "group", "driver", "start", "end")),
    Report("distance", "Distance", (C("vehicle", "Vehicle"), C("trips", "Trips", "number"),
           C("distance", "Distance", "km")), distance, chart=("vehicle", "distance")),
    Report("overspeed", "Speed and overspeed", (C("vehicle", "Vehicle"), C("driver", "Driver"),
           C("start", "Start", "datetime"), C("duration", "Duration", "duration"), C("max_speed", "Top speed", "kmh"),
           C("limit", "Limit", "kmh"), C("severity", "Severity"), C("position", "Position")), overspeed,
           filters=("vehicle", "group", "driver", "start", "end")),
    Report("stops", "Stops", (C("vehicle", "Vehicle"), C("start", "Start", "datetime"), C("end", "End", "datetime"),
           C("duration", "Duration", "duration"), C("engine_off", "Engine off"), C("position", "Position")), stops),
    Report("idling", "Idling", (C("vehicle", "Vehicle"), C("driver", "Driver"), C("start", "Start", "datetime"),
           C("duration", "Duration", "duration"), C("position", "Position")), idling,
           filters=("vehicle", "group", "driver", "start", "end")),
    Report("offline", "Device offline", (C("vehicle", "Vehicle"), C("offline_at", "Went offline", "datetime"),
           C("back_at", "Back online", "datetime"), C("duration", "Offline for", "duration")), offline),
    Report("geofence", "Geofence events", (C("vehicle", "Vehicle"), C("geofence", "Geofence"), C("event", "Event"),
           C("at", "Time", "datetime"), C("dwell", "Dwell", "duration"), C("delayed", "Delayed")), geofence),
    Report("alerts", "Alerts", (C("at", "Time", "datetime"), C("vehicle", "Vehicle"), C("type", "Type"),
           C("severity", "Severity"), C("message", "Message"), C("occurrences", "Occurrences", "number"),
           C("status", "Status"), C("acknowledged_by", "Acknowledged by"), C("delayed", "Delayed")), alerts,
           filters=("vehicle", "group", "start", "end", "status")),
    Report("driver", "Drivers and behaviour score", (C("driver", "Driver"), C("trips", "Trips", "number"),
           C("distance", "Distance", "km"), C("driving", "Driving", "duration"), C("overspeed", "Overspeed", "number"),
           C("harsh", "Harsh events", "number"), C("crash", "Crashes", "number"), C("score", "Score", "number")),
           driver_report, chart=("driver", "score"), filters=("driver", "start", "end")),
    Report("fleet_summary", "Fleet summary", (C("vehicle", "Vehicle"), C("trips", "Trips", "number"),
           C("distance", "Distance", "km"), C("driving", "Driving", "duration"), C("idle", "Idling", "duration"),
           C("max_speed", "Top speed", "kmh"), C("overspeed", "Overspeed", "number"), C("alerts", "Alerts", "number")),
           fleet_summary, chart=("vehicle", "distance")),
    Report("utilization", "Vehicle utilization", (C("vehicle", "Vehicle"), C("driving", "Driving", "duration"),
           C("utilization", "Utilization", "percent"), C("active_days", "Active days", "number"),
           C("days", "Days in period", "number")), utilization, chart=("vehicle", "utilization")),
    Report("maintenance", "Maintenance", (C("vehicle", "Vehicle"), C("item", "Service"), C("kind", "Entry"),
           C("at", "Done / last done", "datetime"), C("status", "Status"), C("km_left", "Km left", "number"),
           C("days_left", "Days left", "number"), C("cost", "Cost", "number"), C("vendor", "Vendor")), maintenance,
           filters=("vehicle", "group", "start", "end", "status")),
    Report("device_health", "Device health", (C("tracker", "Tracker"), C("identifier", "IMEI / ID"),
           C("model", "Model"), C("enabled", "Enabled"), C("status", "Status"), C("last_seen", "Last seen", "datetime"),
           C("silent", "Silent for", "duration"), C("gsm", "GSM signal", "number"), C("battery", "Battery V", "number"),
           C("external", "External V", "number")), device_health, filters=()),
]}

_ = F, timedelta
