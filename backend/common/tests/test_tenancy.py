"""The scoped manager: unscoped tenant data can be described but never read or written."""
from __future__ import annotations

import pytest

from apps.fleet.models import Vehicle
from common.tenancy import TenantScopeError, tenant_context

pytestmark = pytest.mark.django_db


def test_unscoped_access_raises_on_any_evaluation(org_a):
    Vehicle.unscoped.create(organization=org_a, registration_number="T 1 A")
    qs = Vehicle.objects.filter(registration_number="T 1 A")  # building is allowed (DRF validators do this)
    for evaluate in (list, len, lambda q: q.count(), lambda q: q.exists(), lambda q: q.first(),
                     lambda q: q.get(), lambda q: q.update(color="red"), lambda q: q.delete(),
                     lambda q: list(q.values("id")), lambda q: q.aggregate()):
        with pytest.raises(TenantScopeError):
            evaluate(qs)


def test_scoped_access_sees_only_the_tenant(org_a, org_b):
    Vehicle.unscoped.create(organization=org_a, registration_number="T 1 A")
    Vehicle.unscoped.create(organization=org_b, registration_number="T 2 B")
    with tenant_context(org_a.pk):
        assert list(Vehicle.objects.values_list("registration_number", flat=True)) == ["T 1 A"]
    with tenant_context(org_b.pk):
        assert Vehicle.objects.get().registration_number == "T 2 B"
