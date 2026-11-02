"""Super Administrator entry point for sending a manual fix through the gateway pipeline."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings
from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record
from apps.devices.models import Device, DeviceAssignment
from apps.organizations.models import Organization
from common.exceptions import ApiError
from common.middleware import client_ip
from common.rls import rls_bypass, set_current_org
from common.tenancy import activate_org, deactivate_org
from common.viewsets import resolve_tenant


class ManualPositionSerializer(serializers.Serializer):  # type: ignore[type-arg]
    device_id = serializers.IntegerField(min_value=1)
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    speed_kmh = serializers.FloatField(min_value=0, max_value=300, default=0)
    heading = serializers.FloatField(min_value=0, max_value=359.999, required=False, allow_null=True)
    ignition = serializers.BooleanField(default=True)


class CanSubmitManualPosition(BasePermission):
    message = "Manual positions require device-management permission."

    def has_permission(self, request: Request, view: Any) -> bool:
        return bool(request.user and request.user.is_authenticated and (
            request.user.is_platform_admin or "devices.manage" in request.tenant.permissions))


class ManualPositionView(APIView):
    permission_classes = [IsAuthenticated, CanSubmitManualPosition]

    def perform_authentication(self, request: Request) -> None:
        super().perform_authentication(request)
        if not request.user or not request.user.is_authenticated:
            return
        if request.user.is_platform_admin:
            self._bypass = rls_bypass()
            self._bypass.__enter__()
            return
        tenant = resolve_tenant(request)
        request.tenant = tenant  # type: ignore[attr-defined]
        self._org_token = activate_org(tenant.organization.pk)
        set_current_org(tenant.organization.pk)

    def finalize_response(self, request: Request, response: Response, *args: Any, **kwargs: Any) -> Response:
        org_token = getattr(self, "_org_token", None)
        if org_token is not None:
            deactivate_org(org_token)
            self._org_token = None
        bypass = getattr(self, "_bypass", None)
        if bypass is not None:
            bypass.__exit__(None, None, None)
            self._bypass = None
        return super().finalize_response(request, response, *args, **kwargs)

    def get(self, request: Request) -> Response:
        platform_admin = request.user.is_platform_admin
        organization_id = None if platform_admin else request.tenant.organization.pk  # type: ignore[attr-defined]
        organizations_query = Organization.objects.filter(is_active=True, deleted_at__isnull=True)
        devices_query = Device.unscoped.filter(is_active=True, deleted_at__isnull=True,
                                                organization__is_active=True,
                                                organization__deleted_at__isnull=True)
        if organization_id is not None:
            organizations_query = organizations_query.filter(pk=organization_id)
            devices_query = devices_query.filter(organization_id=organization_id)
        organizations = list(organizations_query.order_by("name").values("id", "name"))
        devices = list(devices_query.select_related("organization", "device_model__protocol")
                       .prefetch_related(Prefetch(
                           "assignments", queryset=DeviceAssignment.unscoped.filter(
                               unassigned_at__isnull=True).select_related("vehicle")))
                       .order_by("organization__name", "name"))
        rows = []
        for device in devices:
            assignment = next(iter(device.assignments.all()), None)
            if assignment is None:
                continue
            rows.append({"id": device.pk, "organization_id": device.organization_id,
                         "organization_name": device.organization.name, "name": device.name,
                         "identifier": device.identifier, "protocol": device.device_model.protocol.name,
                         "vehicle": assignment.vehicle.registration_number})
        return Response({"organizations": organizations, "devices": rows})

    @extend_schema(request=ManualPositionSerializer, responses={200: serializers.DictField()})
    def post(self, request: Request) -> Response:
        data = ManualPositionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        platform_admin = request.user.is_platform_admin
        organization_id = None if platform_admin else request.tenant.organization.pk  # type: ignore[attr-defined]
        device_query = Device.unscoped.filter(pk=values["device_id"], is_active=True, deleted_at__isnull=True,
                                               organization__is_active=True,
                                               organization__deleted_at__isnull=True)
        assignment_query = DeviceAssignment.unscoped.filter(device_id=values["device_id"],
                                                             unassigned_at__isnull=True)
        if organization_id is not None:
            device_query = device_query.filter(organization_id=organization_id)
            assignment_query = assignment_query.filter(organization_id=organization_id)
        device = device_query.select_related("organization", "device_model__protocol").first()
        assignment = assignment_query.select_related("vehicle").first()
        if device is None or assignment is None:
            raise ApiError("device_unavailable", "Choose an active tracker assigned to a vehicle.", 404)

        payload = {"identifier": device.identifier, "protocol": device.device_model.protocol.name,
                   "latitude": values["latitude"], "longitude": values["longitude"],
                   "speed_kmh": values["speed_kmh"], "heading": values.get("heading"),
                   "ignition": values["ignition"]}
        gateway_request = urllib.request.Request(  # noqa: S310 - configured internal gateway URL
            settings.GATEWAY_MANUAL_INGEST_URL, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {settings.SECRET_KEY}", "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(gateway_request, timeout=5) as response:  # noqa: S310 - internal URL
                gateway_result: dict[str, Any] = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ApiError("device_unavailable", "The gateway cannot find this active tracker.", 404) from exc
            raise ApiError("gateway_unavailable", "The gateway could not accept this position.", 503) from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ApiError("gateway_unavailable", "The gateway could not accept this position.", 503) from exc

        record("devices.manual_position_submitted", user=request.user, organization=device.organization,
               resource=device, ip=client_ip(request), metadata={"protocol": payload["protocol"]})
        return Response({"device_id": device.pk, "device_name": device.name,
                         "organization_name": device.organization.name,
                         "vehicle": assignment.vehicle.registration_number,
                         "latitude": gateway_result["latitude"], "longitude": gateway_result["longitude"],
                         "device_time": gateway_result["device_time"],
                         "accepted_records": gateway_result["accepted_records"]})
