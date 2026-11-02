from __future__ import annotations

from zoneinfo import available_timezones

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from common.tenancy import TenantModel


def validate_timezone(value: str) -> None:
    if value not in available_timezones():
        raise ValidationError(f"Unknown timezone: {value}")


class Organization(models.Model):
    UNITS = [("km", "Kilometres"), ("mi", "Miles")]

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=80, unique=True)
    timezone = models.CharField(max_length=64, default="Africa/Dar_es_Salaam", validators=[validate_timezone])
    language = models.CharField(max_length=5, default="en", choices=[("en", "English"), ("sw", "Kiswahili")])
    distance_unit = models.CharField(max_length=2, choices=UNITS, default="km")
    country = models.CharField(max_length=2, default="TZ")
    is_active = models.BooleanField(default=True)
    #: Prepared for a future Dealer -> Customer level (spec A8); not used by business logic yet.
    parent_organization = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                            related_name="children")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "organizations"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class OrganizationMembership(TenantModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.ForeignKey("rbac.Role", on_delete=models.PROTECT, related_name="memberships")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "organization_users"
        ordering = ["user__email"]
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="uniq_org_user")]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.organization_id}"


class BreakGlassGrant(models.Model):
    """Time-limited, audited Super Administrator access to one organization's data."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="break_glass_grants")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="break_glass_grants")
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "break_glass_grants"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "organization", "expires_at"])]

    def __str__(self) -> str:
        return f"break-glass {self.user_id}@{self.organization_id}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()
