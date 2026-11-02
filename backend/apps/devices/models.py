"""Tracker units and their vehicle assignment history (spec A7, A9)."""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.fleet.models import SimCard, SoftDeleteMixin, Vehicle
from common.tenancy import TenantModel


class Device(SoftDeleteMixin, TenantModel):
    INVENTORY = [("in_stock", "In stock"), ("installed", "Installed"), ("faulty", "Faulty"),
                 ("returned", "Returned"), ("retired", "Retired")]

    name = models.CharField(max_length=100)
    identifier = models.CharField(max_length=40, help_text="IMEI or the model's identifier. Unique platform-wide.")
    device_model = models.ForeignKey("device_catalog.DeviceModel", on_delete=models.PROTECT, related_name="devices")
    serial_number = models.CharField(max_length=60, blank=True)
    firmware_version = models.CharField(max_length=60, blank=True)
    sim = models.ForeignKey(SimCard, null=True, blank=True, on_delete=models.SET_NULL, related_name="devices")
    is_active = models.BooleanField(default=True, help_text="Disabled trackers are refused by the gateway.")
    inventory_status = models.CharField(max_length=10, choices=INVENTORY, default="in_stock")
    installation_date = models.DateField(null=True, blank=True)
    is_simulated = models.BooleanField(default=False)
    log_raw_packets = models.BooleanField(default=False)
    #: HTTP trackers (generic protocol): SHA-256 of the per-device token. The token is shown once when issued.
    ingest_token_hash = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False)
    notes = models.TextField(blank=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_packet_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "devices"
        ordering = ["name"]
        constraints = [
            # One identifier can never belong to two organizations (spec A7 step 2).
            models.UniqueConstraint(fields=["identifier"], condition=Q(deleted_at__isnull=True),
                                    name="uniq_device_identifier"),
            models.UniqueConstraint(fields=["sim"], condition=Q(deleted_at__isnull=True) & Q(sim__isnull=False),
                                    name="one_device_per_sim"),
        ]
        indexes = [models.Index(fields=["identifier"], name="devices_identifier_idx")]

    def __str__(self) -> str:
        return f"{self.name} ({self.identifier})"


class DeviceAssignment(TenantModel):
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="assignments")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="device_assignments")
    assigned_at = models.DateTimeField()
    unassigned_at = models.DateTimeField(null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta(TenantModel.Meta):
        db_table = "device_assignments"
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(fields=["device"], condition=Q(unassigned_at__isnull=True),
                                    name="one_open_assignment_per_device"),
            models.UniqueConstraint(fields=["vehicle"], condition=Q(unassigned_at__isnull=True),
                                    name="one_open_device_per_vehicle"),
        ]

    def __str__(self) -> str:
        return f"{self.device_id}->{self.vehicle_id}"


class DeviceConnection(TenantModel):
    """One network session of a tracker with the gateway (written by the gateway, spec A7 diagnostics)."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="connections")
    transport = models.CharField(max_length=4)
    port = models.PositiveIntegerField()
    peer = models.CharField(max_length=64)
    connected_at = models.DateTimeField()
    disconnected_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=40, blank=True)
    packets = models.PositiveIntegerField(default=0)
    records = models.PositiveIntegerField(default=0)

    class Meta(TenantModel.Meta):
        db_table = "device_connections"
        ordering = ["-connected_at"]
        indexes = [models.Index(fields=["device", "-connected_at"], name="device_conn_device_idx")]

    def __str__(self) -> str:
        return f"{self.device_id}@{self.connected_at:%Y-%m-%d %H:%M}"


class UnregisteredDeviceAttempt(models.Model):
    """Platform-level inbox of unknown identifiers that tried to connect (spec A7). Never stores positions."""

    identifier = models.CharField(max_length=40)
    protocol = models.CharField(max_length=40)
    transport = models.CharField(max_length=4)
    port = models.PositiveIntegerField()
    last_peer = models.CharField(max_length=64)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=1)
    sample_hex = models.TextField(blank=True, help_text="First bytes of one packet, for diagnosis.")
    dismissed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "unregistered_device_attempts"
        ordering = ["-last_seen_at"]
        constraints = [models.UniqueConstraint(fields=["identifier", "protocol"], name="uniq_unregistered_attempt")]

    def __str__(self) -> str:
        return f"{self.identifier} ({self.protocol})"
