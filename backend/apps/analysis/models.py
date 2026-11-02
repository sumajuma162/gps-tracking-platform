"""Results of the processor's analysis engine (spec A9 "Analysis", A10). Written only by the processor.

Every table is unique on (device, start) so at-least-once stream delivery and late-data replays never duplicate
rows. Rows carry the organization, vehicle and driver at the time of the event.
"""
from __future__ import annotations

from django.db import models

from common.tenancy import TenantModel


class _Event(TenantModel):
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="+")
    vehicle = models.ForeignKey("fleet.Vehicle", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    driver = models.ForeignKey("fleet.Driver", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    delayed = models.BooleanField(default=False, help_text="Produced from backlog (late) data.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        abstract = True


class Trip(_Event):
    DISTANCE_SOURCES = [("gps", "GPS"), ("odometer", "Odometer")]

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    start_lat = models.FloatField(null=True)
    start_lon = models.FloatField(null=True)
    end_lat = models.FloatField(null=True)
    end_lon = models.FloatField(null=True)
    start_address = models.CharField(max_length=300, blank=True)
    end_address = models.CharField(max_length=300, blank=True)
    distance_km = models.FloatField()
    distance_source = models.CharField(max_length=10, choices=DISTANCE_SOURCES)
    duration_s = models.PositiveIntegerField()
    driving_s = models.PositiveIntegerField()
    idle_s = models.PositiveIntegerField()
    stopped_s = models.PositiveIntegerField()
    max_speed_kmh = models.FloatField()
    avg_moving_speed_kmh = models.FloatField()
    points = models.PositiveIntegerField()
    detection_mode = models.CharField(max_length=10)

    class Meta(TenantModel.Meta):
        db_table = "trips"
        ordering = ["-start_time"]
        constraints = [models.UniqueConstraint(fields=["device", "start_time"], name="uniq_trip_device_start")]
        indexes = [models.Index(fields=["vehicle", "-start_time"], name="trips_vehicle_idx"),
                   models.Index(fields=["organization", "-start_time"], name="trips_org_idx")]

    def __str__(self) -> str:
        return f"trip {self.device_id} {self.start_time:%Y-%m-%d %H:%M}"


class Stop(_Event):
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    duration_s = models.PositiveIntegerField()
    lat = models.FloatField()
    lon = models.FloatField()
    engine_off = models.BooleanField()
    address = models.CharField(max_length=300, blank=True)

    class Meta(TenantModel.Meta):
        db_table = "stops"
        ordering = ["-start_time"]
        constraints = [models.UniqueConstraint(fields=["device", "start_time"], name="uniq_stop_device_start")]
        indexes = [models.Index(fields=["vehicle", "-start_time"], name="stops_vehicle_idx")]

    def __str__(self) -> str:
        return f"stop {self.device_id} {self.start_time:%H:%M}"


class IdleEvent(_Event):
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    duration_s = models.PositiveIntegerField()
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta(TenantModel.Meta):
        db_table = "idle_events"
        ordering = ["-start_time"]
        constraints = [models.UniqueConstraint(fields=["device", "start_time"], name="uniq_idle_device_start")]

    def __str__(self) -> str:
        return f"idle {self.device_id} {self.start_time:%H:%M}"


class SpeedEvent(_Event):
    SEVERITY = [("warning", "Warning"), ("critical", "Critical")]

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    duration_s = models.PositiveIntegerField()
    max_speed_kmh = models.FloatField()
    threshold_kmh = models.FloatField()
    severity = models.CharField(max_length=10, choices=SEVERITY)
    lat = models.FloatField()
    lon = models.FloatField()
    end_lat = models.FloatField()
    end_lon = models.FloatField()

    class Meta(TenantModel.Meta):
        db_table = "speed_events"
        ordering = ["-start_time"]
        constraints = [models.UniqueConstraint(fields=["device", "start_time"], name="uniq_speed_device_start")]

    def __str__(self) -> str:
        return f"overspeed {self.device_id} {self.max_speed_kmh} km/h"


class DrivingBehaviorEvent(_Event):
    type = models.CharField(max_length=40)
    occurred_at = models.DateTimeField()
    lat = models.FloatField(null=True)
    lon = models.FloatField(null=True)
    speed_kmh = models.FloatField(null=True)

    class Meta(TenantModel.Meta):
        db_table = "driving_behavior_events"
        ordering = ["-occurred_at"]
        constraints = [models.UniqueConstraint(fields=["device", "type", "occurred_at"], name="uniq_behavior")]

    def __str__(self) -> str:
        return f"{self.type} {self.device_id}"


class RecomputeRequest(models.Model):
    """A device/time window whose analysis must be replayed because late (backlog) records arrived (spec A10).
    The processor debounces requests and replaces the window's results in one transaction."""

    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="+")
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    requested_at = models.DateTimeField()
    done_at = models.DateTimeField(null=True, blank=True)
    replayed_points = models.PositiveIntegerField(default=0)
    first_late = models.DateTimeField(help_text="Earliest late record: results from here on are marked delayed.")

    class Meta:
        db_table = "recompute_requests"
        ordering = ["requested_at"]
        # One pending request per device: further late data widens its window instead of queueing more replays.
        constraints = [models.UniqueConstraint(fields=["device"], condition=models.Q(done_at__isnull=True),
                                               name="one_pending_recompute_per_device")]

    def __str__(self) -> str:
        return f"recompute {self.device_id} {self.window_start:%Y-%m-%d}"
