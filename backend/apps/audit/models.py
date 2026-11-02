from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only record of security-relevant actions (spec A13). Never updated or deleted by the app."""

    organization = models.ForeignKey("organizations.Organization", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    action = models.CharField(max_length=64, db_index=True)
    resource_type = models.CharField(max_length=64, blank=True)
    resource_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise RuntimeError("Audit log entries are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise RuntimeError("Audit log entries are append-only")
