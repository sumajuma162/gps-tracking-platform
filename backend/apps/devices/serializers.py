from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.device_catalog.models import DeviceModel
from apps.devices.identifiers import identifier_error, normalize_identifier
from apps.devices.models import Device
from apps.fleet.models import SimCard
from apps.fleet.serializers import TenantRelatedField


class DeviceSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    device_model = serializers.PrimaryKeyRelatedField(queryset=DeviceModel.objects.select_related("protocol"))
    model_name = serializers.CharField(source="device_model.__str__", read_only=True)
    protocol = serializers.CharField(source="device_model.protocol.name", read_only=True)
    sim = TenantRelatedField(SimCard, required=False, allow_null=True)
    sim_phone = serializers.CharField(source="sim.phone_number", read_only=True, default=None)
    current_vehicle = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = ["id", "name", "identifier", "device_model", "model_name", "protocol", "serial_number",
                  "firmware_version", "sim", "sim_phone", "is_active", "inventory_status", "installation_date",
                  "is_simulated", "log_raw_packets", "notes", "current_vehicle", "status", "last_connected_at",
                  "last_packet_at", "created_at", "updated_at"]
        read_only_fields = ["id", "is_active", "last_connected_at", "last_packet_at", "created_at", "updated_at"]

    def get_current_vehicle(self, obj: Device) -> dict[str, Any] | None:
        a = next((a for a in obj.assignments.all() if a.unassigned_at is None), None)
        return {"id": a.vehicle_id, "registration_number": a.vehicle.registration_number} if a else None

    def get_status(self, obj: Device) -> str:
        state = getattr(obj, "state", None)
        return state.status if state else "UNKNOWN"

    def validate_device_model(self, model: DeviceModel) -> DeviceModel:
        if model.status != "active" and (self.instance is None or self.instance.device_model_id != model.pk):
            raise serializers.ValidationError("This model is deprecated. Choose an active model.")
        return model

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        model = attrs.get("device_model", getattr(self.instance, "device_model", None))
        if "identifier" in attrs or "device_model" in attrs:
            identifier = normalize_identifier(attrs.get("identifier", getattr(self.instance, "identifier", "")))
            if problem := identifier_error(identifier, model.identifier_type, model.identifier_pattern):
                raise serializers.ValidationError({"identifier": problem})
            attrs["identifier"] = identifier
        if self.instance is not None and "identifier" in attrs and attrs["identifier"] != self.instance.identifier:
            raise serializers.ValidationError({"identifier": "The identifier can't be changed. Register a new "
                                                             "tracker instead; history stays with this one."})
        return attrs


class ImportSerializer(serializers.Serializer):  # type: ignore[type-arg]
    csv = serializers.CharField(max_length=1_000_000, trim_whitespace=False)
    commit = serializers.BooleanField(default=False)
