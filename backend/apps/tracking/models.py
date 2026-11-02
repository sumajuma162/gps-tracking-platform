"""Location history (TimescaleDB hypertable) and per-device live state (spec A9)."""
from __future__ import annotations

from django.db import models

from common.tenancy import TenantModel


class Position(models.Model):
    """Read-only, tenant-filtered view (``tenant_positions``) over the ``positions`` hypertable.

    The hypertable is created by raw SQL in migration 0002 because its primary key must contain the time
    column. Rows are written only by the gateway (spec A4). The web app's database role can read only this
    view, which returns the current organization's rows (or everything inside ``rls_bypass()``).
    """

    id = models.BigIntegerField(primary_key=True)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.DO_NOTHING, related_name="+")
    device = models.ForeignKey("devices.Device", on_delete=models.DO_NOTHING, related_name="+")
    vehicle = models.ForeignKey("fleet.Vehicle", null=True, on_delete=models.DO_NOTHING, related_name="+")
    device_time = models.DateTimeField()
    server_time = models.DateTimeField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    altitude = models.FloatField(null=True)
    speed_kmh = models.FloatField(null=True)
    heading = models.FloatField(null=True)
    satellites = models.SmallIntegerField(null=True)
    hdop = models.FloatField(null=True)
    gps_valid = models.BooleanField()
    is_outlier = models.BooleanField()
    is_late = models.BooleanField()
    ignition = models.BooleanField(null=True)
    motion = models.BooleanField(null=True)
    odometer_km = models.FloatField(null=True)
    engine_hours = models.FloatField(null=True)
    battery_voltage = models.FloatField(null=True)
    external_voltage = models.FloatField(null=True)
    battery_level = models.FloatField(null=True)
    gsm_signal = models.SmallIntegerField(null=True)
    event_code = models.IntegerField(null=True)
    events = models.JSONField()
    sensors = models.JSONField()
    protocol = models.CharField(max_length=32)
    raw_metadata = models.JSONField()
    record_hash = models.CharField(max_length=64)

    class Meta:
        managed = False
        db_table = "tenant_positions"
        ordering = ["-device_time"]

    def __str__(self) -> str:
        return f"{self.device_id}@{self.device_time:%Y-%m-%d %H:%M:%S}"


class DeviceState(TenantModel):
    """Latest known state of one device. The live map and dashboard read this, never ``positions``."""

    STATUS = [("ONLINE_MOVING", "Moving"), ("ONLINE_IDLE", "Idle"), ("ONLINE_STOPPED", "Stopped"),
              ("OFFLINE", "Offline"), ("NO_GPS", "No GPS"), ("UNKNOWN", "Unknown")]

    device = models.OneToOneField("devices.Device", primary_key=True, on_delete=models.CASCADE, related_name="state")
    vehicle = models.ForeignKey("fleet.Vehicle", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=16, choices=STATUS, default="UNKNOWN")
    device_time = models.DateTimeField(null=True, blank=True, help_text="Device time of the newest valid fix.")
    last_packet_at = models.DateTimeField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    speed_kmh = models.FloatField(null=True, blank=True)
    heading = models.FloatField(null=True, blank=True)
    satellites = models.SmallIntegerField(null=True, blank=True)
    ignition = models.BooleanField(null=True, blank=True)
    motion = models.BooleanField(null=True, blank=True)
    gps_valid = models.BooleanField(default=False)
    external_voltage = models.FloatField(null=True, blank=True)
    battery_voltage = models.FloatField(null=True, blank=True)
    gsm_signal = models.SmallIntegerField(null=True, blank=True)
    odometer_km = models.FloatField(null=True, blank=True)
    io = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "device_state"
        indexes = [models.Index(fields=["organization", "status"], name="device_state_org_status_idx")]

    def __str__(self) -> str:
        return f"{self.device_id}:{self.status}"


class RawPacket(models.Model):
    """Read-only view of the ``raw_packets`` hypertable (raw tracker traffic kept for troubleshooting)."""

    id = models.BigIntegerField(primary_key=True)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.DO_NOTHING, related_name="+")
    device = models.ForeignKey("devices.Device", on_delete=models.DO_NOTHING, related_name="+")
    received_at = models.DateTimeField()
    direction = models.CharField(max_length=3)
    transport = models.CharField(max_length=4)
    payload_hex = models.TextField()
    decode_result = models.CharField(max_length=60)

    class Meta:
        managed = False
        db_table = "raw_packets"
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.device_id} {self.direction} {self.received_at:%H:%M:%S}"


class ProcessingCheckpoint(models.Model):
    """Last record the processor handled per device (spec A3): lets it resume and, in later phases, replay."""

    device = models.OneToOneField("devices.Device", primary_key=True, on_delete=models.CASCADE, related_name="+")
    last_device_time = models.DateTimeField()
    last_record_hash = models.CharField(max_length=64)
    updated_at = models.DateTimeField()
    #: The device's analysis state machine (open trip, stop, idle, overspeed, geofence membership), as JSON.
    state = models.JSONField(default=dict)

    class Meta:
        db_table = "processing_checkpoints"

    def __str__(self) -> str:
        return f"{self.device_id}@{self.last_device_time:%Y-%m-%d %H:%M:%S}"
