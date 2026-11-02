"""Global search (tenant- and permission-scoped) and the platform system health page."""
from __future__ import annotations

import pytest

from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device
from apps.fleet.models import Driver, Vehicle

pytestmark = pytest.mark.django_db


def test_search_is_scoped_by_tenant_and_permission(org_a, org_b, make_member, api):
    Vehicle.unscoped.create(organization=org_a, registration_number="T 777 FND", make="Isuzu")
    Vehicle.unscoped.create(organization=org_b, registration_number="T 777 HID")
    Device.unscoped.create(organization=org_a, name="Finder 777", identifier="356307042441013",
                           device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    Driver.unscoped.create(organization=org_a, first_name="Neema", last_name="Seven777")
    admin = api(make_member(org_a, "org_admin"))
    res = admin.get("/api/v1/search/?q=777").json()["results"]
    assert [v["label"] for v in res["vehicles"]] == ["T 777 FND"]                 # never the other tenant's
    assert res["devices"][0]["detail"] == "356307042441013" and res["drivers"][0]["label"].startswith("Neema")
    assert admin.get("/api/v1/search/?q=7").json() == {"results": {}}              # at least 2 characters
    driver = api(make_member(org_a, "driver"))
    assert driver.get("/api/v1/search/?q=777").json()["results"] == {}             # no view permissions


def test_system_health_is_for_platform_admins(org_a, make_member, api, settings, django_user_model):
    settings.GATEWAY_HEALTH_URL = ""
    settings.PROCESSOR_HEALTH_URL = "http://127.0.0.1:9/health"                     # nothing listens there
    assert api(make_member(org_a, "org_admin")).get("/api/v1/system/health/").status_code == 403
    root = django_user_model.objects.create_superuser(email="root@x.test", password="Root-Pass-2026!x")
    body = api(root).get("/api/v1/system/health/").json()
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["gateway"] == {"status": "not_configured"}
    assert body["checks"]["processor"]["status"] == "down" and "processor" in body["down"]
    assert body["status"] == "degraded" and "queues" in body["checks"] and "devices" in body["checks"]
