"""Webhook and API key management (spec A13). Both need ``integrations.manage``; changes are audited."""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.services import record
from apps.integrations import dispatch
from apps.integrations.authentication import KEY_PREFIX
from apps.integrations.events import emit
from apps.integrations.models import EVENTS, ApiKey, Webhook, WebhookDelivery, WebhookEvent, key_hash
from apps.rbac.catalog import PERMISSION_CHOICES
from common.crud import TenantCrudViewSet
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import TenantViewSet

MANAGE = "integrations.manage"
EVENT_NAMES = [e for e, _ in EVENTS]


class WebhookSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    events = serializers.ListField(child=serializers.ChoiceField(choices=EVENT_NAMES), min_length=1)

    class Meta:
        model = Webhook
        fields = ["id", "name", "url", "events", "is_active", "consecutive_failures", "last_delivery_at",
                  "last_status", "created_at"]
        read_only_fields = ["id", "consecutive_failures", "last_delivery_at", "last_status", "created_at"]

    def validate_url(self, value: str) -> str:
        try:
            dispatch.check_url(value)
        except dispatch.UnsafeUrl as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate_events(self, value: list[str]) -> list[str]:
        return sorted(set(value))


class DeliverySerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    event = serializers.CharField(source="event.event", read_only=True)
    event_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = WebhookDelivery
        fields = ["id", "event", "event_id", "status", "attempts", "next_attempt_at", "response_status",
                  "response_body", "error", "duration_ms", "delivered_at", "created_at"]


class WebhookViewSet(TenantCrudViewSet):
    model = Webhook
    audit_resource = "webhooks"
    soft_delete = False
    serializer_class = WebhookSerializer
    search_fields = ("name", "url")
    required_permissions = {a: MANAGE for a in ("list", "retrieve", "create", "update", "partial_update", "destroy",
                                                "deliveries", "test", "events")}

    @extend_schema(responses=inline_serializer("WebhookCreated", {"id": serializers.IntegerField(),
                                                                  "secret": serializers.CharField()}))
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        secret = "whsec_" + secrets.token_urlsafe(32)
        hook = self._save(ser, organization=self.tenant.organization, secret=secret, created_by=request.user)
        self._audit("created", hook, {"label": hook.name, "url": hook.url, "events": hook.events})
        return Response({**WebhookSerializer(hook).data, "secret": secret}, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer: Any) -> None:
        if serializer.validated_data.get("is_active"):
            serializer.validated_data["consecutive_failures"] = 0
        super().perform_update(serializer)

    @extend_schema(responses=DeliverySerializer(many=True))
    @action(detail=True, methods=["get"])
    def deliveries(self, request: Request, pk: str | None = None) -> Response:
        hook = self.get_object()
        qs = WebhookDelivery.objects.filter(webhook=hook).select_related("event")
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(DeliverySerializer(page, many=True).data)

    @extend_schema(request=None, responses=DeliverySerializer)
    @action(detail=True, methods=["post"])
    def test(self, request: Request, pk: str | None = None) -> Response:
        """Send a signed ``webhook.test`` event now and return the attempt (it is not retried)."""
        hook = self.get_object()
        key = f"test:{hook.pk}:{secrets.token_hex(6)}"
        emit(hook.organization_id, "webhook.test", key, {"webhook": hook.name, "message": "Test delivery"})
        event = WebhookEvent.objects.get(dedup_key=key)
        WebhookEvent.objects.filter(pk=event.pk).update(dispatched_at=timezone.now())
        delivery = WebhookDelivery.objects.create(organization=hook.organization, webhook=hook, event=event)
        dispatch.send(delivery)
        if delivery.status == "pending":          # a test is a single attempt
            delivery.status, delivery.next_attempt_at = "failed", None
        delivery.save()
        hook.save(update_fields=["consecutive_failures", "last_status", "last_delivery_at", "is_active"])
        self._audit("tested", hook, {"status": delivery.status})
        return Response(DeliverySerializer(delivery).data)

    @extend_schema(responses=inline_serializer("WebhookEvents", {"events": serializers.ListField()}))
    @action(detail=False, methods=["get"])
    def events(self, request: Request) -> Response:
        return Response({"events": [{"value": e, "label": label} for e, label in EVENTS]})


class ApiKeySerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)
    active = serializers.BooleanField(source="is_active", read_only=True)

    class Meta:
        model = ApiKey
        fields = ["id", "name", "prefix", "scopes", "expires_at", "revoked_at", "last_used_at", "last_used_ip",
                  "created_by_email", "created_at", "active"]


class CreateKeySerializer(serializers.Serializer):  # type: ignore[type-arg]
    name = serializers.CharField(max_length=100)
    scopes = serializers.ListField(child=serializers.ChoiceField(choices=PERMISSION_CHOICES), min_length=1)
    expires_in_days = serializers.IntegerField(min_value=1, max_value=730, required=False, allow_null=True)


class ApiKeyViewSet(mixins.ListModelMixin, TenantViewSet):
    serializer_class = ApiKeySerializer
    required_permissions = {"list": MANAGE, "create": MANAGE, "revoke": MANAGE}

    def get_queryset(self) -> Any:
        if getattr(self, "swagger_fake_view", False):
            return ApiKey.unscoped.none()
        return ApiKey.objects.select_related("created_by")

    @extend_schema(request=CreateKeySerializer, responses=inline_serializer("ApiKeyCreated", {
        "id": serializers.IntegerField(), "key": serializers.CharField()}))
    def create(self, request: Request) -> Response:
        if self.tenant.via == "api_key":
            raise ApiError("forbidden", "API keys cannot create API keys.", 403)
        data = CreateKeySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        scopes = sorted(set(data.validated_data["scopes"]))
        extra = [s for s in scopes if s not in self.tenant.permissions]
        if extra:
            raise ApiError("scope_not_allowed", "A key can't have permissions you don't have.", 400,
                           {"scopes": extra})
        prefix = secrets.token_hex(4)
        raw = f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
        days = data.validated_data.get("expires_in_days")
        key = ApiKey.objects.create(organization=self.tenant.organization, name=data.validated_data["name"],
                                    prefix=f"{KEY_PREFIX}{prefix}", key_hash=key_hash(raw), scopes=scopes,
                                    created_by=request.user,
                                    expires_at=timezone.now() + timedelta(days=days) if days else None)
        record("api_keys.created", user=request.user, organization=self.tenant.organization, resource=key,
               ip=client_ip(request), metadata={"name": key.name, "scopes": scopes, "expires_at": str(key.expires_at)})
        return Response({**ApiKeySerializer(key).data, "key": raw}, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=ApiKeySerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, request: Request, pk: str | None = None) -> Response:
        key = self.get_queryset().filter(pk=pk).first()
        if key is None:
            raise ApiError("not_found", "API key not found.", 404)
        if key.revoked_at is None:
            key.revoked_at = timezone.now()
            key.save(update_fields=["revoked_at"])
            record("api_keys.revoked", user=request.user, organization=self.tenant.organization, resource=key,
                   ip=client_ip(request), metadata={"name": key.name})
        return Response(ApiKeySerializer(key).data)
