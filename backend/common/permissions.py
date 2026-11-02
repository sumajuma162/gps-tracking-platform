"""DRF permission classes. Every action must declare its permission (deny by default)."""
from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request


class HasTenantPermission(BasePermission):
    """Checks ``view.required_permissions[action]`` against the resolved tenant's permissions.

    An action missing from the map is denied, so new endpoints cannot be exposed by accident.
    ``None`` as the value means "any member of the organization".
    """

    message = "You don't have permission to do this in this organization."

    def has_permission(self, request: Request, view: Any) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        tenant = getattr(request, "tenant", None)
        required: dict[str, str | tuple[str, ...] | None] = getattr(view, "required_permissions", {})
        action = getattr(view, "action", None) or request.method.lower()
        if tenant is None or action not in required:
            return False
        needed = required[action]
        if needed is None:              # "any member": personal views, never through an API key
            return tenant.via != "api_key"
        if isinstance(needed, tuple):           # every listed permission is required
            return all(p in tenant.permissions for p in needed)
        return needed in tenant.permissions


class IsPlatformAdmin(BasePermission):
    message = "Only platform Super Administrators can do this."

    def has_permission(self, request: Request, view: Any) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_platform_admin)
