"""Share links (spec A13): a time-limited, revocable public URL showing one vehicle's live position or one trip."""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import models

from common.tenancy import TenantModel


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ShareLink(TenantModel):
    vehicle = models.ForeignKey("fleet.Vehicle", on_delete=models.CASCADE, related_name="+")
    trip = models.ForeignKey("analysis.Trip", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    #: SHA-256 of the token; the token itself is shown once and never stored.
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=100, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    views = models.PositiveIntegerField(default=0)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "share_links"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"share {self.vehicle_id} until {self.expires_at:%Y-%m-%d %H:%M}"
