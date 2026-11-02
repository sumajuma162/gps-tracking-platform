"""Idempotently sync the permission catalog and system roles into the database."""
from __future__ import annotations

from typing import Any

from apps.rbac.catalog import PERMISSIONS, SYSTEM_ROLES


def sync_catalog(permission_model: Any, role_model: Any) -> None:
    perms = {}
    for code, desc in PERMISSIONS.items():
        obj, _ = permission_model.objects.update_or_create(codename=code, defaults={"description": desc})
        perms[code] = obj
    permission_model.objects.exclude(codename__in=PERMISSIONS).delete()
    for slug, (name, codes) in SYSTEM_ROLES.items():
        role, _ = role_model.objects.update_or_create(
            slug=slug, organization=None, defaults={"name": name, "is_system": True})
        role.permissions.set([perms[c] for c in codes])
