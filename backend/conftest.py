"""Shared fixtures: two organizations, one user per role, authenticated API clients."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import User
from apps.organizations.models import Organization, OrganizationMembership
from apps.rbac.models import Role

PASSWORD = "Correct-Horse-Battery-9"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    cache.clear()


@pytest.fixture(autouse=True)
def _seed_data(request: pytest.FixtureRequest) -> None:
    """Make sure migration seed data exists (permission catalog, system roles, device catalog).

    Tests that commit for real (``transaction=True``) end with a database flush that removes it; re-running the
    migrations' own seed functions restores it for whatever runs next.
    """
    marker = request.node.get_closest_marker("django_db")
    if marker is None and not {"db", "transactional_db"} & set(request.fixturenames):
        return
    request.getfixturevalue("transactional_db" if marker and marker.kwargs.get("transaction") else "db")
    import importlib

    from django.apps import apps

    from apps.device_catalog.models import DeviceProtocol
    from apps.rbac.models import Permission, Role
    from apps.rbac.sync import sync_catalog

    if not Permission.objects.exists():
        sync_catalog(Permission, Role)
    if not DeviceProtocol.objects.exists():
        importlib.import_module("apps.device_catalog.migrations.0002_seed_catalog").seed(apps, None)
        importlib.import_module("apps.device_catalog.migrations.0004_seed_phase10").seed(apps, None)


@pytest.fixture
def org_a(db: Any) -> Organization:
    return Organization.objects.create(name="Demo Logistics Ltd", slug="demo-logistics")


@pytest.fixture
def org_b(db: Any) -> Organization:
    return Organization.objects.create(name="Coastal Transport Ltd", slug="coastal-transport")


@pytest.fixture
def make_member(db: Any) -> Callable[..., User]:
    def make(org: Organization, role: str, email: str | None = None) -> User:
        email = email or f"{role}@{org.slug}.test"
        user = User.objects.filter(email=email).first() or User.objects.create_user(email, PASSWORD,
                                                                                     full_name=role.title())
        OrganizationMembership.unscoped.create(organization=org, user=user,
                                               role=Role.objects.get(slug=role, organization__isnull=True))
        return user
    return make


@pytest.fixture
def superadmin(db: Any) -> User:
    return User.objects.create_superuser("root@platform.test", PASSWORD, full_name="Root")


def client_for(user: User, org: Organization | None = None) -> APIClient:
    issued = services._issue(user, "127.0.0.1", "pytest")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.access}",
                       **({"HTTP_X_ORGANIZATION_ID": str(org.pk)} if org else {}))
    client.session_obj = issued.session  # type: ignore[attr-defined]
    return client


@pytest.fixture
def api() -> Callable[..., APIClient]:
    return client_for


@pytest.fixture
def isolated_redis_url(settings):  # type: ignore[no-untyped-def]
    """Redis database 15 on the configured server, so tests never share streams or consumer groups with a dev
    stack running on database 0 (a live processor would otherwise consume the tests' entries)."""
    base, _, last = settings.REDIS_URL.rpartition("/")
    url = f"{base}/15" if last.isdigit() else f"{settings.REDIS_URL.rstrip('/')}/15"
    settings.REDIS_URL = url
    return url


@pytest.fixture(autouse=True)
def _gateway_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    """The web app asks the gateway's internal /test-parse endpoint; tests call the same gateway function in-process
    (``gpsgw`` is on PYTHONPATH in CI) instead of needing a running gateway."""
    try:
        from gpsgw.protocols.generic.format import parse_sample
    except ImportError:
        return
    monkeypatch.setattr("apps.device_catalog.generic.gateway_parse", lambda d, s: parse_sample(d, s))
