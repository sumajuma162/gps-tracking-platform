"""Analytics charts (spec A13): built from the ``positions_hourly`` continuous aggregate and from the processor's
results (trips, alerts, overspeed and idle events, device state), never from raw positions.

``GET /api/v1/analytics/?start=&end=`` returns every series at once (days in the organization's timezone).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from apps.alerts.models import Alert
from apps.analysis.models import IdleEvent, SpeedEvent, Trip
from apps.fleet.models import Vehicle
from apps.tracking.models import DeviceState
from common.exceptions import ApiError
from common.viewsets import TenantViewSet

MAX_DAYS = 92


def _window(params: Any, tz: ZoneInfo) -> tuple[datetime, datetime]:
    now = timezone.now()
    end = parse_datetime(params.get("end", "")) if params.get("end") else None
    start = parse_datetime(params.get("start", "")) if params.get("start") else None
    if params.get("end") and end is None or params.get("start") and start is None:
        raise ApiError("invalid_range", "Use ISO 8601 times for start and end.")
    end = end or now
    if start is None:
        local_end = end.astimezone(tz)
        start = datetime.combine(local_end.date() - timedelta(days=13), datetime.min.time(), tz)
    if start >= end:
        raise ApiError("invalid_range", "The start must be before the end.")
    if end - start > timedelta(days=MAX_DAYS):
        raise ApiError("range_too_long", f"Analytics cover at most {MAX_DAYS} days.")
    return start, end


def _days(start: datetime, end: datetime, tz: ZoneInfo) -> list[date]:
    first, last = start.astimezone(tz).date(), (end - timedelta(microseconds=1)).astimezone(tz).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


class AnalyticsViewSet(TenantViewSet):
    required_permissions = {"list": "analytics.view"}
    tenant_policy = "summary"   # aggregates: the isolation test checks nothing of another tenant is counted

    @extend_schema(parameters=[OpenApiParameter("start", str), OpenApiParameter("end", str)],
                   responses=inline_serializer("Analytics", {"days": serializers.ListField()}))
    def list(self, request: Request) -> Response:
        org = self.tenant.organization
        tz = ZoneInfo(org.timezone)
        start, end = _window(request.query_params, tz)
        days = _days(start, end, tz)
        keys = [d.isoformat() for d in days]

        def by_day(qs: Any, field: str, **agg: Any) -> dict[str, dict[str, Any]]:
            rows = qs.annotate(day=TruncDate(field, tzinfo=tz)).values("day").annotate(**agg)
            return {r["day"].isoformat(): r for r in rows}

        trips = by_day(Trip.objects.filter(start_time__gte=start, start_time__lt=end), "start_time",
                       trips=Count("id"), km=Sum("distance_km"), driving=Sum("driving_s"), idle=Sum("idle_s"))
        speeding = by_day(SpeedEvent.objects.filter(start_time__gte=start, start_time__lt=end), "start_time",
                          n=Count("id"), critical=Count("id", filter=_q(severity="critical")))
        idling = by_day(IdleEvent.objects.filter(start_time__gte=start, start_time__lt=end), "start_time",
                        n=Count("id"), seconds=Sum("duration_s"))
        alerts = by_day(Alert.objects.filter(occurred_at__gte=start, occurred_at__lt=end), "occurred_at",
                        info=Count("id", filter=_q(severity="info")),
                        warning=Count("id", filter=_q(severity="warning")),
                        critical=Count("id", filter=_q(severity="critical")))
        vehicles = Vehicle.objects.filter(deleted_at__isnull=True).count()
        hourly = self._hourly(start, end, tz)
        series = []
        for key in keys:
            t, s, i, a, h = (trips.get(key, {}), speeding.get(key, {}), idling.get(key, {}), alerts.get(key, {}),
                             hourly["days"].get(key, {}))
            driving = t.get("driving") or 0
            series.append({
                "day": key, "distance_km": round(float(t.get("km") or 0), 1), "trips": t.get("trips", 0),
                "driving_h": round(driving / 3600, 2), "idle_h": round((t.get("idle") or 0) / 3600, 2),
                "utilization_pct": round(100 * driving / (vehicles * 86400), 2) if vehicles else 0,
                "overspeed": s.get("n", 0), "overspeed_critical": s.get("critical", 0),
                "idle_events": i.get("n", 0), "idle_minutes": round((i.get("seconds") or 0) / 60, 1),
                "alerts_info": a.get("info", 0), "alerts_warning": a.get("warning", 0),
                "alerts_critical": a.get("critical", 0), "records": h.get("records", 0),
                "late_records": h.get("late", 0), "uptime_pct": h.get("uptime_pct", 0)})
        status = dict(DeviceState.objects.values_list("status").annotate(n=Count("device_id")))
        return Response({"start": start, "end": end, "timezone": org.timezone, "vehicles": vehicles,
                         "days": series, "status_distribution": status, "ingestion_hourly": hourly["hours"],
                         "source": "positions_hourly continuous aggregate; trips, alerts and events tables"})

    def _hourly(self, start: datetime, end: datetime, tz: ZoneInfo) -> dict[str, Any]:
        """Records per day and hour, and device uptime (share of device-hours with data) from the aggregate."""
        org_id = self.tenant.organization.pk
        with connection.cursor() as cur:
            cur.execute(
                "SELECT bucket, sum(records), sum(late_records), count(DISTINCT device_id) "
                "FROM tenant_positions_hourly WHERE organization_id = %s AND bucket >= %s AND bucket < %s "
                "GROUP BY bucket ORDER BY bucket", [org_id, start, end])
            rows = cur.fetchall()
            cur.execute("SELECT count(*) FROM devices WHERE organization_id = %s AND deleted_at IS NULL "
                        "AND is_active", [org_id])
            devices = cur.fetchone()[0] or 0
        days: dict[str, dict[str, Any]] = {}
        hours = []
        for bucket, records, late, active in rows:
            key = bucket.astimezone(tz).date().isoformat()
            d = days.setdefault(key, {"records": 0, "late": 0, "device_hours": 0})
            d["records"] += int(records)
            d["late"] += int(late)
            d["device_hours"] += int(active)
            hours.append({"hour": bucket.isoformat(), "records": int(records)})
        for d in days.values():
            d["uptime_pct"] = round(100 * d["device_hours"] / (devices * 24), 1) if devices else 0
        return {"days": days, "hours": hours[-24 * 7:]}


def _q(**kwargs: Any) -> Any:
    from django.db.models import Q
    return Q(**kwargs)
