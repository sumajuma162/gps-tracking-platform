from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.fleet.models import Driver, SimCard, Vehicle, VehicleGroup


class TenantRelatedField(serializers.PrimaryKeyRelatedField):  # type: ignore[type-arg]
    """Primary-key field limited to the current organization's live rows (cross-tenant ids look missing)."""

    def __init__(self, model: Any, **kwargs: Any) -> None:
        self.model = model
        kwargs.setdefault("queryset", model.unscoped.none())
        super().__init__(**kwargs)

    def get_queryset(self) -> Any:
        from common.tenancy import current_org_id

        if current_org_id() is None:  # e.g. OpenAPI generation: there is no tenant, so nothing is selectable
            return self.model.unscoped.none()
        qs = self.model.objects.all()
        if hasattr(self.model, "deleted_at"):
            qs = qs.filter(deleted_at__isnull=True)
        return qs


class SimCardSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    apn_password = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=60)
    has_apn_password = serializers.SerializerMethodField()
    device_name = serializers.SerializerMethodField()

    class Meta:
        model = SimCard
        fields = ["id", "phone_number", "iccid", "operator", "apn", "apn_username", "apn_password",
                  "has_apn_password", "data_plan", "expiry_date", "status", "notes", "device_name", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_has_apn_password(self, obj: SimCard) -> bool:
        return bool(obj.apn_password)

    def get_device_name(self, obj: SimCard) -> str | None:
        device = obj.devices.filter(deleted_at__isnull=True).first()
        return device.name if device else None


class VehicleGroupSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    vehicle_count = serializers.SerializerMethodField()

    class Meta:
        model = VehicleGroup
        fields = ["id", "name", "description", "vehicle_count", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_vehicle_count(self, obj: VehicleGroup) -> int:
        return obj.vehicles.filter(deleted_at__isnull=True).count()


class VehicleSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    groups = TenantRelatedField(VehicleGroup, many=True, required=False)
    current_device = serializers.SerializerMethodField()
    current_driver = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle
        fields = ["id", "registration_number", "vin", "make", "model", "year", "vehicle_type", "color", "fuel_type",
                  "fuel_capacity_l", "status", "odometer_km", "groups", "notes", "current_device", "current_driver",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_registration_number(self, value: str) -> str:
        return " ".join(value.upper().split())

    def validate_vin(self, value: str) -> str:
        value = value.strip().upper()
        if value and (len(value) != 17 or any(c in "IOQ" for c in value) or not value.isalnum()):
            raise serializers.ValidationError("A VIN has 17 letters and digits (no I, O or Q).")
        return value

    def get_current_device(self, obj: Vehicle) -> dict[str, Any] | None:
        a = next((a for a in obj.device_assignments.all() if a.unassigned_at is None), None)
        return {"id": a.device_id, "name": a.device.name, "identifier": a.device.identifier} if a else None

    def get_current_driver(self, obj: Vehicle) -> dict[str, Any] | None:
        a = next((a for a in obj.driver_assignments.all() if a.unassigned_at is None), None)
        return {"id": a.driver_id, "name": a.driver.full_name} if a else None


class DriverSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    full_name = serializers.CharField(read_only=True)
    #: Login for the driver portal: must be an active member of this organization.
    user = serializers.IntegerField(source="user_id", required=False, allow_null=True)
    current_vehicle = serializers.SerializerMethodField()
    license_expired = serializers.SerializerMethodField()

    class Meta:
        model = Driver
        fields = ["id", "first_name", "middle_name", "last_name", "full_name", "phone", "email", "employee_number",
                  "license_number", "license_class", "license_expiry", "license_expired", "emergency_contact_name",
                  "emergency_contact_phone", "status", "current_vehicle", "user", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_user(self, value: int | None) -> int | None:
        from apps.organizations.models import OrganizationMembership

        if value is None:
            return None
        if not OrganizationMembership.objects.filter(user_id=value, is_active=True).exists():
            raise serializers.ValidationError("Choose a user of this organization.")
        return value

    def get_current_vehicle(self, obj: Driver) -> dict[str, Any] | None:
        a = next((a for a in obj.assignments.all() if a.unassigned_at is None), None)
        return {"id": a.vehicle_id, "registration_number": a.vehicle.registration_number} if a else None

    def get_license_expired(self, obj: Driver) -> bool:
        from django.utils import timezone

        return bool(obj.license_expiry and obj.license_expiry < timezone.localdate())


class AssignVehicleSerializer(serializers.Serializer):  # type: ignore[type-arg]
    vehicle_id = TenantRelatedField(Vehicle, source="vehicle")


class AssignDriverSerializer(serializers.Serializer):  # type: ignore[type-arg]
    driver_id = TenantRelatedField(Driver, source="driver")
