from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.geofences.models import Geofence
from apps.geofences.services import notify_processor, sync_area
from common.crud import TenantCrudViewSet

MAX_VERTICES = 500


class GeofenceSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    polygon = serializers.ListField(child=serializers.ListField(child=serializers.FloatField(), min_length=2,
                                                                max_length=2), required=False, max_length=MAX_VERTICES)

    class Meta:
        model = Geofence
        fields = ["id", "name", "description", "kind", "center_lat", "center_lon", "radius_m", "polygon",
                  "dwell_minutes", "color", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_color(self, value: str) -> str:
        if not (len(value) == 7 and value.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in value[1:])):
            raise serializers.ValidationError("Use a colour like #2563eb.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        get = lambda k: attrs.get(k, getattr(self.instance, k, None))  # noqa: E731
        if get("kind") == "circle":
            if get("center_lat") is None or get("center_lon") is None or not get("radius_m"):
                raise serializers.ValidationError({"radius_m": "A circle needs a centre and a radius."})
            attrs["polygon"] = []
        else:
            ring = get("polygon") or []
            if len(ring) < 3:
                raise serializers.ValidationError({"polygon": "Draw at least 3 points."})
            if any(not (-90 <= lat <= 90 and -180 <= lon <= 180) for lat, lon in ring):
                raise serializers.ValidationError({"polygon": "A point is outside valid coordinates."})
            if len({(round(lat, 7), round(lon, 7)) for lat, lon in ring}) < 3:
                raise serializers.ValidationError({"polygon": "The points must not all be the same."})
            attrs.update(center_lat=None, center_lon=None, radius_m=None)
        return attrs


class GeofenceViewSet(TenantCrudViewSet):
    model = Geofence
    audit_resource = "geofences"
    serializer_class = GeofenceSerializer
    search_fields = ("name",)
    unique_error = ("geofence_exists", "A geofence with this name already exists.")
    unique_error_field = "name"
    required_permissions = {"list": "geofences.view", "retrieve": "geofences.view", "create": "geofences.manage",
                            "update": "geofences.manage", "partial_update": "geofences.manage",
                            "destroy": "geofences.manage"}

    def after_change(self, instance: Any, action: str) -> None:
        if action != "deleted":
            sync_area(instance)
        notify_processor(instance.organization_id)
