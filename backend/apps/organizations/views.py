from __future__ import annotations

from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.services import record
from apps.organizations import services
from apps.organizations.models import Organization, OrganizationMembership
from apps.organizations.serializers import MembershipSerializer, OrganizationSerializer
from apps.rbac.models import Role
from apps.rbac.services import role_permissions
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import TenantViewSet


class OrganizationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                          mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):  # type: ignore[type-arg]
    """Super Administrators manage all organizations; members see (and admins edit) their own."""

    tenant_policy: ClassVar[str] = "membership"
    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[Organization]:
        qs = Organization.objects.filter(deleted_at__isnull=True)
        if getattr(self, "swagger_fake_view", False):
            return qs.none()
        user = self.request.user
        if user.is_platform_admin:
            return qs
        return qs.filter(memberships_set_filter(user), is_active=True).distinct()

    def _require_platform(self) -> None:
        if not self.request.user.is_platform_admin:
            raise ApiError("forbidden", "Only platform Super Administrators can do this.", 403)

    def perform_create(self, serializer: Any) -> None:
        self._require_platform()
        admin_email = serializer.validated_data.pop("admin_email", None)
        org = serializer.save()
        record("organizations.created", user=self.request.user, organization=org, resource=org,
               ip=client_ip(self.request), metadata={"name": org.name})
        if admin_email:
            services.add_member(org, admin_email, "", Role.objects.get(slug="org_admin", organization=None),
                                self.request.user, client_ip(self.request))

    def perform_update(self, serializer: Any) -> None:
        org: Organization = serializer.instance
        user = self.request.user
        if not user.is_platform_admin:
            membership = OrganizationMembership.unscoped.filter(user=user, organization=org, is_active=True) \
                .select_related("role").first()
            if membership is None or "organizations.manage" not in role_permissions(membership.role):
                raise ApiError("forbidden", "You don't have permission to edit this organization.", 403)
        serializer.validated_data.pop("admin_email", None)
        changed = {k: v for k, v in serializer.validated_data.items() if getattr(org, k) != v}
        serializer.save()
        record("organizations.updated", user=user, organization=org, resource=org, ip=client_ip(self.request),
               metadata={"changed": sorted(changed)})

    def perform_destroy(self, instance: Organization) -> None:
        self._require_platform()
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        record("organizations.deactivated", user=self.request.user, organization=instance, resource=instance,
               ip=client_ip(self.request))

    @extend_schema(request=inline_serializer("BreakGlassRequest", {"reason": serializers.CharField(min_length=10)}),
                   responses={201: inline_serializer("BreakGlassGrantOut", {
                       "organization_id": serializers.IntegerField(), "expires_at": serializers.DateTimeField(),
                       "access": serializers.CharField()})})
    @action(detail=True, methods=["post"], url_path="break-glass")
    def break_glass(self, request: Request, pk: str | None = None) -> Response:
        self._require_platform()
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 10:
            raise serializers.ValidationError({"reason": "Explain why you need access (at least 10 characters)."})
        grant = services.grant_break_glass(request.user, self.get_object(), reason, client_ip(request))
        return Response({"organization_id": grant.organization_id, "expires_at": grant.expires_at,
                         "access": "read-only"}, status=status.HTTP_201_CREATED)


def memberships_set_filter(user: Any) -> Any:
    from django.db.models import Exists, OuterRef

    return Exists(OrganizationMembership.unscoped.filter(organization=OuterRef("pk"), user=user, is_active=True))


class MembershipViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        mixins.UpdateModelMixin, mixins.DestroyModelMixin, TenantViewSet):
    """Organization users (memberships). Deleting deactivates the membership, never the account."""

    serializer_class = MembershipSerializer
    required_permissions = {"list": "users.view", "retrieve": "users.view", "create": "users.manage",
                            "update": "users.manage", "partial_update": "users.manage", "destroy": "users.manage",
                            "reactivate": "users.manage"}

    def get_queryset(self) -> QuerySet[OrganizationMembership]:
        if getattr(self, "swagger_fake_view", False):
            return OrganizationMembership.unscoped.none()
        qs = OrganizationMembership.objects.select_related("user", "role")
        if self.action != "reactivate" and self.request.query_params.get("include_inactive") != "1":
            qs = qs.filter(is_active=True)
        search = self.request.query_params.get("search", "").strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(Q(user__email__icontains=search) | Q(user__full_name__icontains=search))
        return qs

    def perform_create(self, serializer: Any) -> None:
        d = serializer.validated_data
        serializer.instance = services.add_member(self.tenant.organization, d["email"], d.get("full_name", ""),
                                                  d["role"], self.request.user, client_ip(self.request))

    def perform_update(self, serializer: Any) -> None:
        if "role" in serializer.validated_data:
            services.change_role(serializer.instance, serializer.validated_data["role"], self.request.user,
                                 client_ip(self.request))

    def perform_destroy(self, instance: OrganizationMembership) -> None:
        services.deactivate_member(instance, self.request.user, client_ip(self.request))

    @extend_schema(request=None, responses=MembershipSerializer)
    @action(detail=True, methods=["post"])
    def reactivate(self, request: Request, pk: str | None = None) -> Response:
        membership = self.get_object()
        membership.is_active = True
        membership.save(update_fields=["is_active", "updated_at"])
        record("users.reactivated", user=request.user, organization=self.tenant.organization, resource=membership,
               ip=client_ip(request), metadata={"email": membership.user.email})
        return Response(MembershipSerializer(membership, context={"request": request}).data)

