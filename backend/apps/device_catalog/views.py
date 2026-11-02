"""Device catalog API. Readable by signed-in users where the Add Device flow needs it; writable only by
platform Super Administrators. IO profiles and listener ports are platform-only."""
from __future__ import annotations

from typing import Any, ClassVar

from django.db import transaction
from django.db.models import Max, QuerySet
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.services import record
from apps.device_catalog import serializers as s
from apps.device_catalog.models import (
    CommandTemplate,
    DeviceManufacturer,
    DeviceModel,
    DeviceProtocol,
    GenericFormat,
    IoMappingProfile,
    IoMappingProfileVersion,
    ListenerPort,
)
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import PlatformViewSet


class ReadAllWritePlatform(permissions.BasePermission):
    message = "Only platform Super Administrators can change the device catalog."

    def has_permission(self, request: Request, view: Any) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False
        return request.method in permissions.SAFE_METHODS or bool(request.user.is_platform_admin)


class AuditedCatalogMixin:
    audit_resource: ClassVar[str] = "catalog"
    request: Request

    def _audit(self, action: str, instance: Any, metadata: dict[str, Any] | None = None) -> None:
        record(f"catalog.{self.audit_resource}.{action}", user=self.request.user, resource=instance,
               ip=client_ip(self.request), metadata={"label": str(instance), **(metadata or {})})

    def _save(self, serializer: Any) -> Any:
        from django.db import IntegrityError

        try:
            with transaction.atomic():
                return serializer.save()
        except IntegrityError as exc:
            raise ApiError("duplicate", "Something with the same unique values already exists.") from exc

    def perform_create(self, serializer: Any) -> None:
        self._audit("created", self._save(serializer))

    def perform_update(self, serializer: Any) -> None:
        self._audit("updated", self._save(serializer), {"fields": sorted(serializer.validated_data)})

    def perform_destroy(self, instance: Any) -> None:
        from django.db.models import ProtectedError

        try:
            instance.delete()
        except ProtectedError as exc:
            raise ApiError("in_use", "This is still used by other records. Deprecate or disable it instead.") from exc
        self._audit("deleted", instance)


class CatalogViewSet(AuditedCatalogMixin, viewsets.ModelViewSet):  # type: ignore[type-arg]
    tenant_policy: ClassVar[str] = "catalog"
    permission_classes = [ReadAllWritePlatform]


class ManufacturerViewSet(CatalogViewSet):
    audit_resource = "manufacturer"
    queryset = DeviceManufacturer.objects.all()
    serializer_class = s.ManufacturerSerializer


class ProtocolViewSet(AuditedCatalogMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      mixins.UpdateModelMixin, viewsets.GenericViewSet):  # type: ignore[type-arg]
    """Protocols come from the gateway's decoder registry; administrators can only enable or disable them."""

    tenant_policy: ClassVar[str] = "catalog"
    permission_classes = [ReadAllWritePlatform]
    audit_resource = "protocol"
    queryset = DeviceProtocol.objects.all()
    serializer_class = s.ProtocolSerializer


class DeviceModelViewSet(CatalogViewSet):
    audit_resource = "device_model"
    serializer_class = s.DeviceModelSerializer

    def get_queryset(self) -> QuerySet[DeviceModel]:
        qs = DeviceModel.objects.select_related("manufacturer", "protocol")
        if self.request.query_params.get("active") == "1":
            qs = qs.filter(status="active")
        if term := self.request.query_params.get("search", "").strip():
            from django.db.models import Q

            qs = qs.filter(Q(name__icontains=term) | Q(slug__icontains=term) | Q(manufacturer__name__icontains=term))
        return qs

    def perform_update(self, serializer: Any) -> None:
        from apps.devices.events import catalog_changed

        super().perform_update(serializer)
        catalog_changed()

    @extend_schema(request=s.CloneModelSerializer, responses=s.DeviceModelSerializer)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def clone(self, request: Request, pk: str | None = None) -> Response:
        """Copy a model under a new name, unverified (spec A7). Its IO profile is copied too, so editing the
        clone's mappings never changes the original model's trackers."""
        source: DeviceModel = self.get_object()
        data = s.CloneModelSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        copy = DeviceModel.objects.get(pk=source.pk)
        copy.pk = None
        copy.name, copy.slug = data.validated_data["name"], data.validated_data["slug"]
        copy.verified = False
        if source.io_profile is not None:
            profile = IoMappingProfile.objects.create(
                name=f"{source.manufacturer.name} {copy.name}", slug=f"{copy.slug}-io"[:80],
                protocol=source.io_profile.protocol, verified=False,
                description=f"Copied from {source.io_profile.name}.")
            version = IoMappingProfileVersion.objects.create(
                profile=profile, version=1, notes=f"Copied from {source.io_profile.slug}",
                mappings=source.io_profile.current_version.mappings if source.io_profile.current_version else [],
                created_by=request.user)
            profile.current_version = version
            profile.save(update_fields=["current_version"])
            copy.io_profile = profile
        copy.save()
        self._audit("cloned", copy, {"from": source.slug})
        return Response(s.DeviceModelSerializer(copy).data, status=status.HTTP_201_CREATED)


class CommandTemplateViewSet(CatalogViewSet):
    audit_resource = "command_template"
    serializer_class = s.CommandTemplateSerializer

    def get_queryset(self) -> QuerySet[CommandTemplate]:
        qs = CommandTemplate.objects.select_related("device_model")
        if model := self.request.query_params.get("device_model"):
            qs = qs.filter(device_model_id=model)
        return qs


class PreviewSerializer(serializers.Serializer):  # type: ignore[type-arg]
    mappings = serializers.JSONField()


SAMPLE_IO = {"239": 1, "240": 1, "21": 4, "66": 12650, "67": 4012, "16": 123456, "182": 9, "69": 1, "1": 0}


class IoProfilePreviewMixin:
    @extend_schema(request=PreviewSerializer, responses=inline_serializer("ProfilePreview", {
        "source": serializers.CharField(), "io": serializers.DictField(), "current": serializers.DictField(),
        "draft": serializers.DictField()}))
    @action(detail=True, methods=["post"])
    def preview(self, request: Request, pk: str | None = None) -> Response:
        """Apply draft mappings to the latest real IO values of a tracker using this profile (spec A7)."""
        from apps.device_catalog.io_schema import ProfileInvalid, validate_mappings
        from apps.device_catalog.mapping import apply_mappings
        from apps.tracking.models import Position

        profile: IoMappingProfile = self.get_object()  # type: ignore[attr-defined]
        data = PreviewSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            draft = validate_mappings(data.validated_data["mappings"])
        except ProfileInvalid as exc:
            raise serializers.ValidationError({"mappings": exc.errors}) from exc
        latest = (Position.objects.filter(device__device_model__io_profile=profile)
                  .exclude(raw_metadata__io={}).order_by("-device_time").values("raw_metadata", "device_time",
                                                                                 "device__name").first())
        if latest:
            io = latest["raw_metadata"].get("io", {})
            source = f"{latest['device__name']} at {latest['device_time']:%Y-%m-%d %H:%M} UTC"
        else:
            io, source = SAMPLE_IO, "sample"
        current = profile.current_version.mappings if profile.current_version else []
        return Response({"source": source, "io": io, "current": apply_mappings(current, io),
                         "draft": apply_mappings(draft, io)})


class IoProfileViewSet(IoProfilePreviewMixin, AuditedCatalogMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                       mixins.CreateModelMixin, mixins.UpdateModelMixin, PlatformViewSet):
    """Editing mappings always creates a new immutable version; rollback re-points to an older one."""

    audit_resource = "io_profile"
    serializer_class = s.IoProfileSerializer

    def get_queryset(self) -> QuerySet[IoMappingProfile]:
        return IoMappingProfile.objects.select_related("current_version__created_by").prefetch_related(
            "device_models__manufacturer")

    def _new_version(self, profile: IoMappingProfile, mappings: list[dict[str, Any]], notes: str) -> None:
        from apps.devices.events import catalog_changed

        latest = profile.versions.aggregate(m=Max("version"))["m"] or 0
        version = IoMappingProfileVersion.objects.create(profile=profile, version=latest + 1, mappings=mappings,
                                                         notes=notes, created_by=self.request.user)
        profile.current_version = version
        profile.save(update_fields=["current_version", "updated_at"])
        catalog_changed()

    @transaction.atomic
    def perform_create(self, serializer: Any) -> None:
        mappings = serializer.validated_data.pop("mappings")
        notes = serializer.validated_data.pop("notes", "")
        profile = serializer.save()
        self._new_version(profile, mappings, notes or "Created")
        self._audit("created", profile, {"version": 1})

    @transaction.atomic
    def perform_update(self, serializer: Any) -> None:
        mappings = serializer.validated_data.pop("mappings", None)
        notes = serializer.validated_data.pop("notes", "")
        profile = serializer.save()
        if mappings is not None:
            self._new_version(profile, mappings, notes)
        self._audit("updated", profile, {"new_version": profile.current_version.version if mappings is not None
                                         else None})

    @extend_schema(responses=s.ProfileVersionSerializer(many=True))
    @action(detail=True, methods=["get"])
    def versions(self, request: Request, pk: str | None = None) -> Response:
        profile = self.get_object()
        return Response(s.ProfileVersionSerializer(profile.versions.select_related("created_by"), many=True).data)

    @extend_schema(request=inline_serializer("RollbackRequest", {"version": serializers.IntegerField()}),
                   responses=s.IoProfileSerializer)
    @action(detail=True, methods=["post"])
    def rollback(self, request: Request, pk: str | None = None) -> Response:
        profile = self.get_object()
        target = profile.versions.filter(version=request.data.get("version")).first()
        if target is None:
            raise ApiError("version_not_found", "That version does not exist for this profile.", 404)
        profile.current_version = target
        profile.save(update_fields=["current_version", "updated_at"])
        from apps.devices.events import catalog_changed

        catalog_changed()
        self._audit("rolled_back", profile, {"version": target.version})
        return Response(s.IoProfileSerializer(profile).data)


class ListenerPortViewSet(AuditedCatalogMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin,
                          PlatformViewSet):
    audit_resource = "listener_port"
    serializer_class = s.ListenerPortSerializer
    queryset = ListenerPort.objects.select_related("protocol")

    def _audit(self, action: str, instance: Any, metadata: dict[str, Any] | None = None) -> None:
        from apps.devices.events import listener_ports_changed

        super()._audit(action, instance, metadata)
        listener_ports_changed()


class TestParseSerializer(serializers.Serializer):  # type: ignore[type-arg]
    sample = serializers.CharField(max_length=65536, trim_whitespace=False)
    definition = serializers.DictField(required=False, help_text="A draft definition; default: the saved one.")


class GenericFormatViewSet(AuditedCatalogMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin,
                           PlatformViewSet):
    """UI-defined tracker formats (spec A7). Changes reach the gateway within seconds: HTTP formats are re-read
    after 10 s, text listeners are restarted by the listener reconcile."""

    audit_resource = "generic_format"
    serializer_class = s.GenericFormatSerializer
    queryset = GenericFormat.objects.prefetch_related("device_models")

    def _audit(self, action: str, instance: Any, metadata: dict[str, Any] | None = None) -> None:
        from apps.devices.events import catalog_changed, listener_ports_changed

        super()._audit(action, instance, metadata)
        listener_ports_changed()
        catalog_changed()

    @extend_schema(request=TestParseSerializer, responses=inline_serializer("TestParseResult", {
        "ok": serializers.BooleanField(), "stage": serializers.CharField(), "records": serializers.ListField(),
        "errors": serializers.ListField()}))
    @action(detail=True, methods=["post"], url_path="test-parse")
    def test_parse(self, request: Request, pk: str | None = None) -> Response:
        """Run a sample message through the gateway's parser with this (or a draft) definition."""
        from apps.device_catalog.generic import gateway_parse

        fmt = self.get_object()
        data = TestParseSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        definition = {**data.validated_data.get("definition", fmt.definition), "kind": fmt.kind, "slug": fmt.slug}
        return Response(gateway_parse(definition, data.validated_data["sample"]))
