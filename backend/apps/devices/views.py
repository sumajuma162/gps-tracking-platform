from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db.models import Prefetch, QuerySet
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.device_catalog.models import ListenerPort
from apps.devices import services
from apps.devices.events import device_changed
from apps.devices.models import Device, DeviceAssignment
from apps.devices.serializers import DeviceSerializer, ImportSerializer
from apps.fleet.serializers import AssignVehicleSerializer
from common.crud import TenantCrudViewSet
from common.exceptions import ApiError
from common.middleware import client_ip

ImportResult = inline_serializer("DeviceImportResult", {
    "committed": serializers.BooleanField(), "created": serializers.IntegerField(),
    "valid": serializers.IntegerField(), "invalid": serializers.IntegerField(),
    "rows": serializers.ListField(child=serializers.DictField())})


class DeviceViewSet(TenantCrudViewSet):
    unique_error_field = "identifier"
    model = Device
    audit_resource = "devices"
    serializer_class = DeviceSerializer
    search_fields = ("name", "identifier", "serial_number")
    unique_error = ("identifier_taken", "This identifier is already registered on the platform. Check the "
                                        "number, or contact support if the tracker was moved from another account.")
    required_permissions = {"list": "devices.view", "retrieve": "devices.view", "create": "devices.manage",
                            "update": "devices.manage", "partial_update": "devices.manage",
                            "destroy": "devices.manage", "assign": "devices.manage", "unassign": "devices.manage",
                            "enable": "devices.manage", "disable": "devices.manage", "rotate_token": "devices.manage",
                            "setup_instructions": "devices.view", "bulk_import": "devices.manage",
                            "diagnostics": "devices.view"}

    def get_queryset(self) -> QuerySet[Device]:
        qs = super().get_queryset().select_related("device_model__manufacturer", "device_model__protocol", "sim",
                                                   "state").prefetch_related(
            Prefetch("assignments", queryset=DeviceAssignment.unscoped.filter(unassigned_at__isnull=True)
                     .select_related("vehicle")))
        params = self.request.query_params
        if params.get("active") in ("0", "1"):
            qs = qs.filter(is_active=params["active"] == "1")
        if params.get("model"):
            qs = qs.filter(device_model_id=params["model"])
        if params.get("assigned") in ("0", "1"):
            has = DeviceAssignment.objects.filter(unassigned_at__isnull=True).values("device_id")
            qs = qs.filter(pk__in=has) if params["assigned"] == "1" else qs.exclude(pk__in=has)
        return qs

    def after_change(self, instance: Any, action: str) -> None:
        device_changed(instance, action)

    def before_delete(self, instance: Any) -> None:
        services.close_device_assignments(device=instance)
        instance.is_active = False
        instance.inventory_status = "retired"
        instance.save(update_fields=["is_active", "inventory_status", "updated_at"])

    def _fresh(self, pk: int) -> dict[str, Any]:
        return DeviceSerializer(self.get_queryset().get(pk=pk)).data  # type: ignore[no-any-return]

    @extend_schema(request=AssignVehicleSerializer, responses=DeviceSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request: Request, pk: str | None = None) -> Response:
        device = self.get_object()
        data = AssignVehicleSerializer(data=request.data, context={"request": request})
        data.is_valid(raise_exception=True)
        services.assign_device(device, data.validated_data["vehicle"], request.user, client_ip(request))
        return Response(self._fresh(device.pk))

    @extend_schema(request=None, responses=DeviceSerializer)
    @action(detail=True, methods=["post"])
    def unassign(self, request: Request, pk: str | None = None) -> Response:
        device = self.get_object()
        services.unassign_device(device, request.user, client_ip(request))
        return Response(self._fresh(device.pk))

    @extend_schema(request=None, responses=DeviceSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request: Request, pk: str | None = None) -> Response:
        device = self.get_object()
        services.set_active(device, True, request.user, client_ip(request))
        return Response(self._fresh(device.pk))

    @extend_schema(request=None, responses=DeviceSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request: Request, pk: str | None = None) -> Response:
        device = self.get_object()
        services.set_active(device, False, request.user, client_ip(request))
        return Response(self._fresh(device.pk))

    @extend_schema(responses=inline_serializer("SetupInstructions", {
        "markdown": serializers.CharField(), "server_host": serializers.CharField(),
        "port": serializers.IntegerField(), "transport": serializers.CharField(), "apn": serializers.CharField()}))
    @action(detail=True, methods=["get"], url_path="setup-instructions")
    def setup_instructions(self, request: Request, pk: str | None = None) -> Response:
        device: Device = self.get_object()
        model = device.device_model
        listener = (ListenerPort.objects.filter(protocol=model.protocol, is_enabled=True)
                    .order_by("transport").first())
        port = listener.port if listener else model.default_port
        apn = device.sim.apn if device.sim and device.sim.apn else "(your operator's APN)"
        values = {"server_host": settings.TRACKER_SERVER_HOST, "port": str(port), "apn": apn,
                  "identifier": device.identifier}
        text = model.setup_instructions or ""
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        return Response({"markdown": text, "server_host": settings.TRACKER_SERVER_HOST, "port": port,
                         "transport": listener.transport if listener else model.transports[0], "apn": apn})

    @extend_schema(request=None, responses=inline_serializer("IngestToken", {"token": serializers.CharField()}))
    @action(detail=True, methods=["post"], url_path="rotate-token")
    def rotate_token(self, request: Request, pk: str | None = None) -> Response:
        """Issue a new HTTP ingestion token (generic protocol). It is shown once; the old one stops working."""
        import hashlib
        import secrets

        from apps.devices.events import catalog_changed

        device: Device = self.get_object()
        if "http" not in (device.device_model.transports or []):
            raise ApiError("not_http_tracker", "Only HTTP trackers use a token.")
        token = "gdt_" + secrets.token_urlsafe(32)
        Device.objects.filter(pk=device.pk).update(ingest_token_hash=hashlib.sha256(token.encode()).hexdigest())
        self._audit("token_rotated", device, {"name": device.name})
        catalog_changed()          # the gateway drops cached tokens at once
        return Response({"token": token})

    @extend_schema(request=ImportSerializer, responses=ImportResult)
    @action(detail=False, methods=["post"], url_path="import")
    def bulk_import(self, request: Request) -> Response:
        """Validate a CSV (preview). With ``commit: true``, create the valid rows and skip the rest."""
        data = ImportSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        rows = services.parse_import(data.validated_data["csv"])
        services.validate_import(rows)
        created = 0
        if data.validated_data["commit"]:
            if not any(not r.errors for r in rows):
                raise ApiError("import_nothing_valid", "No row is valid. Fix the errors and try again.")
            created = services.commit_import(rows, self.tenant.organization, request.user, client_ip(request))
        return Response({"committed": data.validated_data["commit"], "created": created,
                         "valid": sum(1 for r in rows if not r.errors), "invalid": sum(1 for r in rows if r.errors),
                         "rows": [r.as_dict() for r in rows]},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


    @extend_schema(responses=inline_serializer("DeviceDiagnostics", {
        "stages": serializers.ListField(child=serializers.DictField()), "current_stage": serializers.CharField(),
        "connection": serializers.DictField(), "latest": serializers.DictField(),
        "possible_typos": serializers.ListField(child=serializers.DictField())}))
    @action(detail=True, methods=["get"])
    def diagnostics(self, request: Request, pk: str | None = None) -> Response:
        """Connection test: which setup stages have really happened, the latest decoded record and hints.
        Raw packets are included for Super Administrators only (spec A7)."""
        from apps.devices.diagnostics import diagnose

        return Response(diagnose(self.get_object(), include_raw=bool(request.user.is_platform_admin)))
