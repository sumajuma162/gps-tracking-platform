from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.accounts.models import User, UserSession


class LoginSerializer(serializers.Serializer):  # type: ignore[type-arg]
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class PasswordChangeSerializer(serializers.Serializer):  # type: ignore[type-arg]
    current_password = serializers.CharField(max_length=256, trim_whitespace=False)
    new_password = serializers.CharField(max_length=256, trim_whitespace=False)


class PasswordForgotSerializer(serializers.Serializer):  # type: ignore[type-arg]
    email = serializers.EmailField(max_length=254)


class PasswordResetSerializer(serializers.Serializer):  # type: ignore[type-arg]
    uid = serializers.CharField(max_length=64)
    token = serializers.CharField(max_length=128)
    new_password = serializers.CharField(max_length=256, trim_whitespace=False)


class ProfileSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = User
        fields = ["id", "email", "full_name", "language", "is_platform_admin", "last_login", "date_joined"]
        read_only_fields = ["id", "email", "is_platform_admin", "last_login", "date_joined"]


class SessionSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = ["id", "created_at", "last_used_at", "expires_at", "ip_address", "user_agent", "current"]

    def get_current(self, obj: UserSession) -> bool:
        return str(obj.pk) == self.context.get("current_session")


def memberships_payload(user: User) -> list[dict[str, Any]]:
    from apps.organizations.models import OrganizationMembership
    from apps.rbac.services import role_permissions

    rows = (OrganizationMembership.unscoped
            .filter(user=user, is_active=True, organization__is_active=True, organization__deleted_at__isnull=True)
            .select_related("organization", "role").order_by("organization__name"))
    return [{
        "organization": {"id": m.organization.pk, "name": m.organization.name, "slug": m.organization.slug,
                         "timezone": m.organization.timezone, "language": m.organization.language,
                         "distance_unit": m.organization.distance_unit},
        "role": {"id": m.role.pk, "slug": m.role.slug, "name": m.role.name},
        "permissions": sorted(role_permissions(m.role)),
    } for m in rows]
