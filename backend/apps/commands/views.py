"""Device command queue API (spec A13: commands are built from verified per-model templates; output-control
commands need a capability flag, a confirmation, a dedicated permission and an audit entry)."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.services import record
from apps.commands.models import Command, CommandResponse
from apps.device_catalog.models import CommandTemplate
from apps.devices.models import Device
from apps.fleet.serializers import TenantRelatedField
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import TenantViewSet

log = logging.getLogger("commands")


class ResponseSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = CommandResponse
        fields = ["response_text", "received_at"]


class CommandSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    responses = ResponseSerializer(many=True, read_only=True)
    requested_by_email = serializers.EmailField(source="requested_by.email", default=None, read_only=True)

    class Meta:
        model = Command
        fields = ["id", "device", "template", "name", "command_text", "is_dangerous", "status", "requested_by_email",
                  "created_at", "sent_at", "completed_at", "expires_at", "error", "responses"]


class CreateCommandSerializer(serializers.Serializer):  # type: ignore[type-arg]
    device = TenantRelatedField(Device)
    template = serializers.PrimaryKeyRelatedField(queryset=CommandTemplate.objects.select_related("device_model"))
    confirm = serializers.BooleanField(default=False, help_text="Required for output-control (dangerous) commands.")


def publish(identifier: str, command_id: int) -> None:
    def send() -> None:
        try:
            import redis
            redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2).publish(
                "commands", json.dumps({"identifier": identifier, "command_id": command_id}))
        except Exception as exc:  # the gateway also delivers PENDING commands when the tracker connects
            log.warning("command event not published", extra={"error": type(exc).__name__})
    transaction.on_commit(send)


class CommandViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, TenantViewSet):
    """Queue a command for a tracker; it is sent while the tracker's connection is open. Filter: ``device``."""

    serializer_class = CommandSerializer
    required_permissions = {"list": "devices.view", "retrieve": "devices.view", "create": "commands.send"}

    def get_queryset(self) -> Any:
        if getattr(self, "swagger_fake_view", False):
            return Command.unscoped.none()
        qs = Command.objects.select_related("requested_by").prefetch_related("responses")
        if self.request.query_params.get("device", "").isdigit():
            qs = qs.filter(device_id=int(self.request.query_params["device"]))
        return qs

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        data = CreateCommandSerializer(data=request.data, context={"request": request})
        data.is_valid(raise_exception=True)
        device: Device = data.validated_data["device"]
        template: CommandTemplate = data.validated_data["template"]
        model = device.device_model
        if template.device_model_id != model.pk:
            raise ApiError("wrong_template", "This command template belongs to a different tracker model.")
        if not (model.protocol.supports_commands and model.capabilities.get("commands")):
            raise ApiError("commands_unsupported", "This tracker model does not accept commands.")
        if not device.is_active:
            raise ApiError("device_disabled", "Enable this tracker before sending commands.")
        if template.is_dangerous:
            if not model.capabilities.get("output_control"):
                raise ApiError("capability_missing", "This model is not configured for output-control commands.")
            if not template.is_verified:
                raise ApiError("unverified_dangerous", "Output-control commands must be verified on a real tracker "
                                                       "before they can be sent.")
            if "commands.send_output" not in self.tenant.permissions:
                raise ApiError("forbidden", "You don't have permission to send output-control commands.", 403)
            if not data.validated_data["confirm"]:
                raise ApiError("confirmation_required", "Confirm that you want to send this output-control command.")
        with transaction.atomic():
            cmd = Command.objects.create(
                organization=self.tenant.organization, device=device, template=template, name=template.name,
                command_text=template.command_text, is_dangerous=template.is_dangerous, requested_by=request.user,
                expires_at=timezone.now() + timedelta(hours=settings.COMMAND_EXPIRY_HOURS))
            record("commands.queued", user=request.user, organization=self.tenant.organization, resource=cmd,
                   ip=client_ip(request), metadata={"device": device.identifier, "command": template.name,
                                                    "dangerous": template.is_dangerous})
            publish(device.identifier, cmd.pk)
        return Response(CommandSerializer(cmd).data, status=status.HTTP_201_CREATED)
