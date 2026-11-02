"""Device command queue (spec A6 Commands, A13): Codec 12 commands wait as PENDING until the tracker's TCP
connection is open; the gateway sends them and records the response or a timeout."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from common.tenancy import TenantModel


class Command(TenantModel):
    STATUS = [("PENDING", "Pending"), ("SENT", "Sent"), ("ACKNOWLEDGED", "Acknowledged"), ("FAILED", "Failed"),
              ("TIMEOUT", "Timed out")]

    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="commands")
    template = models.ForeignKey("device_catalog.CommandTemplate", null=True, on_delete=models.SET_NULL,
                                 related_name="+")
    name = models.CharField(max_length=80)
    command_text = models.CharField(max_length=500)
    is_dangerous = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=STATUS, default="PENDING")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(help_text="A PENDING command not delivered by then times out.")
    error = models.CharField(max_length=300, blank=True)

    class Meta(TenantModel.Meta):
        db_table = "commands"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["device", "status", "created_at"], name="commands_queue_idx")]

    def __str__(self) -> str:
        return f"{self.name} -> {self.device_id} ({self.status})"


class CommandResponse(models.Model):
    command = models.ForeignKey(Command, on_delete=models.CASCADE, related_name="responses")
    response_text = models.TextField()
    received_at = models.DateTimeField()

    class Meta:
        db_table = "command_responses"
        ordering = ["received_at"]

    def __str__(self) -> str:
        return f"response to {self.command_id}"
