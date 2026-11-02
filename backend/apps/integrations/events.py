"""Writing webhook events from the web app (the processor writes the same table with plain SQL)."""
from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from apps.integrations.models import WebhookEvent


def emit(organization_id: int, event: str, dedup_key: str, payload: dict[str, Any]) -> bool:
    """Record an event once; returns False when it was already recorded. Joins the caller's transaction."""
    try:
        with transaction.atomic():
            WebhookEvent.unscoped.create(organization_id=organization_id, event=event, dedup_key=dedup_key[:200],
                                         payload=payload)
    except IntegrityError:
        return False
    return True
