"""Device catalog (spec A7): seeds, platform-only writes, model cloning, versioned IO profiles, ports."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.audit.models import AuditLog
from apps.device_catalog.io_schema import ProfileInvalid, validate_mappings
from apps.device_catalog.models import DeviceModel, DeviceProtocol, IoMappingProfile

pytestmark = pytest.mark.django_db
SEED = Path(__file__).resolve().parents[1] / "seed" / "teltonika_fmc920.json"
GATEWAY_PROFILE = Path(__file__).resolve().parents[4] / "gateway/gpsgw/normalization/profiles/teltonika_fmc920.json"


def test_gateway_profile_contract():
    """Backend validation accepts the gateway's profile, and the seed copy matches it exactly."""
    seed = json.loads(SEED.read_text())
    validate_mappings(seed["mappings"])
    if GATEWAY_PROFILE.exists():  # repository checkout (CI); absent inside the backend image
        assert json.loads(GATEWAY_PROFILE.read_text())["mappings"] == seed["mappings"]


def test_seeded_catalog():
    fmc920 = DeviceModel.objects.get(slug="teltonika-fmc920")
    assert fmc920.protocol.name == "teltonika" and fmc920.protocol.implemented
    assert fmc920.io_profile.current_version.version == 1
    assert {m.slug for m in DeviceModel.objects.all()} >= {"teltonika-fmb920", "teltonika-fmc130", "gt06-generic",
                                                           "generic-http-json"}
    assert not DeviceModel.objects.filter(verified=True).exists()  # nothing claims hardware verification
    assert fmc920.command_templates.filter(is_dangerous=True).count() == 0


@pytest.mark.parametrize("mappings,fragment", [
    ([{"io_id": 1, "kind": "field", "target": "warp_speed"}], "not a normalized field"),
    ([{"io_id": 1, "kind": "sensor", "target": "a"}, {"io_id": 1, "kind": "sensor", "target": "b"}], "mapped twice"),
    ([{"io_id": 70000, "kind": "sensor", "target": "a"}], "0-65535"),
    ([{"io_id": 2, "kind": "event", "target": "x"}], "event_map"),
    ([{"io_id": 3, "kind": "sensor", "target": "Bad Name"}], "lowercase"),
    ([{"io_id": 4, "kind": "sensor", "target": "a", "scale": "1e3"}], "scale must be a number"),
    ("nope", "must be a list"),
])
def test_mapping_validation(mappings, fragment):
    with pytest.raises(ProfileInvalid) as e:
        validate_mappings(mappings)
    assert fragment in str(e.value)


def test_members_read_catalog_but_cannot_write(org_a, make_member, api):
    admin = api(make_member(org_a, "org_admin"))
    models = admin.get("/api/v1/device-catalog/models/").json()["results"]
    assert any(m["slug"] == "teltonika-fmc920" for m in models)
    assert admin.post("/api/v1/device-catalog/manufacturers/", {"name": "X", "slug": "x"},
                      format="json").status_code == 403
    assert admin.get("/api/v1/device-catalog/io-profiles/").status_code == 403
    assert admin.get("/api/v1/device-catalog/listener-ports/").status_code == 403


def test_clone_model_without_code(api, superadmin):
    """A7 table row 2: a new model of a supported protocol is added entirely through the API/UI."""
    root = api(superadmin)
    fmc920 = DeviceModel.objects.get(slug="teltonika-fmc920")
    resp = root.post(f"/api/v1/device-catalog/models/{fmc920.pk}/clone/",
                     {"name": "FMB003", "slug": "teltonika-fmb003"}, format="json")
    assert resp.status_code == 201
    body = resp.json()
    assert body["protocol_name"] == "teltonika" and body["io_profile"] not in (None, fmc920.io_profile_id)
    assert body["verified"] is False
    dup = root.post(f"/api/v1/device-catalog/models/{fmc920.pk}/clone/", {"name": "Y", "slug": "teltonika-fmb003"},
                    format="json")
    assert dup.status_code == 400
    assert AuditLog.objects.filter(action="catalog.device_model.cloned").exists()


def test_model_transport_and_profile_consistency(api, superadmin):
    root = api(superadmin)
    fmc920 = DeviceModel.objects.get(slug="teltonika-fmc920")
    bad = root.patch(f"/api/v1/device-catalog/models/{fmc920.pk}/", {"transports": ["http"]}, format="json")
    assert "transports" in bad.json()["error"]["details"]
    gt06 = DeviceModel.objects.get(slug="gt06-generic")
    mismatch = root.patch(f"/api/v1/device-catalog/models/{gt06.pk}/", {"io_profile": fmc920.io_profile_id},
                          format="json")
    assert "io_profile" in mismatch.json()["error"]["details"]
    unknown_cap = root.patch(f"/api/v1/device-catalog/models/{gt06.pk}/", {"capabilities": {"teleport": True}},
                             format="json")
    assert unknown_cap.status_code == 400


def test_io_profile_versioning_and_rollback(api, superadmin):
    root = api(superadmin)
    profile = IoMappingProfile.objects.get(slug="teltonika-fmc920")
    url = f"/api/v1/device-catalog/io-profiles/{profile.pk}/"
    mappings = profile.current_version.mappings
    edited = [dict(m, scale=0.01) if m["io_id"] == 66 else m for m in mappings]
    resp = root.patch(url, {"mappings": edited, "notes": "Voltage reported in 10 mV steps"}, format="json")
    assert resp.status_code == 200 and resp.json()["current"]["version"] == 2
    invalid = root.patch(url, {"mappings": [{"io_id": 1, "kind": "field", "target": "nope"}]}, format="json")
    assert invalid.status_code == 400
    assert root.get(f"{url}versions/").json()[0]["version"] == 2
    back = root.post(f"{url}rollback/", {"version": 1}, format="json")
    assert back.json()["current"]["version"] == 1
    assert root.post(f"{url}rollback/", {"version": 99}, format="json").status_code == 404


def test_listener_ports(api, superadmin):
    root = api(superadmin)
    teltonika = DeviceProtocol.objects.get(name="teltonika")
    ok = root.post("/api/v1/device-catalog/listener-ports/", {"port": 5101, "transport": "tcp",
                                                               "protocol": teltonika.pk}, format="json")
    assert ok.status_code == 201 and ok.json()["reachable"] is True and ok.json()["warning"] is None
    far = root.post("/api/v1/device-catalog/listener-ports/", {"port": 7000, "transport": "tcp",
                                                                "protocol": teltonika.pk}, format="json")
    assert far.json()["reachable"] is False and "not published" in far.json()["warning"]
    wrong = root.post("/api/v1/device-catalog/listener-ports/", {"port": 5102, "transport": "http",
                                                                  "protocol": teltonika.pk}, format="json")
    assert wrong.status_code == 400
    dup = root.post("/api/v1/device-catalog/listener-ports/", {"port": 5027, "transport": "tcp",
                                                                "protocol": teltonika.pk}, format="json")
    assert dup.status_code == 400


def test_protected_catalog_rows_cannot_be_deleted(api, superadmin):
    root = api(superadmin)
    teltonika = DeviceModel.objects.get(slug="teltonika-fmc920").manufacturer
    resp = root.delete(f"/api/v1/device-catalog/manufacturers/{teltonika.pk}/")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "in_use"
