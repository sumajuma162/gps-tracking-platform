from __future__ import annotations

import re
from typing import Any

from django.conf import settings
from rest_framework import serializers

from apps.device_catalog.io_schema import ProfileInvalid, validate_mappings
from apps.device_catalog.models import (
    CAPABILITIES,
    TRANSPORTS,
    CommandTemplate,
    DeviceManufacturer,
    DeviceModel,
    DeviceProtocol,
    IoMappingProfile,
    IoMappingProfileVersion,
    ListenerPort,
)


class ManufacturerSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = DeviceManufacturer
        fields = ["id", "name", "slug", "website", "is_active"]


class ProtocolSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = DeviceProtocol
        fields = ["id", "name", "display_name", "transports", "supports_commands", "implemented", "is_enabled"]
        read_only_fields = ["id", "name", "transports", "supports_commands", "implemented"]


class DeviceModelSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    manufacturer_name = serializers.CharField(source="manufacturer.name", read_only=True)
    protocol_name = serializers.CharField(source="protocol.name", read_only=True)
    protocol_implemented = serializers.BooleanField(source="protocol.implemented", read_only=True)
    transports = serializers.ListField(child=serializers.ChoiceField(choices=[t for t, _ in TRANSPORTS]),
                                       allow_empty=False)
    capabilities = serializers.DictField(child=serializers.BooleanField(), required=False)

    class Meta:
        model = DeviceModel
        fields = ["id", "manufacturer", "manufacturer_name", "name", "slug", "protocol", "protocol_name",
                  "protocol_implemented", "transports", "default_port", "identifier_type", "identifier_pattern",
                  "capabilities", "io_profile", "time_offset_minutes", "trip_detection_mode", "generic_format",
                  "setup_instructions", "status", "verified", "is_public"]

    def validate_capabilities(self, value: dict[str, bool]) -> dict[str, bool]:
        unknown = set(value) - set(CAPABILITIES)
        if unknown:
            raise serializers.ValidationError(f"Unknown capabilities: {', '.join(sorted(unknown))}")
        return {k: v for k, v in value.items() if v}

    def validate_identifier_pattern(self, value: str) -> str:
        if value:
            if len(value) > 200:
                raise serializers.ValidationError("Keep the pattern under 200 characters.")
            try:
                re.compile(value)
            except re.error as exc:
                raise serializers.ValidationError(f"Not a valid regular expression: {exc}") from exc
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        id_type = attrs.get("identifier_type", getattr(self.instance, "identifier_type", "imei"))
        pattern = attrs.get("identifier_pattern", getattr(self.instance, "identifier_pattern", ""))
        if id_type == "custom" and not pattern:
            raise serializers.ValidationError({"identifier_pattern": "Custom identifiers need a pattern."})
        protocol = attrs.get("protocol", getattr(self.instance, "protocol", None))
        transports = attrs.get("transports", getattr(self.instance, "transports", []))
        if protocol and set(transports) - set(protocol.transports):
            raise serializers.ValidationError({"transports": f"{protocol.display_name} supports only "
                                                             f"{', '.join(protocol.transports)}."})
        profile = attrs.get("io_profile", getattr(self.instance, "io_profile", None))
        if profile and protocol and profile.protocol_id != protocol.pk:
            raise serializers.ValidationError({"io_profile": "This IO profile belongs to a different protocol."})
        return attrs


class CloneModelSerializer(serializers.Serializer):  # type: ignore[type-arg]
    name = serializers.CharField(max_length=100)
    slug = serializers.SlugField(max_length=80)

    def validate_slug(self, value: str) -> str:
        if DeviceModel.objects.filter(slug=value).exists():
            raise serializers.ValidationError("A model with this slug already exists.")
        return value


class ProfileVersionSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    created_by_email = serializers.EmailField(source="created_by.email", default=None, read_only=True)

    class Meta:
        model = IoMappingProfileVersion
        fields = ["id", "version", "mappings", "notes", "created_by_email", "created_at"]


class IoProfileSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    mappings = serializers.JSONField(write_only=True, required=False)
    notes = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=300)
    current = ProfileVersionSerializer(source="current_version", read_only=True)
    used_by = serializers.SerializerMethodField()

    class Meta:
        model = IoMappingProfile
        fields = ["id", "name", "slug", "protocol", "description", "verified", "current", "used_by",
                  "mappings", "notes"]

    def get_used_by(self, obj: IoMappingProfile) -> list[str]:
        return [str(m) for m in obj.device_models.all()]

    def validate_mappings(self, value: Any) -> list[dict[str, Any]]:
        try:
            return validate_mappings(value)
        except ProfileInvalid as exc:
            raise serializers.ValidationError(exc.errors) from exc

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if self.instance is None and "mappings" not in attrs:
            raise serializers.ValidationError({"mappings": "A new profile needs its mappings."})
        return attrs


class ListenerPortSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    reachable = serializers.SerializerMethodField()
    warning = serializers.SerializerMethodField()

    class Meta:
        model = ListenerPort
        fields = ["id", "port", "transport", "protocol", "generic_format", "is_enabled", "description", "reachable",
                  "warning"]

    @staticmethod
    def _published(port: int) -> bool:
        low, high = settings.LISTENER_RESERVED_RANGE
        return port in settings.LISTENER_PUBLISHED_PORTS or low <= port <= high

    def get_reachable(self, obj: ListenerPort) -> bool:
        return self._published(obj.port)

    def get_warning(self, obj: ListenerPort) -> str | None:
        if self._published(obj.port):
            return None
        low, high = settings.LISTENER_RESERVED_RANGE
        return (f"Port {obj.port} is not published by Docker. Trackers can't reach it until it is added to the "
                f"gateway's ports and the firewall. Use the reserved range {low}-{high} instead.")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        protocol = attrs.get("protocol", getattr(self.instance, "protocol", None))
        transport = attrs.get("transport", getattr(self.instance, "transport", None))
        fmt = attrs.get("generic_format", getattr(self.instance, "generic_format", None))
        if protocol is not None and protocol.name == "generic_text" and (fmt is None or fmt.kind != "delimited"):
            raise serializers.ValidationError({"generic_format": "Delimited-text ports need a delimited format."})
        if fmt is not None and (protocol is None or protocol.name != "generic_text"):
            attrs["generic_format"] = None      # only text ports are bound to one format
        if protocol and transport and transport not in protocol.transports:
            raise serializers.ValidationError({"transport": f"{protocol.display_name} does not use {transport}."})
        return attrs


class CommandTemplateSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = CommandTemplate
        fields = ["id", "device_model", "name", "command_text", "description", "is_verified", "is_dangerous"]


class GenericFormatSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    models_using = serializers.SerializerMethodField()

    class Meta:
        from apps.device_catalog.models import GenericFormat
        model = GenericFormat
        fields = ["id", "name", "slug", "kind", "definition", "is_active", "notes", "models_using", "created_at",
                  "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_models_using(self, obj: Any) -> list[str]:
        return [m.name for m in obj.device_models.all()]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        from apps.device_catalog.generic import check_definition

        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        definition = attrs.get("definition", getattr(self.instance, "definition", None))
        if kind and definition is not None:
            attrs["definition"] = check_definition(kind, definition)
        return attrs
