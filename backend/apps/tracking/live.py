"""Live map snapshot, dashboard summary and map settings (spec A12)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Prefetch
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import DriverAssignment, Vehicle
from common.viewsets import TenantViewSet

STATUSES = ["ONLINE_MOVING", "ONLINE_IDLE", "ONLINE_STOPPED", "NO_GPS", "OFFLINE", "UNKNOWN"]
ONLINE = {"ONLINE_MOVING", "ONLINE_IDLE", "ONLINE_STOPPED", "NO_GPS"}


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


def live_rows() -> list[dict[str, Any]]:
    """Every active tracker of the current organization with its vehicle, driver and state."""
    devices = (Device.objects.filter(deleted_at__isnull=True, is_active=True)
               .select_related("state", "device_model__manufacturer")
               .prefetch_related(Prefetch("assignments",
                                          queryset=DeviceAssignment.unscoped.filter(unassigned_at__isnull=True)
                                          .select_related("vehicle").prefetch_related("vehicle__groups"))))
    vehicle_ids = [a.vehicle_id for d in devices for a in d.assignments.all()]
    drivers = {a.vehicle_id: a.driver for a in DriverAssignment.objects.filter(
        vehicle_id__in=vehicle_ids, unassigned_at__isnull=True).select_related("driver")}
    rows = []
    for d in devices:
        a = next(iter(d.assignments.all()), None)
        v: Vehicle | None = a.vehicle if a else None
        st = getattr(d, "state", None)
        driver = drivers.get(v.pk) if v else None
        rows.append({
            "id": d.pk, "device_id": d.pk, "device_name": d.name, "identifier": d.identifier,
            "model": str(d.device_model),
            "vehicle": None if v is None else {"id": v.pk, "registration_number": v.registration_number,
                                               "make": v.make, "model": v.model, "vehicle_type": v.vehicle_type,
                                               "groups": [g.pk for g in v.groups.all()]},
            "driver": None if driver is None else {"id": driver.pk, "name": driver.full_name, "phone": driver.phone},
            "status": st.status if st else "UNKNOWN",
            "latitude": st.latitude if st else None, "longitude": st.longitude if st else None,
            "speed_kmh": st.speed_kmh if st else None, "heading": st.heading if st else None,
            "satellites": st.satellites if st else None, "ignition": st.ignition if st else None,
            "gps_valid": st.gps_valid if st else False, "fix_time": _iso(st.device_time) if st else None,
            "last_packet_at": _iso(st.last_packet_at) if st else None,
            "external_voltage": st.external_voltage if st else None,
            "battery_voltage": st.battery_voltage if st else None, "gsm_signal": st.gsm_signal if st else None,
            "is_simulated": d.is_simulated,
        })
    return rows


class LiveViewSet(TenantViewSet):
    required_permissions = {"list": "tracking.view_live"}

    @extend_schema(responses=inline_serializer("LiveVehicle", {
        "device_id": serializers.IntegerField(), "vehicle": serializers.DictField(allow_null=True),
        "status": serializers.CharField(), "latitude": serializers.FloatField(allow_null=True),
        "longitude": serializers.FloatField(allow_null=True)}, many=True))
    def list(self, request: Request) -> Response:
        return Response(live_rows())


class DashboardViewSet(TenantViewSet):
    tenant_policy = "summary"
    required_permissions = {"list": "dashboard.view"}

    @extend_schema(responses=inline_serializer("DashboardSummary", {
        "vehicles": serializers.IntegerField(), "by_status": serializers.DictField(),
        "online_devices": serializers.IntegerField(), "devices": serializers.IntegerField(),
        "health": serializers.DictField(), "trips_today": serializers.IntegerField(),
        "distance_today_km": serializers.FloatField()}))
    def list(self, request: Request) -> Response:
        rows = live_rows()
        tracked = [r for r in rows if r["vehicle"]]
        vehicles = Vehicle.objects.filter(deleted_at__isnull=True).exclude(status="inactive").count()
        by_status = {s: 0 for s in STATUSES}
        for r in tracked:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        now = timezone.now()

        def item(r: dict[str, Any]) -> dict[str, Any]:
            return {"device_id": r["device_id"], "name": r["vehicle"]["registration_number"] if r["vehicle"]
                    else r["device_name"], "last_packet_at": r["last_packet_at"]}
        stale = now - timedelta(hours=24)
        health = {
            "never_connected": [item(r) for r in rows if not r["last_packet_at"]],
            "offline_over_24h": [item(r) for r in rows if r["last_packet_at"] and r["status"] == "OFFLINE"
                                 and r["last_packet_at"] < stale.isoformat()],
            "weak_signal": [item(r) for r in rows if r["status"] in ONLINE and r["gsm_signal"] is not None
                            and r["gsm_signal"] <= 1],
            "low_tracker_battery": [item(r) for r in rows if r["battery_voltage"] is not None
                                    and r["battery_voltage"] < 3.6],
        }
        return Response({"vehicles": vehicles, "vehicles_without_tracker": max(0, vehicles - len(tracked)),
                         "by_status": by_status, "devices": len(rows),
                         "online_devices": sum(1 for r in rows if r["status"] in ONLINE), "health": health,
                         **self._today(now)})

    def _today(self, now: Any) -> dict[str, Any]:
        """Spec A13 dashboard: trips and distance since midnight in the organization's time zone (alert counts and
        recent alerts come from ``/alerts/counts/`` and ``/alerts/``)."""
        from zoneinfo import ZoneInfo

        from django.db.models import Count, Sum

        from apps.analysis.models import Trip

        midnight = now.astimezone(ZoneInfo(self.tenant.organization.timezone)).replace(hour=0, minute=0, second=0,
                                                                                         microsecond=0)
        trips = Trip.objects.filter(start_time__gte=midnight).aggregate(n=Count("id"), km=Sum("distance_km"))
        return {"trips_today": trips["n"] or 0, "distance_today_km": round(float(trips["km"] or 0), 1)}


class MapSettingsView(APIView):
    @extend_schema(responses=inline_serializer("MapSettings", {
        "tile_url": serializers.CharField(), "attribution": serializers.CharField(),
        "center": serializers.ListField(child=serializers.FloatField()), "zoom": serializers.IntegerField()}))
    def get(self, request: Request) -> Response:
        from apps.settings_app.views import map_settings
        from common.viewsets import resolve_tenant

        try:   # the organization's starting view; the platform's when the user has no single organization
            org_id: int | None = resolve_tenant(request).organization.pk
        except Exception:
            org_id = None
        return Response(map_settings(org_id))
