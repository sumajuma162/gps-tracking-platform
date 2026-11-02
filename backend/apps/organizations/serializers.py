from __future__ import annotations

from typing import Any

from django.utils.text import slugify
from rest_framework import serializers

from apps.organizations.models import Organization, OrganizationMembership, validate_timezone
from apps.rbac.models import Role


class OrganizationSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    admin_email = serializers.EmailField(write_only=True, required=False,
                                         help_text="Creates/invites the first Organization Administrator.")
    timezone = serializers.CharField(validators=[validate_timezone], required=False)

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "timezone", "language", "distance_unit", "country", "is_active",
                  "created_at", "updated_at", "admin_email"]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {"slug": {"required": False}}

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if self.instance is None and not attrs.get("slug"):
            base = slugify(attrs.get("name", ""))[:70] or "organization"
            slug, n = base, 2
            while Organization.objects.filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            attrs["slug"] = slug
        request = self.context.get("request")
        if "is_active" in attrs and not (request and request.user.is_platform_admin):
            attrs.pop("is_active")
        return attrs


class MembershipSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    email = serializers.EmailField(write_only=True, required=False)
    full_name = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=150)
    role_id = serializers.PrimaryKeyRelatedField(source="role", queryset=Role.objects.all(), write_only=True)
    user = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationMembership
        fields = ["id", "user", "role", "is_active", "created_at", "email", "full_name", "role_id"]
        read_only_fields = ["id", "is_active", "created_at"]

    def get_user(self, obj: OrganizationMembership) -> dict[str, Any]:
        u = obj.user
        return {"id": u.pk, "email": u.email, "full_name": u.full_name, "last_login": u.last_login,
                "is_locked": u.is_locked()}

    def get_role(self, obj: OrganizationMembership) -> dict[str, Any]:
        return {"id": obj.role.pk, "slug": obj.role.slug, "name": obj.role.name}

    def validate_role_id(self, role: Role) -> Role:
        org = self.context["request"].tenant.organization
        if role.organization_id not in (None, org.pk):
            raise serializers.ValidationError("Role not found.")
        return role

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if self.instance is None and not attrs.get("email"):
            raise serializers.ValidationError({"email": "Email is required."})
        if self.instance is not None:
            attrs.pop("email", None)
            attrs.pop("full_name", None)
        return attrs
