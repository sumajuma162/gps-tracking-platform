"""Share link management (authenticated) and the public, read-only view (spec A13)."""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.analysis.models import Trip
from apps.audit.services import record
from apps.fleet.models import Vehicle
from apps.fleet.serializers import TenantRelatedField
from apps.sharing.models import ShareLink, token_hash
from apps.tracking.models import DeviceState
from common.exceptions import ApiError
from common.middleware import client_ip
from common.rls import rls_bypass
from common.viewsets import TenantViewSet


class ShareSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    vehicle_registration = serializers.CharField(source="vehicle.registration_number", read_only=True)
    active = serializers.SerializerMethodField()

    class Meta:
        model = ShareLink
        fields = ["id", "vehicle", "vehicle_registration", "trip", "label", "expires_at", "revoked_at", "views",
                  "last_viewed_at", "created_at", "active"]

    def get_active(self, link: ShareLink) -> bool:
        return link.revoked_at is None and link.expires_at > timezone.now()


class CreateShareSerializer(serializers.Serializer):  # type: ignore[type-arg]
    vehicle = TenantRelatedField(Vehicle)
    trip = TenantRelatedField(Trip, required=False, allow_null=True)
    hours = serializers.IntegerField(min_value=1, max_value=168, default=24)
    label = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        trip = attrs.get("trip")
        if trip is not None and trip.vehicle_id != attrs["vehicle"].pk:
            raise serializers.ValidationError({"trip": "This trip belongs to another vehicle."})
        return attrs


class ShareLinkViewSet(mixins.ListModelMixin, TenantViewSet):
    serializer_class = ShareSerializer
    required_permissions = {"list": "share_links.create", "create": "share_links.create",
                            "revoke": "share_links.create"}

    def get_queryset(self) -> Any:
        if getattr(self, "swagger_fake_view", False):
            return ShareLink.unscoped.none()
        return ShareLink.objects.select_related("vehicle")

    @extend_schema(request=CreateShareSerializer, responses=inline_serializer("ShareCreated", {
        "id": serializers.IntegerField(), "url": serializers.URLField(), "expires_at": serializers.DateTimeField()}))
    def create(self, request: Request) -> Response:
        data = CreateShareSerializer(data=request.data, context={"request": request})
        data.is_valid(raise_exception=True)
        token = secrets.token_urlsafe(32)
        link = ShareLink.objects.create(
            organization=self.tenant.organization, vehicle=data.validated_data["vehicle"],
            trip=data.validated_data.get("trip"), label=data.validated_data.get("label", ""),
            token_hash=token_hash(token), created_by=request.user,
            expires_at=timezone.now() + timedelta(hours=data.validated_data["hours"]))
        record("share_links.created", user=request.user, organization=self.tenant.organization, resource=link,
               ip=client_ip(request), metadata={"vehicle": link.vehicle.registration_number,
                                                "trip": link.trip_id, "hours": data.validated_data["hours"]})
        return Response({"id": link.pk, "url": f"{settings.FRONTEND_URL}/share/{token}", "expires_at": link.expires_at},
                        status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=ShareSerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, request: Request, pk: str | None = None) -> Response:
        link = self.get_queryset().filter(pk=pk).first()
        if link is None:
            raise ApiError("not_found", "Share link not found.", 404)
        if link.revoked_at is None:
            link.revoked_at = timezone.now()
            link.save(update_fields=["revoked_at"])
            record("share_links.revoked", user=request.user, organization=self.tenant.organization, resource=link,
                   ip=client_ip(request), metadata={"vehicle": link.vehicle.registration_number})
        return Response(ShareSerializer(link).data)


class ShareThrottle(AnonRateThrottle):
    rate = "60/min"


class PublicShareView(APIView):
    """What a share link shows: one vehicle's position (or one trip's path) and nothing else."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [ShareThrottle]

    @extend_schema(responses=inline_serializer("PublicShare", {"vehicle": serializers.CharField(),
                                                               "expires_at": serializers.DateTimeField()}))
    def get(self, request: Request, token: str) -> Response:
        now = timezone.now()
        with rls_bypass():                      # looked up by the secret token only
            link = (ShareLink.unscoped.select_related("vehicle", "trip")
                    .filter(token_hash=token_hash(token), revoked_at__isnull=True, expires_at__gt=now).first())
            if link is None:
                raise ApiError("not_found", "This link has expired or was revoked.", 404)
            ShareLink.unscoped.filter(pk=link.pk).update(views=F("views") + 1, last_viewed_at=now)
            body: dict[str, Any] = {"vehicle": link.vehicle.registration_number, "label": link.label,
                                    "expires_at": link.expires_at}
            if link.trip_id:
                from apps.tracking import history
                from common.tenancy import tenant_context
                with tenant_context(link.organization_id):
                    pts = history.fetch(link.vehicle_id, link.trip.start_time,
                                        link.trip.end_time + timedelta(seconds=1))
                kept = history.simplify(pts, 10.0, set())
                body["trip"] = {"start": link.trip.start_time, "end": link.trip.end_time,
                                "distance_km": link.trip.distance_km,
                                "points": [[round(pts[i].lat, 6), round(pts[i].lon, 6)] for i in kept]}
            else:
                state = DeviceState.unscoped.filter(vehicle_id=link.vehicle_id).order_by("-last_packet_at").first()
                body["position"] = None if state is None or state.latitude is None else {
                    "lat": state.latitude, "lon": state.longitude, "heading": state.heading,
                    "speed_kmh": state.speed_kmh, "status": state.status, "updated_at": state.device_time}
        return Response(body)
