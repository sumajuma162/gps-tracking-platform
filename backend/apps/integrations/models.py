"""Webhooks (with a transactional outbox of events and a delivery log) and API keys (spec A13 "Integrations")."""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

from common.tenancy import TenantModel

#: Events an organization can subscribe to. ``webhook.test`` is sent only by the "Send test" button.
EVENTS = [("alert.created", "Alert created"), ("trip.completed", "Trip completed"),
          ("geofence.event", "Geofence entry, exit or dwell"), ("device.offline", "Tracker went offline")]


class Webhook(TenantModel):
    name = models.CharField(max_length=100)
    url = models.URLField(max_length=500)
    events = models.JSONField(default=list, help_text="Subscribed event names.")
    #: HMAC key. Shown once when created; never returned by the API afterwards.
    secret = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "webhooks"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class WebhookEvent(TenantModel):
    """Outbox: written in the same transaction as the fact it reports (by the processor or the web app), then fanned
    out to subscribed webhooks by Celery. ``dedup_key`` makes re-emission (e.g. a recompute) harmless."""

    event = models.CharField(max_length=40)
    dedup_key = models.CharField(max_length=200)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(default=timezone.now)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "webhook_events"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["organization", "dedup_key"], name="uniq_webhook_event")]
        indexes = [models.Index(fields=["dispatched_at", "created_at"], name="webhook_events_pending_idx",
                                condition=models.Q(dispatched_at__isnull=True))]

    def __str__(self) -> str:
        return f"{self.event} {self.dedup_key}"


class WebhookDelivery(TenantModel):
    STATUS = [("pending", "Pending"), ("succeeded", "Succeeded"), ("failed", "Failed")]

    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    event = models.ForeignKey(WebhookEvent, on_delete=models.CASCADE, related_name="deliveries")
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.CharField(max_length=500, blank=True)
    error = models.CharField(max_length=300, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "webhook_deliveries"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="webhook_deliveries_due_idx")]
        constraints = [models.UniqueConstraint(fields=["webhook", "event"], name="uniq_webhook_delivery")]

    def __str__(self) -> str:
        return f"{self.webhook_id}:{self.event_id} {self.status}"


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ApiKey(TenantModel):
    """An organization-scoped key acting with its creator's permissions, narrowed to ``scopes``.
    Only a SHA-256 of the key is stored; the key itself is shown once."""

    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=16, help_text="Shown in lists so a key can be recognised.")
    key_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list, help_text="Permission codenames this key may use.")
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "api_keys"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > timezone.now())


class DataForwarder(TenantModel):
    """Prepared (spec A2, A13): outbound forwarding of positions to a regulator, insurer or client system.

    The model, the payload mapping and the delivery interface exist; no delivery loop runs and no regulator's
    specification is built in. A deployment that needs one (for example LATRA in Tanzania) must configure the
    target from the official specification. See ``apps/integrations/forwarders.py`` and docs/DEVICE_INTEGRATION.md.
    """

    KINDS = [("http_json", "HTTP JSON")]

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=KINDS, default="http_json")
    url = models.URLField(max_length=500)
    #: Output field -> normalized position field, e.g. {"imei": "identifier", "lat": "latitude"}.
    mapping = models.JSONField(default=dict)
    vehicles = models.ManyToManyField("fleet.Vehicle", blank=True, related_name="+", db_table="data_forwarder_vehicles")
    is_active = models.BooleanField(default=False, help_text="Kept off: the delivery loop is not implemented yet.")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantModel.Meta):
        db_table = "data_forwarders"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
