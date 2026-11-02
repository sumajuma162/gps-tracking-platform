"""Unregistered device inbox for Super Administrators (spec A7)."""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.services import record
from apps.device_catalog.models import DeviceModel
from apps.devices.events import device_changed
from apps.devices.identifiers import identifier_error
from apps.devices.models import Device, UnregisteredDeviceAttempt
from apps.organizations.models import Organization
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import PlatformViewSet


class UnregisteredAttemptSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = UnregisteredDeviceAttempt
        fields = ["id", "identifier", "protocol", "transport", "port", "last_peer", "first_seen_at", "last_seen_at",
                  "attempts", "sample_hex"]


class RegisterSerializer(serializers.Serializer):  # type: ignore[type-arg]
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.filter(is_active=True,
                                                                                          deleted_at__isnull=True))
    device_model = serializers.PrimaryKeyRelatedField(queryset=DeviceModel.objects.filter(status="active"))
    name = serializers.CharField(max_length=100)


class UnregisteredDeviceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin,
                                PlatformViewSet):
    """Identifiers that tried to connect but are not registered. Deleting dismisses an entry."""

    serializer_class = UnregisteredAttemptSerializer

    def get_queryset(self) -> QuerySet[UnregisteredDeviceAttempt]:
        registered = Device.unscoped.filter(deleted_at__isnull=True).values("identifier")
        return UnregisteredDeviceAttempt.objects.filter(dismissed_at__isnull=True).exclude(identifier__in=registered)

    def perform_destroy(self, instance: UnregisteredDeviceAttempt) -> None:
        instance.dismissed_at = timezone.now()
        instance.save(update_fields=["dismissed_at"])
        record("platform.unregistered_dismissed", user=self.request.user, ip=client_ip(self.request),
               metadata={"identifier": instance.identifier, "protocol": instance.protocol})

    @extend_schema(request=RegisterSerializer, responses={201: inline_serializer("InboxRegistration", {
        "device_id": serializers.IntegerField(), "organization": serializers.CharField(),
        "identifier": serializers.CharField()})})
    @action(detail=True, methods=["post"])
    def register(self, request: Request, pk: str | None = None) -> Response:
        """Register the identifier in an organization. The tracker is accepted on its next connection attempt;
        the action is recorded in that organization's audit log."""
        attempt = self.get_object()
        data = RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        model: DeviceModel = data.validated_data["device_model"]
        if model.protocol.name != attempt.protocol:
            raise ApiError("protocol_mismatch", f"This tracker speaks {attempt.protocol}; choose a model using that "
                                                f"protocol.")
        if problem := identifier_error(attempt.identifier, model.identifier_type, model.identifier_pattern):
            raise ApiError("identifier_invalid", problem)
        org: Organization = data.validated_data["organization"]
        try:
            with transaction.atomic():
                device = Device.unscoped.create(organization=org, name=data.validated_data["name"],
                                                identifier=attempt.identifier, device_model=model)
        except IntegrityError as exc:
            raise ApiError("identifier_taken", "This identifier is already registered.") from exc
        attempt.dismissed_at = timezone.now()
        attempt.save(update_fields=["dismissed_at"])
        record("devices.registered_from_inbox", user=request.user, organization=org, resource=device,
               ip=client_ip(request), metadata={"identifier": device.identifier, "model": model.slug})
        device_changed(device, "created")
        return Response({"device_id": device.pk, "organization": org.name, "identifier": device.identifier},
                        status=status.HTTP_201_CREATED)
