"""Maintenance schedules and service records (spec A13 "Maintenance")."""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from common.tenancy import TenantModel


class MaintenanceSchedule(TenantModel):
    """A recurring service for one vehicle, due by distance, engine hours or days, whichever comes first."""

    STATES = [("ok", "OK"), ("due_soon", "Due soon"), ("overdue", "Overdue")]

    vehicle = models.ForeignKey("fleet.Vehicle", on_delete=models.CASCADE, related_name="maintenance_schedules")
    name = models.CharField(max_length=100, help_text="e.g. Oil change")
    interval_km = models.PositiveIntegerField(null=True, blank=True)
    interval_engine_hours = models.PositiveIntegerField(null=True, blank=True)
    interval_days = models.PositiveIntegerField(null=True, blank=True)
    due_soon_km = models.PositiveIntegerField(default=500)
    due_soon_engine_hours = models.PositiveIntegerField(default=10)
    due_soon_days = models.PositiveIntegerField(default=7)
    last_service_at = models.DateTimeField(help_text="Counting starts here.")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    #: The last state an alert was raised for, so an hourly check doesn't repeat itself.
    alert_state = models.CharField(max_length=10, choices=STATES, default="ok")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "maintenance_schedules"
        ordering = ["vehicle__registration_number", "name"]
        constraints = [models.CheckConstraint(
            condition=Q(interval_km__isnull=False) | Q(interval_engine_hours__isnull=False)
            | Q(interval_days__isnull=False), name="maintenance_schedule_has_interval")]

    def __str__(self) -> str:
        return f"{self.name} ({self.vehicle_id})"


class ServiceRecord(TenantModel):
    vehicle = models.ForeignKey("fleet.Vehicle", on_delete=models.CASCADE, related_name="service_records")
    schedule = models.ForeignKey(MaintenanceSchedule, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="records")
    performed_at = models.DateTimeField()
    title = models.CharField(max_length=150)
    odometer_km = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    engine_hours = models.DecimalField(max_digits=9, decimal_places=1, null=True, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="TZS")
    vendor = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "service_records"
        ordering = ["-performed_at"]

    def __str__(self) -> str:
        return f"{self.title} {self.performed_at:%Y-%m-%d}"
