"""Per-organization data retention (spec A13 settings, A14 "configurable retention with automatic deletion")."""
from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.db import connection
from django.utils import timezone

from apps.organizations.models import Organization
from apps.settings_app.models import effective

BATCH = 50_000


@shared_task(name="settings.apply_retention")
def apply_retention() -> dict[str, int]:
    """Delete each organization's positions older than its ``retention_days``. The platform-wide TimescaleDB
    retention policy (``POSITIONS_MAX_RETENTION_DAYS``) drops whole chunks beyond the longest allowed period."""
    deleted: dict[str, int] = {}
    for org in Organization.objects.filter(deleted_at__isnull=True):
        cutoff = timezone.now() - timedelta(days=int(effective(org.pk)["retention_days"]))
        total = 0
        while True:
            with connection.cursor() as cur:
                # (id, device_time) is the hypertable's key; ctid is only unique within one chunk.
                cur.execute("DELETE FROM positions WHERE (id, device_time) IN (SELECT id, device_time FROM positions "
                            "WHERE organization_id = %s AND device_time < %s LIMIT %s)", [org.pk, cutoff, BATCH])
                n = cur.rowcount
            total += n
            if n < BATCH:
                break
        if total:
            deleted[org.slug] = total
    return deleted
