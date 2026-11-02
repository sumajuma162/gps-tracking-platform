from __future__ import annotations

from django.db import models


class Permission(models.Model):
    codename = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=200)

    class Meta:
        db_table = "permissions"
        ordering = ["codename"]

    def __str__(self) -> str:
        return self.codename


class Role(models.Model):
    """System roles have organization=None and is_system=True; custom roles belong to one organization."""

    organization = models.ForeignKey("organizations.Organization", null=True, blank=True,
                                     on_delete=models.CASCADE, related_name="roles")
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=100)
    is_system = models.BooleanField(default=False)
    permissions = models.ManyToManyField(Permission, related_name="roles", db_table="role_permissions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "roles"
        ordering = ["-is_system", "name"]
        constraints = [
            models.UniqueConstraint(fields=["slug"], condition=models.Q(organization__isnull=True),
                                    name="uniq_system_role_slug"),
            models.UniqueConstraint(fields=["organization", "slug"], name="uniq_org_role_slug"),
        ]

    def __str__(self) -> str:
        return self.name

    def codenames(self) -> set[str]:
        return set(self.permissions.values_list("codename", flat=True))
