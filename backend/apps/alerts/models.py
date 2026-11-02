"""Alert rules, alerts and notifications (spec A9 "Alerts and notifications", A11)."""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from common.tenancy import TenantModel

ALERT_TYPES = [
    ("overspeed", "Overspeed"), ("geofence_enter", "Geofence entry"), ("geofence_exit", "Geofence exit"),
    ("geofence_dwell", "Geofence dwell"), ("device_offline", "Device offline"), ("gps_lost", "GPS signal lost"),
    ("unauthorized_movement", "Unauthorized movement"), ("ignition_on", "Ignition on"),
    ("ignition_off", "Ignition off"),
    ("low_battery", "Low tracker battery"), ("power_disconnected", "External power disconnected"),
    ("tampering", "Tampering / unplug"), ("gsm_jamming", "GSM jamming"), ("towing", "Towing"), ("crash", "Crash"),
    ("harsh_driving", "Harsh driving"), ("long_idle", "Long idle"), ("long_stop", "Long stop"),
    ("maintenance_due", "Maintenance due"), ("sim_expiry", "SIM expiry"),
]
SEVERITIES = [("info", "Info"), ("warning", "Warning"), ("critical", "Critical")]
CHANNELS = [("in_app", "In-app"), ("email", "Email"), ("sms", "SMS"), ("whatsapp", "WhatsApp"), ("push", "Push")]


class AlertRule(TenantModel):
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=30, choices=ALERT_TYPES)
    params = models.JSONField(default=dict, blank=True, help_text='e.g. {"limit_kmh": 90}, {"geofences": [3]}, '
                                                                   '{"minutes": 30}')
    severity = models.CharField(max_length=10, choices=SEVERITIES, default="warning")
    applies_to_all = models.BooleanField(default=True)
    vehicles = models.ManyToManyField("fleet.Vehicle", blank=True, related_name="+", db_table="alert_rule_vehicles")
    groups = models.ManyToManyField("fleet.VehicleGroup", blank=True, related_name="+", db_table="alert_rule_groups")
    schedule = models.JSONField(default=dict, blank=True,
                                help_text='Active when: {"days": [0..6, Monday=0], "from": "06:00", "to": "18:00"}')
    recipient_roles = models.JSONField(default=list, blank=True, help_text="Role slugs, e.g. [\"fleet_manager\"].")
    recipient_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+",
                                             db_table="alert_rule_recipients")
    cooldown_minutes = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "alert_rules"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Alert(TenantModel):
    STATUS = [("new", "New"), ("acknowledged", "Acknowledged"), ("resolved", "Resolved")]

    rule = models.ForeignKey(AlertRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="alerts")
    vehicle = models.ForeignKey("fleet.Vehicle", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    device = models.ForeignKey("devices.Device", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    driver = models.ForeignKey("fleet.Driver", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    type = models.CharField(max_length=30, choices=ALERT_TYPES)
    severity = models.CharField(max_length=10, choices=SEVERITIES)
    message = models.CharField(max_length=300)
    message_params = models.JSONField(default=dict, blank=True, help_text="Values used to render the message per "
                                                                            "language in the web app.")
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    occurred_at = models.DateTimeField()
    last_occurred_at = models.DateTimeField()
    occurrences = models.PositiveIntegerField(default=1)
    delayed = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=STATUS, default="new")
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "alerts"
        ordering = ["-last_occurred_at"]
        constraints = [
            # Deduplication (spec A11): one open alert per rule, vehicle and type.
            models.UniqueConstraint(fields=["rule", "vehicle", "type"], condition=~Q(status="resolved"),
                                    name="one_open_alert_per_rule_vehicle_type"),
        ]
        indexes = [models.Index(fields=["organization", "status", "-last_occurred_at"], name="alerts_org_status_idx")]

    def __str__(self) -> str:
        return f"{self.type} {self.vehicle_id} {self.status}"


class Notification(TenantModel):
    """One delivery to one user on one channel. Every attempt's status and error is kept (spec A11)."""

    STATUS = [("queued", "Queued"), ("sent", "Sent"), ("failed", "Failed"), ("suppressed", "Suppressed")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    alert = models.ForeignKey(Alert, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=10, choices=CHANNELS)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    severity = models.CharField(max_length=10, choices=SEVERITIES, default="info")
    status = models.CharField(max_length=10, choices=STATUS, default="queued")
    reason = models.CharField(max_length=60, blank=True, help_text="Why suppressed, e.g. quiet_hours.")
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "channel", "read_at"], name="notifications_user_idx"),
                   models.Index(fields=["channel", "status", "next_attempt_at"], name="notifications_queue_idx")]

    def __str__(self) -> str:
        return f"{self.channel} {self.user_id} {self.status}"


class NotificationPreference(TenantModel):
    """Per user, channel and alert type: on/off and the minimum severity (spec A11). Empty type = all types."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    channel = models.CharField(max_length=10, choices=CHANNELS)
    alert_type = models.CharField(max_length=30, choices=ALERT_TYPES, blank=True)
    min_severity = models.CharField(max_length=10, choices=SEVERITIES, default="info")
    enabled = models.BooleanField(default=True)

    class Meta(TenantModel.Meta):
        db_table = "notification_preferences"
        constraints = [models.UniqueConstraint(fields=["organization", "user", "channel", "alert_type"],
                                               name="uniq_notification_pref")]

    def __str__(self) -> str:
        return f"{self.user_id} {self.channel} {self.alert_type or '*'}"


class NotificationChannel(TenantModel):
    """Provider settings per organization. Only in-app and email deliver in this phase; SMS, WhatsApp and push
    have the interface and settings, with no vendor hardcoded (spec A11)."""

    kind = models.CharField(max_length=10, choices=CHANNELS)
    provider = models.CharField(max_length=40, blank=True, help_text="e.g. smtp; for SMS a provider key in Phase 10.")
    settings = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "notification_channels"
        constraints = [models.UniqueConstraint(fields=["organization", "kind"], name="uniq_channel_kind")]

    def __str__(self) -> str:
        return f"{self.kind} ({self.provider or 'default'})"
