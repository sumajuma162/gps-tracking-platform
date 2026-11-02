"""Settings API (spec A13): organization settings for its administrators, platform defaults for Super Admins.

Every change is audited with the changed keys and old/new values, and the processor is told to reload them.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings as dj
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.services import record
from apps.geofences.services import notify_processor
from apps.organizations.models import validate_timezone
from apps.settings_app import schema
from apps.settings_app.models import OrganizationSettings, PlatformSettings, effective, platform_defaults
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import PlatformViewSet, TenantViewSet

SettingsBody = inline_serializer("SettingsUpdate", {"values": serializers.DictField(required=False)})


def describe() -> list[dict[str, Any]]:
    """The schema the web form is built from."""
    return [{"key": s.key, "group": s.group, "min": s.minimum, "max": s.maximum, "choices": list(s.choices),
             "integer": s.integer} for s in schema.SETTINGS.values()]


def _clean(values: Any, allow_null: bool) -> dict[str, Any]:
    try:
        return schema.clean_values(values, allow_null=allow_null)
    except ValueError as exc:
        raise ApiError("invalid_settings", "Some settings are not valid.", 400, exc.args[0]) from exc


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    return {k: [before.get(k), after.get(k)] for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}


class OrganizationSettingsViewSet(TenantViewSet):
    required_permissions = {"list": "settings.view", "partial": "settings.manage"}

    def _body(self) -> dict[str, Any]:
        org = self.tenant.organization
        row = OrganizationSettings.objects.first()
        return {"organization": {"name": org.name, "timezone": org.timezone, "language": org.language,
                                 "distance_unit": org.distance_unit},
                "values": effective(org.pk), "overrides": row.values if row else {},
                "defaults": platform_defaults(), "schema": describe()}

    @extend_schema(responses=inline_serializer("OrganizationSettings", {"values": serializers.DictField()}))
    def list(self, request: Request) -> Response:
        return Response(self._body())

    @extend_schema(request=SettingsBody, responses=inline_serializer("OrganizationSettingsSaved", {
        "values": serializers.DictField()}))
    def partial(self, request: Request) -> Response:
        org = self.tenant.organization
        data = request.data if isinstance(request.data, dict) else {}
        org_changes: dict[str, Any] = {}
        for field, check in (("timezone", validate_timezone), ("language", None), ("distance_unit", None)):
            if field not in data:
                continue
            value = data[field]
            choices = {"language": ("en", "sw"), "distance_unit": ("km", "mi")}.get(field)
            try:
                if check:
                    check(value)
                elif value not in choices:  # type: ignore[operator]
                    raise ValueError
            except Exception as exc:
                raise ApiError("invalid_settings", "Some settings are not valid.", 400,
                               {field: "not a valid choice"}) from exc
            org_changes[field] = value
        row, _ = OrganizationSettings.objects.get_or_create(organization=org)
        before = dict(row.values)
        overrides = dict(before)
        for key, value in _clean(data.get("values", {}), allow_null=True).items():
            if value is None:
                overrides.pop(key, None)
            else:
                overrides[key] = value
        errors = schema.check_consistency({**platform_defaults(), **overrides})
        if errors:
            raise ApiError("invalid_settings", "Some settings are not valid.", 400, errors)
        row.values, row.updated_by = overrides, request.user
        row.save(update_fields=["values", "updated_by", "updated_at"])
        org_before = {f: getattr(org, f) for f in org_changes}
        if org_changes:
            for field, value in org_changes.items():
                setattr(org, field, value)
            org.save(update_fields=[*org_changes, "updated_at"])
        changes = {**_diff(before, overrides), **_diff(org_before, org_changes)}
        if changes:
            record("settings.updated", user=request.user, organization=org, resource=row, ip=client_ip(request),
                   metadata={"changes": changes})
            notify_processor(org.pk)
        return Response(self._body())


class PlatformSettingsViewSet(PlatformViewSet):
    FIELDS = ("map_tile_url", "map_attribution", "geocoder_provider", "geocoder_url")

    def _body(self) -> dict[str, Any]:
        p = PlatformSettings.load()
        return {"values": platform_defaults(), "overrides": p.values, "builtin": schema.DEFAULTS,
                "schema": describe(), **{f: getattr(p, f) for f in self.FIELDS},
                "env_map_tile_url": dj.MAP_TILE_URL}

    @extend_schema(responses=inline_serializer("PlatformSettings", {"values": serializers.DictField()}))
    def list(self, request: Request) -> Response:
        return Response(self._body())

    @extend_schema(request=SettingsBody, responses=inline_serializer("PlatformSettingsSaved", {
        "values": serializers.DictField()}))
    def partial(self, request: Request) -> Response:
        p = PlatformSettings.load()
        data = request.data if isinstance(request.data, dict) else {}
        before = {"values": dict(p.values), **{f: getattr(p, f) for f in self.FIELDS}}
        values = dict(p.values)
        for key, value in _clean(data.get("values", {}), allow_null=True).items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        errors = schema.check_consistency({**schema.DEFAULTS, **values})
        if errors:
            raise ApiError("invalid_settings", "Some settings are not valid.", 400, errors)
        p.values = values
        provider = data.get("geocoder_provider", p.geocoder_provider)
        if provider not in dict(PlatformSettings.GEOCODERS):
            raise ApiError("invalid_settings", "Some settings are not valid.", 400, {"geocoder_provider": "unknown"})
        p.geocoder_provider = provider
        for field, limit in (("map_tile_url", 300), ("map_attribution", 200), ("geocoder_url", 200)):
            if field in data:
                value = str(data[field] or "").strip()[:limit]
                if field != "map_attribution" and value and not value.startswith("https://"):
                    raise ApiError("invalid_settings", "Some settings are not valid.", 400,
                                   {field: "must start with https://"})
                setattr(p, field, value)
        p.updated_by = request.user
        p.save()
        after = {"values": dict(p.values), **{f: getattr(p, f) for f in self.FIELDS}}
        changes = _diff(before, after)
        if changes:
            record("platform_settings.updated", user=request.user, ip=client_ip(request),
                   metadata={"changes": changes})
            notify_processor(None)  # type: ignore[arg-type]   # every organization inherits these
        return Response(self._body())


def map_settings(organization_id: int | None) -> dict[str, Any]:
    """Tile provider (platform) and starting view (organization) for map pages."""
    p = PlatformSettings.load()
    values = effective(organization_id) if organization_id else platform_defaults()
    return {"tile_url": p.map_tile_url or dj.MAP_TILE_URL, "attribution": p.map_attribution or dj.MAP_TILE_ATTRIBUTION,
            "center": [values["map_center_lat"], values["map_center_lon"]], "zoom": values["map_zoom"]}
