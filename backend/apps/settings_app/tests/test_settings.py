"""Organization and platform settings (spec A13 "Settings", A10 thresholds configurable per organization)."""
from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.settings_app.models import OrganizationSettings, PlatformSettings, effective

pytestmark = pytest.mark.django_db


def test_effective_values_overrides_and_audit(org_a, org_b, make_member, api):
    admin = api(make_member(org_a, "org_admin"))
    body = admin.get("/api/v1/settings/organization/").json()
    assert body["values"]["offline_minutes"] == 5 and body["overrides"] == {}
    assert {s["key"] for s in body["schema"]} >= {"overspeed_warning_kmh", "retention_days", "map_zoom"}
    resp = admin.patch("/api/v1/settings/organization/", {
        "values": {"overspeed_warning_kmh": 70, "overspeed_critical_kmh": 90, "map_zoom": 11},
        "timezone": "Africa/Nairobi", "distance_unit": "mi"}, format="json")
    assert resp.status_code == 200, resp.json()
    saved = resp.json()
    assert saved["values"]["overspeed_warning_kmh"] == 70.0 and saved["organization"]["distance_unit"] == "mi"
    org_a.refresh_from_db()
    assert org_a.timezone == "Africa/Nairobi"
    assert effective(org_b.pk)["overspeed_warning_kmh"] == 80.0             # another tenant is unaffected
    entry = AuditLog.objects.get(action="settings.updated")
    assert entry.metadata["changes"]["overspeed_warning_kmh"] == [None, 70.0]
    assert entry.metadata["changes"]["timezone"] == ["Africa/Dar_es_Salaam", "Africa/Nairobi"]
    # null removes an override (back to the platform default)
    admin.patch("/api/v1/settings/organization/", {"values": {"map_zoom": None}}, format="json")
    assert OrganizationSettings.unscoped.get(organization=org_a).values == {
        "overspeed_warning_kmh": 70.0, "overspeed_critical_kmh": 90.0}
    assert api(make_member(org_a, "fleet_manager")).get("/api/v1/settings/organization/").status_code == 403
    assert api(make_member(org_a, "viewer")).patch("/api/v1/settings/organization/", {}).status_code == 403


@pytest.mark.parametrize("payload,field", [
    ({"values": {"offline_minutes": 0}}, "offline_minutes"),
    ({"values": {"map_zoom": 4.5}}, "map_zoom"),
    ({"values": {"nope": 1}}, "nope"),
    ({"values": {"email_min_severity": "loud"}}, "email_min_severity"),
    ({"values": {"overspeed_warning_kmh": 120}}, "overspeed_critical_kmh"),      # above critical (100)
    ({"timezone": "Mars/Base"}, "timezone"),
])
def test_validation(org_a, make_member, api, payload, field):
    admin = api(make_member(org_a, "org_admin"))
    resp = admin.patch("/api/v1/settings/organization/", payload, format="json")
    assert resp.status_code == 400 and field in resp.json()["error"]["details"]
    assert not AuditLog.objects.filter(action="settings.updated").exists()


def test_platform_defaults_super_admin_only_and_inherited(org_a, make_member, api, superadmin):
    admin = api(make_member(org_a, "org_admin"))
    assert admin.get("/api/v1/settings/platform/").status_code == 403
    root = api(superadmin)
    resp = root.patch("/api/v1/settings/platform/", {"values": {"offline_minutes": 15, "no_gps_minutes": 20},
                                                     "map_tile_url": "https://tiles.example.com/{z}/{x}/{y}.png"},
                      format="json")
    assert resp.status_code == 200, resp.json()
    assert effective(org_a.pk)["offline_minutes"] == 15                       # inherited
    assert PlatformSettings.load().map_tile_url.startswith("https://tiles.example.com")
    assert root.patch("/api/v1/settings/platform/", {"map_tile_url": "http://insecure/{z}"},
                      format="json").status_code == 400
    assert AuditLog.objects.filter(action="platform_settings.updated").exists()
    admin.patch("/api/v1/settings/organization/", {"values": {"map_center_lat": -5.07, "map_center_lon": 39.1}},
                format="json")
    m = admin.get("/api/v1/settings/map/").json()
    assert m["center"] == [-5.07, 39.1] and m["tile_url"] == "https://tiles.example.com/{z}/{x}/{y}.png"


@pytest.mark.skipif("__import__('django.db').db.connection.vendor != 'postgresql'")
def test_retention_deletes_only_that_organizations_old_positions(org_a, org_b):
    from datetime import timedelta

    from django.db import connection
    from django.utils import timezone

    from apps.device_catalog.models import DeviceModel
    from apps.devices.models import Device
    from apps.settings_app.tasks import apply_retention

    model = DeviceModel.objects.get(slug="teltonika-fmc920")
    now = timezone.now()
    for org, imei in ((org_a, "356307042441013"), (org_b, "490154203237518")):
        d = Device.unscoped.create(organization=org, name="r", identifier=imei, device_model=model)
        with connection.cursor() as cur:
            cur.executemany("INSERT INTO positions (organization_id, device_id, device_time, latitude, longitude, "
                            "gps_valid, protocol, record_hash) VALUES (%s,%s,%s,-6.8,39.2,true,'teltonika',%s)",
                            [(org.pk, d.pk, now - timedelta(days=days), f"r{days}") for days in (1, 40, 100)])
    OrganizationSettings.unscoped.create(organization=org_a, values={"retention_days": 30})
    assert apply_retention() == {org_a.slug: 2}

    def count(org):  # type: ignore[no-untyped-def]
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM positions WHERE organization_id = %s", [org.pk])
            return cur.fetchone()[0]
    assert (count(org_a), count(org_b)) == (1, 3)          # org B keeps the default 730 days
