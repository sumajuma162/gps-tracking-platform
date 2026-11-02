"""Generic formats, text listener ports, HTTP tokens and the GT06 seed (spec A7, Phase 10)."""
from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.device_catalog.models import DeviceModel, DeviceProtocol, GenericFormat, ListenerPort
from apps.devices.models import Device

pytestmark = pytest.mark.django_db
pytest.importorskip("gpsgw.protocols.generic.format", reason="the gateway parses formats")


def test_seed_gt06_profile_formats_and_ports():
    gt06 = DeviceModel.objects.get(slug="gt06-generic")
    assert gt06.io_profile is not None and gt06.io_profile.current_version.mappings[0]["target"] == "ignition"
    assert DeviceModel.objects.get(slug="generic-http-json").generic_format.slug == "fleet-json"
    assert set(GenericFormat.objects.values_list("slug", flat=True)) >= {"fleet-json", "csv-basic"}
    assert ListenerPort.objects.filter(port__in=(5023, 5055), is_enabled=True).count() == 2


def test_formats_are_platform_only_validated_and_audited(org_a, make_member, api, superadmin):
    root = api(superadmin)
    assert api(make_member(org_a, "org_admin")).get("/api/v1/device-catalog/generic-formats/").status_code == 403
    definition = {"identifier": 0, "latitude": 1, "longitude": 2, "separator": ";"}
    resp = root.post("/api/v1/device-catalog/generic-formats/", {"name": "Semi", "slug": "semi", "kind": "delimited",
                                                                  "definition": definition}, format="json")
    assert resp.status_code == 201 and resp.json()["definition"]["kind"] == "delimited"
    assert AuditLog.objects.filter(action="catalog.generic_format.created").exists()
    wrong_kind = root.patch(f"/api/v1/device-catalog/generic-formats/{resp.json()['id']}/", {"kind": "json"},
                            format="json")
    assert wrong_kind.status_code == 400                   # column numbers don't fit a JSON format
    draft = root.post(f"/api/v1/device-catalog/generic-formats/{resp.json()['id']}/test-parse/",
                      {"sample": "X1;-6.8;39.2\n", "definition": {**definition, "separator": ","}}, format="json")
    assert draft.json()["ok"] is False                     # the draft separator doesn't match the sample
    assert root.post(f"/api/v1/device-catalog/generic-formats/{resp.json()['id']}/test-parse/",
                     {"sample": "X1;-6.8;39.2\n"}, format="json").json()["records"][0]["latitude"] == -6.8


def test_text_ports_need_a_delimited_format(superadmin, api):
    root = api(superadmin)
    proto = DeviceProtocol.objects.get(name="generic_text")
    base = {"port": 5104, "transport": "tcp", "protocol": proto.pk}
    assert root.post("/api/v1/device-catalog/listener-ports/", base, format="json").status_code == 400
    json_fmt = GenericFormat.objects.get(slug="fleet-json").pk
    assert root.post("/api/v1/device-catalog/listener-ports/", {**base, "generic_format": json_fmt},
                     format="json").status_code == 400
    ok = root.post("/api/v1/device-catalog/listener-ports/",
                   {**base, "generic_format": GenericFormat.objects.get(slug="csv-basic").pk}, format="json")
    assert ok.status_code == 201 and ok.json()["warning"] is None          # inside the reserved range


def test_http_token_rotation(org_a, make_member, api):
    admin = api(make_member(org_a, "org_admin"))
    http_dev = Device.unscoped.create(organization=org_a, name="H", identifier="HTTP-1",
                                      device_model=DeviceModel.objects.get(slug="generic-http-json"))
    gt06 = Device.unscoped.create(organization=org_a, name="G", identifier="868120145233604",
                                  device_model=DeviceModel.objects.get(slug="gt06-generic"))
    first = admin.post(f"/api/v1/devices/{http_dev.pk}/rotate-token/").json()["token"]
    second = admin.post(f"/api/v1/devices/{http_dev.pk}/rotate-token/").json()["token"]
    import hashlib
    http_dev.refresh_from_db()
    assert first != second and http_dev.ingest_token_hash == hashlib.sha256(second.encode()).hexdigest()
    assert admin.post(f"/api/v1/devices/{gt06.pk}/rotate-token/").status_code == 400
    assert api(make_member(org_a, "viewer")).post(f"/api/v1/devices/{http_dev.pk}/rotate-token/").status_code == 403
    setup = admin.get(f"/api/v1/devices/{http_dev.pk}/setup-instructions/").json()
    assert '"device_id": "HTTP-1"' in setup["markdown"] and "/fleet-json" in setup["markdown"]
    assert AuditLog.objects.filter(action="devices.token_rotated").exists()
