"""Geofences, their rules and the entry/exit/dwell events the processor detects (spec A9, A10)."""
from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.fleet.models import SoftDeleteMixin
from common.tenancy import TenantModel


class Geofence(SoftDeleteMixin, TenantModel):
    KINDS = [("circle", "Circle"), ("polygon", "Polygon")]

    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=10, choices=KINDS)
    center_lat = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-90), MaxValueValidator(90)])
    center_lon = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])
    radius_m = models.FloatField(null=True, blank=True, validators=[MinValueValidator(10), MaxValueValidator(100_000)])
    polygon = models.JSONField(default=list, blank=True, help_text="Ring of [lat, lon] pairs.")
    dwell_minutes = models.PositiveIntegerField(null=True, blank=True,
                                                help_text="Record a dwell event after this long inside.")
    color = models.CharField(max_length=7, default="#2563eb")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "geofences"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"],
                                               condition=models.Q(deleted_at__isnull=True), name="uniq_geofence_name")]

    def __str__(self) -> str:
        return self.name


class GeofenceEvent(TenantModel):
    EVENTS = [("enter", "Entered"), ("exit", "Exited"), ("dwell", "Dwelled")]

    geofence = models.ForeignKey(Geofence, on_delete=models.CASCADE, related_name="events")
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="+")
    vehicle = models.ForeignKey("fleet.Vehicle", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    driver = models.ForeignKey("fleet.Driver", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    event = models.CharField(max_length=5, choices=EVENTS)
    occurred_at = models.DateTimeField()
    lat = models.FloatField()
    lon = models.FloatField()
    dwell_s = models.PositiveIntegerField(null=True, blank=True)
    delayed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "geofence_events"
        ordering = ["-occurred_at"]
        constraints = [models.UniqueConstraint(fields=["device", "geofence", "event", "occurred_at"],
                                               name="uniq_geofence_event")]
        indexes = [models.Index(fields=["vehicle", "-occurred_at"], name="geofence_events_vehicle_idx")]

    def __str__(self) -> str:
        return f"{self.event} {self.geofence_id} {self.occurred_at:%H:%M}"
