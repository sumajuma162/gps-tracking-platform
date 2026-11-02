from __future__ import annotations

from celery import shared_task

from apps.organizations.models import Organization


@shared_task(name="maintenance.check_due")
def check_due() -> dict[str, int]:
    """Hourly: maintenance due-soon / overdue and SIM expiry alerts, per organization in its own scope (RLS on)."""
    from apps.maintenance.services import check_schedules, check_sims
    from apps.reports.service import org_scope

    out = {"maintenance": 0, "sims": 0}
    for org in Organization.objects.filter(is_active=True, deleted_at__isnull=True):
        with org_scope(org.pk):
            out["maintenance"] += check_schedules(org)
            out["sims"] += check_sims(org)
    return out
