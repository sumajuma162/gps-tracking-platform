"""Queued notification delivery with retries (spec A11: email sent through Celery with retries)."""
from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.alerts.models import Notification
from apps.alerts.providers import DeliveryError, provider_for
from common.rls import rls_bypass

BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)]


@shared_task(name="alerts.deliver_queued_notifications")
def deliver_queued_notifications(limit: int = 100) -> dict[str, int]:
    """Send due queued notifications; failures are retried with back-off, then marked failed with the error."""
    sent = failed = retried = 0
    with transaction.atomic(), rls_bypass():
        due = list(Notification.unscoped.select_for_update(skip_locked=True).select_related("user")
                   .filter(status="queued", next_attempt_at__lte=timezone.now()).exclude(channel="in_app")
                   .order_by("next_attempt_at")[:limit])
        for n in due:
            n.attempts += 1
            try:
                provider_for(n.channel).send(n)
            except DeliveryError as exc:
                n.error = str(exc)[:300]
                if exc.retry and n.attempts <= len(BACKOFF):
                    n.next_attempt_at = timezone.now() + BACKOFF[n.attempts - 1]
                    retried += 1
                else:
                    n.status, n.next_attempt_at = "failed", None
                    failed += 1
            else:
                n.status, n.sent_at, n.error, n.next_attempt_at = "sent", timezone.now(), "", None
                sent += 1
            n.save(update_fields=["attempts", "status", "error", "sent_at", "next_attempt_at"])
    return {"sent": sent, "failed": failed, "retried": retried}
