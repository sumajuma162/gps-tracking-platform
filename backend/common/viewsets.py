"""Base viewsets that bind every request to one organization (spec A8)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from django.db.models import Model
from rest_framework import exceptions, viewsets
from rest_framework.request import Request
from rest_framework.response import Response

from common.exceptions import ApiError
from common.permissions import HasTenantPermission, IsPlatformAdmin
from common.rls import rls_bypass, set_current_org
from common.tenancy import activate_org, deactivate_org


@dataclass(frozen=True)
class Tenant:
    organization: Any
    permissions: frozenset[str]
    membership: Any = None
    via: str = "membership"  # or "break_glass"
    extra: dict[str, Any] = field(default_factory=dict)


def resolve_tenant(request: Request) -> Tenant:
    from apps.organizations.models import BreakGlassGrant, OrganizationMembership
    from apps.rbac.catalog import BREAK_GLASS_PERMISSIONS
    from apps.rbac.services import role_permissions

    user = request.user
    key = getattr(request, "auth", None)
    if key is not None and type(key).__name__ == "ApiKey":
        return _api_key_tenant(request, key)
    memberships = (OrganizationMembership.unscoped
                   .filter(user=user, is_active=True, organization__is_active=True,
                           organization__deleted_at__isnull=True)
                   .select_related("organization", "role"))
    header = request.headers.get("X-Organization-Id", "").strip()
    if header:
        try:
            org_id = int(header)
        except ValueError as exc:
            raise ApiError("organization_invalid", "X-Organization-Id must be a number.") from exc
        membership = memberships.filter(organization_id=org_id).first()
        if membership:
            return Tenant(membership.organization, role_permissions(membership.role), membership)
        if user.is_platform_admin:
            grant = (BreakGlassGrant.objects.select_related("organization")
                     .filter(user=user, organization_id=org_id, revoked_at__isnull=True).order_by("-expires_at")
                     .first())
            if grant and grant.is_active:
                return Tenant(grant.organization, frozenset(BREAK_GLASS_PERMISSIONS), via="break_glass")
        raise exceptions.NotFound("Organization not found or you are not a member.")
    found = list(memberships[:2])
    if len(found) == 1:
        return Tenant(found[0].organization, role_permissions(found[0].role), found[0])
    if not found:
        raise ApiError("no_organization", "Your account is not a member of any active organization.", 403)
    raise ApiError("organization_required", "Choose an organization (X-Organization-Id header).", 400)


def _api_key_tenant(request: Request, key: Any) -> Tenant:
    """An API key acts in its own organization only, with its scopes narrowed to what its creator still holds."""
    from apps.organizations.models import OrganizationMembership
    from apps.rbac.services import role_permissions

    header = request.headers.get("X-Organization-Id", "").strip()
    if header and header != str(key.organization_id):
        raise exceptions.NotFound("Organization not found or you are not a member.")
    membership = (OrganizationMembership.unscoped.select_related("organization", "role")
                  .filter(user=key.created_by, organization_id=key.organization_id, is_active=True,
                          organization__is_active=True, organization__deleted_at__isnull=True).first())
    if membership is None:
        raise exceptions.AuthenticationFailed("This API key's owner no longer belongs to the organization.")
    return Tenant(membership.organization, role_permissions(membership.role) & frozenset(key.scopes), membership,
                  via="api_key", extra={"api_key_id": key.pk})


class TenantViewSet(viewsets.GenericViewSet):  # type: ignore[type-arg]
    """All data access is scoped to ``request.tenant.organization``.

    Subclasses set ``required_permissions = {action: codename | None}``.
    """

    tenant_policy: ClassVar[str] = "tenant"
    permission_classes = [HasTenantPermission]
    required_permissions: ClassVar[dict[str, str | None]] = {}

    def get_authenticators(self) -> list[Any]:
        """Organization routes also accept API keys (spec A13); account and platform routes never do."""
        from apps.integrations.authentication import ApiKeyAuthentication

        return [*super().get_authenticators(), ApiKeyAuthentication()]

    def perform_authentication(self, request: Request) -> None:
        super().perform_authentication(request)
        if request.user and request.user.is_authenticated:
            tenant = resolve_tenant(request)
            request.tenant = tenant  # type: ignore[attr-defined]
            self._org_token = activate_org(tenant.organization.pk)
            set_current_org(tenant.organization.pk)

    def finalize_response(self, request: Request, response: Response, *args: Any, **kwargs: Any) -> Response:
        token = getattr(self, "_org_token", None)
        if token is not None:
            deactivate_org(token)
            self._org_token = None
        return super().finalize_response(request, response, *args, **kwargs)

    @property
    def tenant(self) -> Tenant:
        return self.request.tenant  # type: ignore[attr-defined, no-any-return]

    def get_object(self) -> Model:  # 404 (never 403) outside the tenant: get_queryset is already scoped
        return super().get_object()  # type: ignore[no-any-return]


class PlatformViewSet(viewsets.GenericViewSet):  # type: ignore[type-arg]
    """Cross-tenant endpoints for Super Administrators only."""

    tenant_policy: ClassVar[str] = "platform"
    permission_classes = [IsPlatformAdmin]

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        self._bypass = rls_bypass()
        self._bypass.__enter__()

    def finalize_response(self, request: Request, response: Response, *args: Any, **kwargs: Any) -> Response:
        bypass = getattr(self, "_bypass", None)
        if bypass is not None:
            bypass.__exit__(None, None, None)
            self._bypass = None
        return super().finalize_response(request, response, *args, **kwargs)
