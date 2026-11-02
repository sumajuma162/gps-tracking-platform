"""Alert rules, alerts (timeline, acknowledge, resolve), notifications and preferences (spec A11, A13)."""
from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.alerts.models import ALERT_TYPES, Alert, AlertRule, Notification, NotificationChannel, NotificationPreference
from apps.audit.services import record
from apps.fleet.models import Vehicle, VehicleGroup
from apps.fleet.serializers import TenantRelatedField
from apps.rbac.catalog import SYSTEM_ROLES
from common.crud import TenantCrudViewSet
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import TenantViewSet

PARAM_KEYS = {"overspeed": {"limit_kmh"}, "long_idle": {"minutes"}, "long_stop": {"minutes"}, "low_battery": {"volts"},
              "geofence_enter": {"geofences"}, "geofence_exit": {"geofences"}, "geofence_dwell": {"geofences"},
              "unauthorized_movement": {"require_driver"}}


def _publish_rules_changed(organization_id: int) -> None:
    from apps.geofences.services import notify_processor
    notify_processor(organization_id)            # the processor drops its cached rules and geofences together


class AlertRuleSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    vehicles = TenantRelatedField(Vehicle, many=True, required=False)
    groups = TenantRelatedField(VehicleGroup, many=True, required=False)

    class Meta:
        model = AlertRule
        fields = ["id", "name", "type", "params", "severity", "applies_to_all", "vehicles", "groups", "schedule",
                  "recipient_roles", "recipient_users", "cooldown_minutes", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"recipient_users": {"required": False}}

    def validate_recipient_roles(self, value: list[str]) -> list[str]:
        unknown = set(value) - set(SYSTEM_ROLES)
        if unknown:
            raise serializers.ValidationError(f"Unknown roles: {', '.join(sorted(unknown))}")
        return value

    def validate_schedule(self, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            return {}
        from datetime import time
        try:
            for k in ("from", "to"):
                if k in value:
                    time.fromisoformat(value[k])
            if any(d not in range(7) for d in value.get("days", [])):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise serializers.ValidationError('Use {"days": [0-6], "from": "HH:MM", "to": "HH:MM"}.') from exc
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        kind = attrs.get("type", getattr(self.instance, "type", None))
        params = attrs.get("params", getattr(self.instance, "params", {})) or {}
        extra = set(params) - PARAM_KEYS.get(str(kind), set())
        if extra:
            raise serializers.ValidationError({"params": f"Not used by this alert type: {', '.join(sorted(extra))}"})
        for key in ("limit_kmh", "minutes", "volts"):
            if key in params and (not isinstance(params[key], int | float) or params[key] <= 0):
                raise serializers.ValidationError({"params": f"{key} must be a positive number."})
        if "geofences" in params:
            from apps.geofences.models import Geofence
            ids = params["geofences"]
            if not isinstance(ids, list) or Geofence.objects.filter(pk__in=ids).count() != len(set(ids)):
                raise serializers.ValidationError({"params": "Choose geofences of this organization."})
        everyone = attrs.get("applies_to_all", getattr(self.instance, "applies_to_all", True))
        if not everyone and not (attrs.get("vehicles") or attrs.get("groups")):
            raise serializers.ValidationError({"vehicles": "Choose vehicles or groups, or apply to all vehicles."})
        roles = attrs.get("recipient_roles", getattr(self.instance, "recipient_roles", []))
        users = attrs.get("recipient_users", [])
        if not roles and not users and not (self.instance and self.instance.recipient_users.exists()):
            raise serializers.ValidationError({"recipient_roles": "Choose who is notified."})
        return attrs


class AlertRuleViewSet(TenantCrudViewSet):
    model = AlertRule
    audit_resource = "alert_rules"
    soft_delete = False
    serializer_class = AlertRuleSerializer
    search_fields = ("name",)
    required_permissions = {"list": "alerts.view", "retrieve": "alerts.view", "create": "alerts.manage",
                            "update": "alerts.manage", "partial_update": "alerts.manage", "destroy": "alerts.manage"}

    def after_change(self, instance: Any, action: str) -> None:
        _publish_rules_changed(instance.organization_id)


class AlertSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    vehicle_registration = serializers.CharField(source="vehicle.registration_number", default=None, read_only=True)
    rule_name = serializers.CharField(source="rule.name", default=None, read_only=True)
    acknowledged_by_email = serializers.EmailField(source="acknowledged_by.email", default=None, read_only=True)
    resolved_by_email = serializers.EmailField(source="resolved_by.email", default=None, read_only=True)

    class Meta:
        model = Alert
        exclude = ["organization", "acknowledged_by", "resolved_by"]


class NoteSerializer(serializers.Serializer):  # type: ignore[type-arg]
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class AlertViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, TenantViewSet):
    """Alerts newest first. Filters: ``status`` (open = new + acknowledged), ``severity``, ``type``, ``vehicle``."""

    serializer_class = AlertSerializer
    required_permissions = {"list": "alerts.view", "retrieve": "alerts.view", "acknowledge": "alerts.acknowledge",
                            "resolve": "alerts.acknowledge", "counts": "alerts.view"}

    def get_queryset(self) -> Any:
        if getattr(self, "swagger_fake_view", False):
            return Alert.unscoped.none()
        qs = Alert.objects.select_related("vehicle", "rule", "acknowledged_by", "resolved_by")
        p = self.request.query_params
        if p.get("status") == "open":
            qs = qs.exclude(status="resolved")
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        for key in ("severity", "type"):
            if p.get(key):
                qs = qs.filter(**{key: p[key]})
        if p.get("vehicle", "").isdigit():
            qs = qs.filter(vehicle_id=int(p["vehicle"]))
        return qs

    def _transition(self, request: Request, to: str) -> Response:
        data = NoteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            alert = Alert.objects.select_for_update().get(pk=self.get_object().pk)
            if alert.status == "resolved":
                raise ApiError("already_resolved", "This alert is already resolved.")
            if to == "acknowledged" and alert.status == "acknowledged":
                raise ApiError("already_acknowledged", "This alert is already acknowledged.")
            now = timezone.now()
            if to == "acknowledged":
                alert.status, alert.acknowledged_by, alert.acknowledged_at = to, request.user, now
            else:
                alert.status, alert.resolved_by, alert.resolved_at = to, request.user, now
                if alert.acknowledged_at is None:
                    alert.acknowledged_by, alert.acknowledged_at = request.user, now
            note = data.validated_data.get("notes", "").strip()
            if note:
                alert.notes = f"{alert.notes}\n{note}".strip()
            alert.save()
            record(f"alerts.{to}", user=request.user, organization=self.tenant.organization, resource=alert,
                   ip=client_ip(request), metadata={"type": alert.type, "notes": note[:200]})
        return Response(AlertSerializer(alert).data)

    @extend_schema(request=NoteSerializer, responses=AlertSerializer)
    @action(detail=True, methods=["post"])
    def acknowledge(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(request, "acknowledged")

    @extend_schema(request=NoteSerializer, responses=AlertSerializer)
    @action(detail=True, methods=["post"])
    def resolve(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(request, "resolved")

    @extend_schema(responses=inline_serializer("AlertCounts", {"open": serializers.IntegerField(),
                                                               "critical_open": serializers.IntegerField(),
                                                               "today": serializers.IntegerField()}))
    @action(detail=False, methods=["get"])
    def counts(self, request: Request) -> Response:
        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        open_ = Alert.objects.exclude(status="resolved")
        return Response({"open": open_.count(), "critical_open": open_.filter(severity="critical").count(),
                         "today": Alert.objects.filter(occurred_at__gte=start).count()})


class NotificationSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    alert_type = serializers.CharField(source="alert.type", default=None, read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "channel", "title", "body", "severity", "status", "reason", "alert", "alert_type", "read_at",
                  "created_at"]


class NotificationViewSet(mixins.ListModelMixin, TenantViewSet):
    """The signed-in user's in-app notifications (notification center)."""

    serializer_class = NotificationSerializer
    required_permissions = {"list": "notifications.view", "read": "notifications.view",
                            "read_all": "notifications.view", "unread": "notifications.view"}

    def get_queryset(self) -> Any:
        if getattr(self, "swagger_fake_view", False):
            return Notification.unscoped.none()
        qs = Notification.objects.filter(user=self.request.user, channel="in_app").select_related("alert")
        if self.request.query_params.get("unread") == "1":
            qs = qs.filter(read_at__isnull=True)
        return qs

    @extend_schema(request=None, responses=NotificationSerializer)
    @action(detail=True, methods=["post"])
    def read(self, request: Request, pk: str | None = None) -> Response:
        n = self.get_queryset().filter(pk=pk).first()
        if n is None:
            raise ApiError("not_found", "Notification not found.", 404)
        if n.read_at is None:
            n.read_at = timezone.now()
            n.save(update_fields=["read_at"])
        return Response(NotificationSerializer(n).data)

    @extend_schema(request=None, responses=inline_serializer("ReadAll", {"updated": serializers.IntegerField()}))
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request: Request) -> Response:
        return Response({"updated": self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())})

    @extend_schema(responses=inline_serializer("Unread", {"unread": serializers.IntegerField()}))
    @action(detail=False, methods=["get"])
    def unread(self, request: Request) -> Response:
        return Response({"unread": self.get_queryset().filter(read_at__isnull=True).count()})


class PreferenceSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = NotificationPreference
        fields = ["id", "channel", "alert_type", "min_severity", "enabled"]


class PreferencesViewSet(TenantViewSet):
    """The signed-in user's preferences and quiet hours. PUT replaces the whole set."""

    tenant_policy = "self"
    serializer_class = PreferenceSerializer
    required_permissions = {"list": "notifications.view", "replace": "notifications.view"}

    @extend_schema(responses=inline_serializer("Preferences", {
        "preferences": PreferenceSerializer(many=True), "quiet_hours_start": serializers.TimeField(allow_null=True),
        "quiet_hours_end": serializers.TimeField(allow_null=True), "time_zone": serializers.CharField(),
        "alert_types": serializers.ListField(child=serializers.CharField())}))
    def list(self, request: Request) -> Response:
        u = request.user
        prefs = NotificationPreference.objects.filter(user=u)
        return Response({"preferences": PreferenceSerializer(prefs, many=True).data,
                         "quiet_hours_start": u.quiet_hours_start, "quiet_hours_end": u.quiet_hours_end,
                         "time_zone": u.time_zone, "alert_types": [k for k, _ in ALERT_TYPES]})

    @extend_schema(request=inline_serializer("PreferencesIn", {
        "preferences": PreferenceSerializer(many=True), "quiet_hours_start": serializers.TimeField(allow_null=True),
        "quiet_hours_end": serializers.TimeField(allow_null=True), "time_zone": serializers.CharField()}),
        responses=inline_serializer("PreferencesOut", {"preferences": PreferenceSerializer(many=True)}))
    @action(detail=False, methods=["put"])
    def replace(self, request: Request) -> Response:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        prefs = PreferenceSerializer(data=request.data.get("preferences", []), many=True)
        prefs.is_valid(raise_exception=True)
        tz = request.data.get("time_zone") or request.user.time_zone
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ApiError("invalid_time_zone", "Unknown time zone.") from exc
        quiet = serializers.TimeField(allow_null=True)
        start = quiet.run_validation(request.data.get("quiet_hours_start"))
        end = quiet.run_validation(request.data.get("quiet_hours_end"))
        if (start is None) != (end is None):
            raise ApiError("quiet_hours", "Set both a start and an end for quiet hours, or neither.")
        with transaction.atomic():
            NotificationPreference.objects.filter(user=request.user).delete()
            for p in prefs.validated_data:
                NotificationPreference.objects.create(organization=self.tenant.organization, user=request.user, **p)
            u = request.user
            u.quiet_hours_start, u.quiet_hours_end, u.time_zone = start, end, tz
            u.save(update_fields=["quiet_hours_start", "quiet_hours_end", "time_zone"])
        return self.list(request)


class ChannelSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = NotificationChannel
        fields = ["id", "kind", "provider", "settings", "is_enabled", "updated_at"]
        read_only_fields = ["id", "updated_at"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        if kind in ("sms", "whatsapp", "push") and attrs.get("is_enabled"):
            raise serializers.ValidationError({"is_enabled": f"No {kind} provider is installed yet; the settings are "
                                                             "stored for when one is added."})
        return attrs


class ChannelViewSet(TenantCrudViewSet):
    model = NotificationChannel
    audit_resource = "notification_channels"
    soft_delete = False
    serializer_class = ChannelSerializer
    unique_error = ("channel_exists", "This channel is already configured.")
    unique_error_field = "kind"
    required_permissions = {"list": "alerts.manage", "retrieve": "alerts.manage", "create": "alerts.manage",
                            "update": "alerts.manage", "partial_update": "alerts.manage", "destroy": "alerts.manage"}
