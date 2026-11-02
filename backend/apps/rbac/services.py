from __future__ import annotations

from django.core.cache import cache

from apps.rbac.models import Role


def role_permissions(role: Role) -> frozenset[str]:
    key = f"rbac:role:{role.pk}:{role.updated_at.timestamp() if role.updated_at else 0}"
    cached = cache.get(key)
    if cached is None:
        cached = frozenset(role.codenames())
        cache.set(key, cached, 300)
    return frozenset(cached)
