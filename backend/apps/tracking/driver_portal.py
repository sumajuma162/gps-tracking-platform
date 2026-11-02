"""Driver portal (spec A8 role table: "Driver — own assigned vehicle and own trips only").

``GET /api/v1/my-vehicle/`` returns the vehicle currently assigned to the signed-in user's driver profile (in the
active organization), its live state and its trips of the last 7 days. Nothing else of the fleet is visible.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from apps.analysis.models import Trip
from apps.fleet.models import DriverAssignment
from apps.tracking.live import live_rows
from common.viewsets import TenantViewSet


class MyVehicleViewSet(TenantViewSet):
    required_permissions = {"list": "own_vehicle.view"}
    tenant_policy = "own_vehicle"     # only the caller's own assignment is ever returned

    @extend_schema(responses=inline_serializer("MyVehicle", {
        "vehicle": serializers.DictField(allow_null=True), "live": serializers.DictField(allow_null=True),
        "trips": serializers.ListField()}))
    def list(self, request: Request) -> Response:
        assignment = (DriverAssignment.objects.filter(driver__user=request.user, unassigned_at__isnull=True,
                                                      driver__deleted_at__isnull=True)
                      .select_related("vehicle", "driver").order_by("-assigned_at").first())
        if assignment is None:
            return Response({"vehicle": None, "live": None, "trips": []})
        v = assignment.vehicle
        live = next((r for r in live_rows() if r["vehicle"] and r["vehicle"]["id"] == v.pk), None)
        since = timezone.now() - timedelta(days=7)
        trips: list[dict[str, Any]] = [
            {"id": t.pk, "start_time": t.start_time, "end_time": t.end_time, "distance_km": float(t.distance_km),
             "duration_s": t.duration_s, "max_speed_kmh": t.max_speed_kmh}
            for t in Trip.objects.filter(vehicle=v, start_time__gte=since).order_by("-start_time")[:100]]
        return Response({"vehicle": {"id": v.pk, "registration_number": v.registration_number, "make": v.make,
                                     "model": v.model},
                         "driver": f"{assignment.driver.first_name} {assignment.driver.last_name}".strip(),
                         "live": live, "trips": trips})
