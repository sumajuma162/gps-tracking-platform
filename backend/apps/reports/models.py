"""Report jobs (large exports run by Celery) and scheduled reports (spec A13)."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from common.tenancy import TenantModel

FORMATS = [("csv", "CSV"), ("xlsx", "Excel"), ("pdf", "PDF")]


class ReportJob(TenantModel):
    STATUS = [("queued", "Queued"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    report = models.CharField(max_length=40)
    filters = models.JSONField(default=dict)
    format = models.CharField(max_length=5, choices=FORMATS)
    status = models.CharField(max_length=8, choices=STATUS, default="queued")
    progress = models.PositiveSmallIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    file = models.FileField(upload_to="reports/", blank=True)
    filename = models.CharField(max_length=200, blank=True)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Download link expiry (24 hours).")

    class Meta(TenantModel.Meta):
        db_table = "report_jobs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.report}.{self.format} ({self.status})"


class ScheduledReport(TenantModel):
    FREQUENCY = [("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly")]

    name = models.CharField(max_length=100)
    report = models.CharField(max_length=40)
    filters = models.JSONField(default=dict, blank=True, help_text="Vehicle, group, driver; the period follows the "
                                                                     "frequency (yesterday / last week / last month).")
    frequency = models.CharField(max_length=8, choices=FREQUENCY)
    format = models.CharField(max_length=5, choices=FORMATS, default="xlsx")
    recipients = models.JSONField(default=list, help_text="Email addresses.")
    is_active = models.BooleanField(default=True)
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "scheduled_reports"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
