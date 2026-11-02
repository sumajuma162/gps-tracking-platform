"""Write audit entries with secrets redacted (spec A13: never log passwords or secrets)."""
from __future__ import annotations

import re
from typing import Any

from django.db import models

from common.rls import rls_bypass

_SECRET_KEY = re.compile(r"pass|secret|token|key|otp|authorization|cookie", re.IGNORECASE)
REDACTED = "[redacted]"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def record(action: str, *, user: Any = None, organization: Any = None, resource: models.Model | None = None,
           ip: str | None = None, user_agent: str = "", metadata: dict[str, Any] | None = None) -> None:
    from apps.audit.models import AuditLog

    with rls_bypass():
        AuditLog.objects.create(
            action=action,
            user=user if getattr(user, "pk", None) else None,
            organization=organization,
            resource_type=resource._meta.label_lower if resource is not None else "",
            resource_id=str(resource.pk) if resource is not None else "",
            ip_address=ip,
            user_agent=user_agent[:300],
            metadata=redact(metadata or {}),
        )


def record_for_user(action: str, user: Any, **kwargs: Any) -> None:
    """Record an account event once per organization the user belongs to, so each organization's
    administrators see their own users' logins and lockouts. Users without memberships get one
    platform-level entry."""
    from apps.organizations.models import OrganizationMembership

    org_ids = list(OrganizationMembership.unscoped.filter(user=user, organization__deleted_at__isnull=True)
                   .values_list("organization_id", flat=True))
    if not org_ids:
        record(action, user=user, **kwargs)
        return
    from apps.organizations.models import Organization

    for org in Organization.objects.filter(pk__in=org_ids):
        record(action, user=user, organization=org, **kwargs)
