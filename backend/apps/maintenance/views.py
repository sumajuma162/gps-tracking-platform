"""Maintenance schedules and service records (spec A13)."""
from __future__ import annotations

from typing import Any

from django.utils import timezone
from rest_framework import serializers

from apps.fleet.models import Vehicle
from apps.fleet.serializers import TenantRelatedField
from apps.maintenance import services
from apps.maintenance.models import MaintenanceSchedule, ServiceRecord
from common.crud import TenantCrudViewSet


def _perms(view: str, manage: str) -> dict[str, str | None]:
    return {"list": view, "retrieve": view, "create": manage, "update": manage, "partial_update": manage,
            "destroy": manage}


class MaintenanceScheduleSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    vehicle = TenantRelatedField(Vehicle)
    vehicle_registration = serializers.CharField(source="vehicle.registration_number", read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceSchedule
        fields = ["id", "vehicle", "vehicle_registration", "name", "interval_km", "interval_engine_hours",
                  "interval_days", "due_soon_km", "due_soon_engine_hours", "due_soon_days", "last_service_at",
                  "is_active", "notes", "status", "created_at"]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"last_service_at": {"required": False}}

    def get_status(self, obj: MaintenanceSchedule) -> dict[str, Any]:
        return services.due_status(obj).as_dict()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        merged = {f: attrs.get(f, getattr(self.instance, f, None))
                  for f in ("interval_km", "interval_engine_hours", "interval_days")}
        if not any(merged.values()):
            raise serializers.ValidationError({"interval_km": "Set a distance, engine-hours or day interval."})
        if self.instance is None and not attrs.get("last_service_at"):
            attrs["last_service_at"] = timezone.now()
        return attrs


class ScheduleViewSet(TenantCrudViewSet):
    model = MaintenanceSchedule
    audit_resource = "maintenance_schedules"
    soft_delete = False
    serializer_class = MaintenanceScheduleSerializer
    search_fields = ("name", "vehicle__registration_number")
    required_permissions = _perms("maintenance.view", "maintenance.manage")

    def get_queryset(self) -> Any:
        qs = super().get_queryset().select_related("vehicle")
        if vehicle := self.request.query_params.get("vehicle"):
            qs = qs.filter(vehicle_id=vehicle)
        return qs


class ServiceRecordSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    vehicle = TenantRelatedField(Vehicle)
    schedule = TenantRelatedField(MaintenanceSchedule, required=False, allow_null=True)
    vehicle_registration = serializers.CharField(source="vehicle.registration_number", read_only=True)
    schedule_name = serializers.CharField(source="schedule.name", read_only=True, default=None)

    class Meta:
        model = ServiceRecord
        fields = ["id", "vehicle", "vehicle_registration", "schedule", "schedule_name", "performed_at", "title",
                  "odometer_km", "engine_hours", "cost", "currency", "vendor", "notes", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        schedule = attrs.get("schedule")
        vehicle = attrs.get("vehicle", getattr(self.instance, "vehicle", None))
        if schedule is not None and vehicle is not None and schedule.vehicle_id != vehicle.pk:
            raise serializers.ValidationError({"schedule": "This schedule belongs to another vehicle."})
        if attrs.get("performed_at") and attrs["performed_at"] > timezone.now():
            raise serializers.ValidationError({"performed_at": "A service can't be in the future."})
        return attrs


class ServiceRecordViewSet(TenantCrudViewSet):
    model = ServiceRecord
    audit_resource = "service_records"
    soft_delete = False
    serializer_class = ServiceRecordSerializer
    search_fields = ("title", "vendor", "vehicle__registration_number")
    required_permissions = _perms("maintenance.view", "maintenance.manage")

    def get_queryset(self) -> Any:
        qs = super().get_queryset().select_related("vehicle", "schedule")
        if vehicle := self.request.query_params.get("vehicle"):
            qs = qs.filter(vehicle_id=vehicle)
        return qs

    def perform_create(self, serializer: Any) -> None:
        serializer.validated_data["created_by"] = self.request.user
        super().perform_create(serializer)

    def after_change(self, instance: Any, action: str) -> None:
        if action == "created":
            services.after_service(instance)
