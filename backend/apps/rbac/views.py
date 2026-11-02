from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet
from django.utils.text import slugify
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record
from apps.rbac.catalog import PERMISSION_CHOICES, PERMISSIONS
from apps.rbac.models import Permission, Role
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import TenantViewSet


class RoleSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    permissions = serializers.ListField(child=serializers.ChoiceField(choices=PERMISSION_CHOICES), write_only=True)
    permission_codes = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ["id", "slug", "name", "is_system", "permissions", "permission_codes"]
        read_only_fields = ["id", "slug", "is_system"]

    def get_permission_codes(self, obj: Role) -> list[str]:
        return sorted(obj.codenames())


class RoleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                  mixins.UpdateModelMixin, mixins.DestroyModelMixin, TenantViewSet):
    """System roles (read-only) plus this organization's custom roles."""

    serializer_class = RoleSerializer
    required_permissions = {"list": None, "retrieve": None, "create": "roles.manage", "update": "roles.manage",
                            "partial_update": "roles.manage", "destroy": "roles.manage"}

    def get_queryset(self) -> QuerySet[Role]:
        if getattr(self, "swagger_fake_view", False):
            return Role.objects.none()
        org = self.tenant.organization
        return Role.objects.filter(Q(organization__isnull=True) | Q(organization=org)).prefetch_related("permissions")

    def _set_permissions(self, role: Role, codes: list[str]) -> None:
        role.permissions.set(Permission.objects.filter(codename__in=codes))
        role.save(update_fields=["updated_at"])  # bumps the permission cache key

    def perform_create(self, serializer: Any) -> None:
        org = self.tenant.organization
        codes = serializer.validated_data.pop("permissions")
        slug = slugify(serializer.validated_data["name"])[:60] or "role"
        if Role.objects.filter(Q(organization=org) | Q(organization__isnull=True), slug=slug).exists():
            raise ApiError("role_exists", "A role with this name already exists.")
        role = serializer.save(organization=org, slug=slug, is_system=False)
        self._set_permissions(role, codes)
        record("roles.created", user=self.request.user, organization=org, resource=role, ip=client_ip(self.request),
               metadata={"name": role.name, "permissions": sorted(codes)})

    def perform_update(self, serializer: Any) -> None:
        role: Role = serializer.instance
        if role.is_system:
            raise ApiError("system_role", "Built-in roles can't be changed. Create a custom role instead.", 403)
        codes = serializer.validated_data.pop("permissions", None)
        serializer.save()
        if codes is not None:
            self._set_permissions(role, codes)
        record("roles.updated", user=self.request.user, organization=role.organization, resource=role,
               ip=client_ip(self.request), metadata={"permissions": sorted(codes) if codes is not None else None})

    def perform_destroy(self, instance: Role) -> None:
        if instance.is_system:
            raise ApiError("system_role", "Built-in roles can't be deleted.", 403)
        if instance.memberships.exists():
            raise ApiError("role_in_use", "Move the users with this role to another role first.")
        record("roles.deleted", user=self.request.user, organization=instance.organization, ip=client_ip(self.request),
               metadata={"name": instance.name})
        instance.delete()


class PermissionCatalogView(APIView):
    @extend_schema(responses=inline_serializer("PermissionEntry", {"codename": serializers.CharField(),
                                                                   "description": serializers.CharField()}, many=True))
    def get(self, request: Request) -> Response:
        return Response([{"codename": c, "description": d} for c, d in sorted(PERMISSIONS.items())])
