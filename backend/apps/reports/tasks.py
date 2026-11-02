"""Report jobs (large exports) and scheduled reports (spec A13)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.utils import timezone

from apps.audit.services import record
from apps.organizations.models import Organization
from apps.reports.models import ReportJob, ScheduledReport
from apps.reports.service import filename, get_report, heading, org_scope, parse_filters
from apps.reports.writers import CONTENT_TYPES, render
from common.rls import rls_bypass

log = logging.getLogger("reports")


@shared_task(name="reports.run_job")
def run_report_job(job_id: int) -> str:
    with rls_bypass():
        job = ReportJob.unscoped.select_related("organization").get(pk=job_id)
    try:
        with org_scope(job.organization_id):
            report = get_report(job.report)
            f = parse_filters(job.filters)
            ReportJob.unscoped.filter(pk=job.pk).update(status="running", progress=10)
            rows = report.rows(f)
            ReportJob.unscoped.filter(pk=job.pk).update(progress=60, row_count=len(rows))
            data = render(report, rows, job.format, heading(job.organization.name, f))
            name = filename(report, f, job.format)
            job.refresh_from_db()
            job.file.save(f"{job.pk}_{name}", ContentFile(data), save=False)
            job.filename, job.status, job.progress, job.row_count = name, "done", 100, len(rows)
            job.finished_at = timezone.now()
            job.expires_at = job.finished_at + timedelta(hours=settings.REPORT_LINK_HOURS)
            job.save()
    except Exception as exc:
        log.exception("report job failed")
        ReportJob.unscoped.filter(pk=job_id).update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300],
                                                   finished_at=timezone.now())
        return "failed"
    return "done"


def period_for(frequency: str, now: datetime) -> tuple[datetime, datetime]:
    """Yesterday, the 7 days before today, or the previous calendar month (local time)."""
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency == "daily":
        return today - timedelta(days=1), today
    if frequency == "weekly":
        return today - timedelta(days=7), today
    first = today.replace(day=1)
    previous = (first - timedelta(days=1)).replace(day=1)
    return previous, first


def next_run(frequency: str, after: datetime) -> datetime:
    """Next 06:00 local run: tomorrow, in a week, or on the first of next month."""
    local = timezone.localtime(after).replace(hour=6, minute=0, second=0, microsecond=0)
    if frequency == "daily":
        return local + timedelta(days=1)
    if frequency == "weekly":
        return local + timedelta(days=7)
    month = (local.replace(day=1) + timedelta(days=32)).replace(day=1)
    return month


@shared_task(name="reports.run_scheduled")
def run_scheduled_reports() -> int:
    now = timezone.now()
    with rls_bypass():
        due = list(ScheduledReport.unscoped.filter(is_active=True, next_run_at__lte=now).select_related("organization"))
    sent = 0
    for sched in due:
        try:
            with org_scope(sched.organization_id):
                report = get_report(sched.report)
                start, end = period_for(sched.frequency, now)
                f = parse_filters({**sched.filters, "start": start.isoformat(), "end": end.isoformat()})
                rows = report.rows(f)
                data = render(report, rows, sched.format, heading(sched.organization.name, f))
                mail = EmailMessage(subject=f"{report.title} — {sched.name}",
                                    body=f"{report.title}\n{heading(sched.organization.name, f)}\n{len(rows)} rows.",
                                    to=list(sched.recipients))
                mail.attach(filename(report, f, sched.format), data, CONTENT_TYPES[sched.format].split(";")[0])
                mail.send(fail_silently=False)
                sched.last_error = ""
                sent += 1
                org = Organization.objects.get(pk=sched.organization_id)
                record("reports.scheduled_sent", user=None, organization=org, resource=sched,
                       metadata={"report": sched.report, "rows": len(rows), "recipients": len(sched.recipients)})
        except Exception as exc:
            log.exception("scheduled report failed")
            sched.last_error = f"{type(exc).__name__}: {exc}"[:300]
        sched.last_run_at = now
        sched.next_run_at = next_run(sched.frequency, now)
        with rls_bypass():
            sched.save(update_fields=["last_run_at", "next_run_at", "last_error"])
    return sent


@shared_task(name="reports.purge_expired")
def purge_expired_files() -> int:
    with rls_bypass():
        jobs = list(ReportJob.unscoped.filter(expires_at__lt=timezone.now()).exclude(file=""))
        for job in jobs:
            job.file.delete(save=False)
            job.file = ""
            job.save(update_fields=["file"])
    return len(jobs)
