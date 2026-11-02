from __future__ import annotations

from typing import Any

from django.db.models import Prefetch, QuerySet
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.devices.models import DeviceAssignment
from apps.fleet import services
from apps.fleet.models import Driver, DriverAssignment, SimCard, Vehicle, VehicleGroup
from apps.fleet.serializers import (
    AssignDriverSerializer,
    DriverSerializer,
    SimCardSerializer,
    VehicleGroupSerializer,
    VehicleSerializer,
)
from common.crud import TenantCrudViewSet
from common.exceptions import ApiError
from common.middleware import client_ip


def _crud_perms(view: str, manage: str) -> dict[str, str | None]:
    return {"list": view, "retrieve": view, "create": manage, "update": manage, "partial_update": manage,
            "destroy": manage}


class SimCardViewSet(TenantCrudViewSet):
    unique_error_field = "phone_number"
    model = SimCard
    audit_resource = "sims"
    serializer_class = SimCardSerializer
    search_fields = ("phone_number", "iccid", "operator")
    unique_error = ("sim_exists", "A SIM card with this phone number or ICCID is already registered.")
    required_permissions = _crud_perms("sims.view", "sims.manage")

    def before_delete(self, instance: Any) -> None:
        if instance.devices.filter(deleted_at__isnull=True).exists():
            raise ApiError("sim_in_use", "Remove this SIM from its tracker first.")


class VehicleGroupViewSet(TenantCrudViewSet):
    unique_error_field = "name"
    model = VehicleGroup
    audit_resource = "vehicle_groups"
    soft_delete = False
    serializer_class = VehicleGroupSerializer
    search_fields = ("name",)
    unique_error = ("group_exists", "A group with this name already exists.")
    required_permissions = _crud_perms("vehicles.view", "vehicles.manage")


class VehicleViewSet(TenantCrudViewSet):
    unique_error_field = "registration_number"
    model = Vehicle
    audit_resource = "vehicles"
    serializer_class = VehicleSerializer
    search_fields = ("registration_number", "make", "model", "vin")
    unique_error = ("vehicle_exists", "A vehicle with this registration number already exists.")
    required_permissions = {**_crud_perms("vehicles.view", "vehicles.manage"), "history": "vehicles.view",
                            "assign_driver": "drivers.manage", "unassign_driver": "drivers.manage"}

    def get_queryset(self) -> QuerySet[Vehicle]:
        qs = super().get_queryset().prefetch_related(
            "groups",
            Prefetch("device_assignments", queryset=DeviceAssignment.unscoped.filter(unassigned_at__isnull=True)
                     .select_related("device")),
            Prefetch("driver_assignments", queryset=DriverAssignment.unscoped.filter(unassigned_at__isnull=True)
                     .select_related("driver")))
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("group"):
            qs = qs.filter(groups__id=params["group"])
        return qs

    def before_delete(self, instance: Any) -> None:
        from apps.devices.services import close_device_assignments

        close_device_assignments(vehicle=instance)
        services.close_driver_assignments(vehicle=instance)

    @extend_schema(request=AssignDriverSerializer, responses=VehicleSerializer)
    @action(detail=True, methods=["post"], url_path="assign-driver")
    def assign_driver(self, request: Request, pk: str | None = None) -> Response:
        vehicle = self.get_object()
        data = AssignDriverSerializer(data=request.data, context={"request": request})
        data.is_valid(raise_exception=True)
        services.assign_driver(data.validated_data["driver"], vehicle, request.user, client_ip(request))
        return Response(VehicleSerializer(self.get_queryset().get(pk=vehicle.pk)).data)

    @extend_schema(request=None, responses=VehicleSerializer)
    @action(detail=True, methods=["post"], url_path="unassign-driver")
    def unassign_driver(self, request: Request, pk: str | None = None) -> Response:
        vehicle = self.get_object()
        services.unassign_driver(vehicle, request.user, client_ip(request))
        return Response(VehicleSerializer(self.get_queryset().get(pk=vehicle.pk)).data)

    @extend_schema(responses=inline_serializer("VehicleHistory", {
        "devices": serializers.ListField(child=serializers.DictField()),
        "drivers": serializers.ListField(child=serializers.DictField())}))
    @action(detail=True, methods=["get"])
    def history(self, request: Request, pk: str | None = None) -> Response:
        vehicle = self.get_object()
        devices = DeviceAssignment.objects.filter(vehicle=vehicle).select_related("device", "assigned_by")[:200]
        drivers = DriverAssignment.objects.filter(vehicle=vehicle).select_related("driver", "assigned_by")[:200]
        return Response({
            "devices": [{"device_id": a.device_id, "name": a.device.name, "identifier": a.device.identifier,
                         "assigned_at": a.assigned_at, "unassigned_at": a.unassigned_at,
                         "assigned_by": a.assigned_by.email if a.assigned_by else None} for a in devices],
            "drivers": [{"driver_id": a.driver_id, "name": a.driver.full_name, "assigned_at": a.assigned_at,
                         "unassigned_at": a.unassigned_at,
                         "assigned_by": a.assigned_by.email if a.assigned_by else None} for a in drivers],
        }, status=status.HTTP_200_OK)


class DriverViewSet(TenantCrudViewSet):
    unique_error_field = "employee_number"
    model = Driver
    audit_resource = "drivers"
    serializer_class = DriverSerializer
    search_fields = ("first_name", "last_name", "employee_number", "license_number", "phone")
    unique_error = ("driver_exists", "A driver with this employee number already exists.")
    required_permissions = _crud_perms("drivers.view", "drivers.manage")

    def get_queryset(self) -> QuerySet[Driver]:
        qs = super().get_queryset().prefetch_related(
            Prefetch("assignments", queryset=DriverAssignment.unscoped.filter(unassigned_at__isnull=True)
                     .select_related("vehicle")))
        if status_filter := self.request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        return qs

    def before_delete(self, instance: Any) -> None:
        services.close_driver_assignments(driver=instance)
