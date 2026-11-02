"""Platform and organization settings (spec A13 "Settings")."""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from apps.settings_app.schema import DEFAULTS
from common.tenancy import TenantModel


class PlatformSettings(models.Model):
    """A single row (id 1): defaults for every organization plus platform-only choices."""

    GEOCODERS = [("none", "None"), ("nominatim", "Nominatim (OpenStreetMap)"), ("custom", "Custom URL")]

    values = models.JSONField(default=dict, blank=True, help_text="Overrides of the built-in defaults.")
    map_tile_url = models.CharField(max_length=300, blank=True, help_text="Blank: MAP_TILE_URL from the environment.")
    map_attribution = models.CharField(max_length=200, blank=True)
    geocoder_provider = models.CharField(max_length=20, choices=GEOCODERS, default="none")
    geocoder_url = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "platform_settings"

    def __str__(self) -> str:
        return "platform settings"

    @classmethod
    def load(cls) -> PlatformSettings:
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class OrganizationSettings(TenantModel):
    values = models.JSONField(default=dict, blank=True, help_text="This organization's overrides only.")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta(TenantModel.Meta):
        db_table = "organization_settings"
        constraints = [models.UniqueConstraint(fields=["organization"], name="uniq_organization_settings")]

    def __str__(self) -> str:
        return f"settings {self.organization_id}"


def platform_defaults() -> dict[str, Any]:
    return {**DEFAULTS, **PlatformSettings.load().values}


def effective(organization_id: int) -> dict[str, Any]:
    """Built-in defaults <- platform defaults <- the organization's own values."""
    row = OrganizationSettings.unscoped.filter(organization_id=organization_id).first()
    return {**platform_defaults(), **(row.values if row else {})}
