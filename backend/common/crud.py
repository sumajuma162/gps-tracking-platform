"""Shared CRUD behaviour for tenant resources: scoping, soft delete, audit trail (spec A13)."""
from __future__ import annotations

from typing import Any, ClassVar

from django.db import IntegrityError, transaction
from django.db.models import Model, QuerySet
from django.utils import timezone
from rest_framework import mixins

from apps.audit.services import record
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import TenantViewSet


def changed_fields(instance: Model, data: dict[str, Any]) -> list[str]:
    out = []
    for key, value in data.items():
        current = getattr(instance, key, None)
        if hasattr(current, "all"):  # many-to-many
            continue
        if current != value:
            out.append(key)
    return sorted(out)


class TenantCrudViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        mixins.UpdateModelMixin, mixins.DestroyModelMixin, TenantViewSet):
    """Create/update/delete with organization stamping, soft delete and audit entries.

    Subclasses set ``model``, ``audit_resource`` (e.g. "vehicles") and ``required_permissions``.
    ``unique_error`` maps a database uniqueness failure to a friendly message.
    """

    model: ClassVar[type[Model]]
    audit_resource: ClassVar[str]
    soft_delete: ClassVar[bool] = True
    unique_error: ClassVar[tuple[str, str]] = ("duplicate", "This already exists.")
    #: Field the uniqueness conflict belongs to, so forms can show the message under that input.
    unique_error_field: ClassVar[str | None] = None
    search_fields: ClassVar[tuple[str, ...]] = ()

    def base_queryset(self) -> QuerySet[Any]:
        if getattr(self, "swagger_fake_view", False):
            return self.model.unscoped.none()  # type: ignore[attr-defined, no-any-return]
        qs = self.model.objects.all()  # type: ignore[attr-defined]
        if self.soft_delete:
            qs = qs.filter(deleted_at__isnull=True)
        return qs  # type: ignore[no-any-return]

    def get_queryset(self) -> QuerySet[Any]:
        qs = self.base_queryset()
        term = self.request.query_params.get("search", "").strip()
        if term and self.search_fields:
            from django.db.models import Q

            cond = Q()
            for field in self.search_fields:
                cond |= Q(**{f"{field}__icontains": term})
            qs = qs.filter(cond)
        return qs

    def _audit(self, action: str, instance: Model, metadata: dict[str, Any] | None = None) -> None:
        record(f"{self.audit_resource}.{action}", user=self.request.user, organization=self.tenant.organization,
               resource=instance, ip=client_ip(self.request), metadata=metadata or {},
               user_agent=self.request.META.get("HTTP_USER_AGENT", ""))

    def _save(self, serializer: Any, **extra: Any) -> Model:
        try:
            with transaction.atomic():
                return serializer.save(**extra)  # type: ignore[no-any-return]
        except IntegrityError as exc:
            code, message = self.unique_error
            details = {self.unique_error_field: [message]} if self.unique_error_field else None
            raise ApiError(code, message, 400, details) from exc

    def perform_create(self, serializer: Any) -> None:
        instance = self._save(serializer, organization=self.tenant.organization)
        self._audit("created", instance, {"label": str(instance)})
        self.after_change(instance, "created")

    def perform_update(self, serializer: Any) -> None:
        changes = changed_fields(serializer.instance, serializer.validated_data)
        instance = self._save(serializer)
        self._audit("updated", instance, {"changed": changes})
        self.after_change(instance, "updated")

    def perform_destroy(self, instance: Model) -> None:
        self.before_delete(instance)
        if self.soft_delete:
            instance.deleted_at = timezone.now()  # type: ignore[attr-defined]
            instance.save(update_fields=["deleted_at", "updated_at"])
        else:
            instance.delete()
        self._audit("deleted", instance, {"label": str(instance)})
        self.after_change(instance, "deleted")

    def before_delete(self, instance: Model) -> None:
        """Hook: close assignments etc. before a soft delete."""

    def after_change(self, instance: Model, action: str) -> None:
        """Hook: publish events after create/update/delete."""
