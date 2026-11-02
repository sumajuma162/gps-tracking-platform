"""Tenant scoping for ORM access (spec A8).

Tenant-owned models inherit ``TenantModel``. Their default manager ``objects``
only works inside an organization scope; ``unscoped`` is the explicit escape
hatch for platform services. Reverse relations use the unscoped base manager.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from django.db import models

_current_org: ContextVar[int | None] = ContextVar("current_org", default=None)


class TenantScopeError(RuntimeError):
    """Raised when tenant data is queried without an organization scope."""


def current_org_id() -> int | None:
    return _current_org.get()


def activate_org(org_id: int | None) -> Token[int | None]:
    return _current_org.set(org_id)


def deactivate_org(token: Token[int | None]) -> None:
    _current_org.reset(token)


@contextmanager
def tenant_context(org_id: int) -> Iterator[None]:
    token = activate_org(org_id)
    try:
        yield
    finally:
        deactivate_org(token)


class ScopelessQuerySet(models.QuerySet):  # type: ignore[type-arg]
    """A tenant queryset built outside any organization scope.

    It can be constructed and chained (frameworks such as DRF build validator querysets when a serializer
    class is set up), but any attempt to read or write data raises ``TenantScopeError``.
    """

    def _guard(self, *args: Any, **kwargs: Any) -> Any:
        raise TenantScopeError(f"{self.model.__name__} queried without an organization scope")

    _fetch_all = count = exists = aggregate = update = delete = iterator = explain = contains = _guard
    get = first = last = earliest = latest = in_bulk = _guard


class TenantManager(models.Manager):  # type: ignore[type-arg]
    def get_queryset(self) -> models.QuerySet[Any]:
        org_id = current_org_id()
        if org_id is None:
            return ScopelessQuerySet(model=self.model, using=self._db)
        return super().get_queryset().filter(organization_id=org_id)


class TenantModel(models.Model):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="+", db_index=True)

    objects = TenantManager()
    unscoped = models.Manager()  # noqa: DJ012 - ruff misreads a Manager() as a field

    class Meta:
        abstract = True
        base_manager_name = "unscoped"
        default_manager_name = "objects"
