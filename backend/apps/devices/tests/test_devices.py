"""Tracker registration, uniqueness, assignment history, enable/disable, setup instructions, CSV import."""
from __future__ import annotations

from unittest import mock

import pytest

from apps.audit.models import AuditLog
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import SimCard, Vehicle

pytestmark = pytest.mark.django_db
IMEI_1, IMEI_2, IMEI_3, IMEI_4 = "490154203237518", "356307042441013", "352093086403655", "353201234567896"
URL = "/api/v1/devices/"


@pytest.fixture
def fmc920() -> DeviceModel:
    return DeviceModel.objects.get(slug="teltonika-fmc920")


@pytest.fixture
def admin(org_a, make_member, api):
    return api(make_member(org_a, "org_admin"))


@pytest.fixture
def published():
    with mock.patch("apps.devices.events._publish") as pub:
        yield pub


def create(client, model, identifier, name="Truck tracker", **extra):
    return client.post(URL, {"name": name, "identifier": identifier, "device_model": model.pk, **extra}, format="json")


def test_register_device_publishes_registry_event(admin, fmc920, published, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        resp = create(admin, fmc920, "4901 5420 3237 518")  # spaces from the label are removed
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["identifier"] == IMEI_1 and body["protocol"] == "teltonika" and body["is_active"]
    published.assert_called_once()
    assert published.call_args.args[0] == {"action": "created", "device_id": body["id"], "identifier": IMEI_1,
                                           "organization_id": Device.unscoped.get().organization_id, "active": True}
    assert AuditLog.objects.filter(action="devices.created").exists()


@pytest.mark.parametrize("identifier,fragment", [
    ("490154203237519", "check digit"), ("49015420323751", "exactly 15 digits"), ("ABCDEFGHIJKLMNO", "15 digits"),
])
def test_imei_validation(admin, fmc920, published, identifier, fragment):
    resp = create(admin, fmc920, identifier)
    assert resp.status_code == 400 and fragment in resp.json()["error"]["details"]["identifier"][0]


def test_imei_is_unique_across_organizations_without_revealing_owner(org_a, org_b, make_member, api, fmc920,
                                                                     published):
    assert create(api(make_member(org_b, "org_admin")), fmc920, IMEI_1).status_code == 201
    resp = create(api(make_member(org_a, "org_admin")), fmc920, IMEI_1)
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "identifier_taken"
    assert resp.json()["error"]["details"]["identifier"]  # placed under the IMEI field in forms
    assert org_b.name not in str(resp.json())


def test_identifier_is_immutable_and_deprecated_models_rejected(admin, fmc920, published):
    device_id = create(admin, fmc920, IMEI_1).json()["id"]
    change = admin.patch(f"{URL}{device_id}/", {"identifier": IMEI_2}, format="json")
    assert "can't be changed" in change.json()["error"]["details"]["identifier"][0]
    DeviceModel.objects.filter(slug="teltonika-fmb920").update(status="deprecated")
    old = DeviceModel.objects.get(slug="teltonika-fmb920")
    assert create(admin, old, IMEI_2).status_code == 400


def test_sim_must_belong_to_the_organization(org_b, admin, fmc920, published):
    foreign = SimCard.unscoped.create(organization=org_b, phone_number="+255700000009")
    resp = create(admin, fmc920, IMEI_1, sim=foreign.pk)
    assert resp.status_code == 400 and "sim" in resp.json()["error"]["details"]


def test_assignment_replaces_tracker_and_keeps_history(org_a, admin, fmc920, published):
    vehicle = Vehicle.unscoped.create(organization=org_a, registration_number="T 123 ABC")
    first = create(admin, fmc920, IMEI_1, name="Old tracker").json()["id"]
    second = create(admin, fmc920, IMEI_2, name="New tracker").json()["id"]
    r1 = admin.post(f"{URL}{first}/assign/", {"vehicle_id": vehicle.pk}, format="json")
    assert r1.json()["current_vehicle"]["registration_number"] == "T 123 ABC"
    assert r1.json()["inventory_status"] == "installed"
    admin.post(f"{URL}{second}/assign/", {"vehicle_id": vehicle.pk}, format="json")
    assert admin.get(f"{URL}{first}/").json()["current_vehicle"] is None
    history = admin.get(f"/api/v1/vehicles/{vehicle.pk}/history/").json()["devices"]
    assert [h["name"] for h in history] == ["New tracker", "Old tracker"]
    assert history[1]["unassigned_at"] is not None and history[0]["unassigned_at"] is None
    assert admin.post(f"{URL}{second}/unassign/").json()["current_vehicle"] is None
    assert DeviceAssignment.unscoped.filter(unassigned_at__isnull=True).count() == 0


def test_disable_blocks_assignment_and_notifies_gateway(org_a, admin, fmc920, published,
                                                        django_capture_on_commit_callbacks):
    vehicle = Vehicle.unscoped.create(organization=org_a, registration_number="T 1 A")
    device_id = create(admin, fmc920, IMEI_1).json()["id"]
    with django_capture_on_commit_callbacks(execute=True):
        assert admin.post(f"{URL}{device_id}/disable/").json()["is_active"] is False
    assert published.call_args.args[0]["action"] == "disabled" and published.call_args.args[0]["active"] is False
    resp = admin.post(f"{URL}{device_id}/assign/", {"vehicle_id": vehicle.pk}, format="json")
    assert resp.json()["error"]["code"] == "device_disabled"
    assert admin.post(f"{URL}{device_id}/enable/").json()["is_active"] is True


def test_delete_retires_and_frees_identifier(org_a, admin, fmc920, published):
    vehicle = Vehicle.unscoped.create(organization=org_a, registration_number="T 2 B")
    device_id = create(admin, fmc920, IMEI_1).json()["id"]
    admin.post(f"{URL}{device_id}/assign/", {"vehicle_id": vehicle.pk}, format="json")
    assert admin.delete(f"{URL}{device_id}/").status_code == 204
    retired = Device.unscoped.get(pk=device_id)
    assert retired.deleted_at and not retired.is_active and retired.inventory_status == "retired"
    assert not DeviceAssignment.unscoped.filter(unassigned_at__isnull=True).exists()
    assert create(admin, fmc920, IMEI_1, name="Replacement").status_code == 201  # same IMEI, re-registered


def test_setup_instructions_fill_in_server_port_and_apn(org_a, admin, fmc920, published, settings):
    settings.TRACKER_SERVER_HOST = "gps.demo-logistics.co.tz"
    sim = SimCard.unscoped.create(organization=org_a, phone_number="+255712345678", apn="internet")
    device_id = create(admin, fmc920, IMEI_1, sim=sim.pk).json()["id"]
    body = admin.get(f"{URL}{device_id}/setup-instructions/").json()
    assert body["port"] == 5027 and body["apn"] == "internet"
    assert "`gps.demo-logistics.co.tz`" in body["markdown"] and "`5027`" in body["markdown"]
    assert "{" not in body["markdown"]


def test_filters(org_a, admin, fmc920, published):
    vehicle = Vehicle.unscoped.create(organization=org_a, registration_number="T 3 C")
    a = create(admin, fmc920, IMEI_1, name="Assigned").json()["id"]
    create(admin, fmc920, IMEI_2, name="Spare")
    admin.post(f"{URL}{a}/assign/", {"vehicle_id": vehicle.pk}, format="json")
    names = lambda q: sorted(d["name"] for d in admin.get(URL + q).json()["results"])  # noqa: E731
    assert names("?assigned=1") == ["Assigned"] and names("?assigned=0") == ["Spare"]
    assert names(f"?search={IMEI_2[:6]}") == ["Spare"]


def test_bulk_import_preview_then_commit_valid_rows(org_a, admin, fmc920, published):
    Vehicle.unscoped.create(organization=org_a, registration_number="T 123 ABC")
    SimCard.unscoped.create(organization=org_a, phone_number="+255712000001")
    csv_text = "\n".join([
        "model,identifier,name,serial_number,sim_phone,vehicle_registration",
        f"teltonika-fmc920,{IMEI_1},Truck 1,SN1,+255712000001,t 123 abc",
        "teltonika-fmc920,490154203237519,Bad check digit,,,",
        f"teltonika-fmc920,{IMEI_2},Truck 2,,,",
        f"teltonika-fmc920,{IMEI_1},Duplicate in file,,,",
        f"no-such-model,{IMEI_3},Unknown model,,,",
        f"teltonika-fmc920,{IMEI_4},No vehicle,,,T 404 NOPE",
    ])
    preview = admin.post(f"{URL}import/", {"csv": csv_text}, format="json").json()
    assert (preview["valid"], preview["invalid"], preview["created"]) == (2, 4, 0)
    errors = {r["line"]: r["errors"] for r in preview["rows"]}
    assert "check digit" in errors[3][0] and "twice" in errors[5][0] and "Unknown" in errors[6][0]
    assert "No vehicle" in errors[7][0]
    assert Device.unscoped.count() == 0  # preview writes nothing
    done = admin.post(f"{URL}import/", {"csv": csv_text, "commit": True}, format="json")
    assert done.status_code == 201 and done.json()["created"] == 2
    truck1 = Device.unscoped.get(identifier=IMEI_1)
    assert truck1.sim.phone_number == "+255712000001"
    assert DeviceAssignment.unscoped.get(device=truck1).vehicle.registration_number == "T 123 ABC"
    log = AuditLog.objects.get(action="devices.imported")
    assert log.metadata == {"created": 2, "skipped": 4}


def test_bulk_import_rejects_bad_files(admin, published):
    bad_header = admin.post(f"{URL}import/", {"csv": "imei,label\n1,2"}, format="json")
    assert bad_header.json()["error"]["code"] == "import_header"
    empty = admin.post(f"{URL}import/", {"csv": "model,identifier,name\n"}, format="json")
    assert empty.json()["error"]["code"] == "import_empty"


@pytest.mark.parametrize("role,can_view,can_manage", [
    ("org_admin", True, True), ("fleet_manager", True, False), ("monitoring_officer", True, False),
    ("viewer", True, False), ("auditor", False, False), ("driver", False, False),
])
def test_device_permissions(org_a, make_member, api, fmc920, published, role, can_view, can_manage):
    client = api(make_member(org_a, role))
    assert (client.get(URL).status_code == 200) is can_view
    assert (create(client, fmc920, IMEI_1).status_code == 201) is can_manage


def test_unregistered_inbox_hides_registered_and_dismisses(org_a, api, superadmin, fmc920, published):
    from django.utils import timezone

    from apps.devices.models import UnregisteredDeviceAttempt
    now = timezone.now()
    for imei in (IMEI_1, IMEI_2):
        UnregisteredDeviceAttempt.objects.create(identifier=imei, protocol="teltonika", transport="tcp", port=5027,
                                                 last_peer="203.0.113.5:4100", first_seen_at=now, last_seen_at=now,
                                                 attempts=3, sample_hex="000f")
    Device.unscoped.create(organization=org_a, name="Now registered", identifier=IMEI_2, device_model=fmc920)
    root = api(superadmin)
    rows = root.get("/api/v1/platform/unregistered-devices/").json()["results"]
    assert [r["identifier"] for r in rows] == [IMEI_1]                     # registered ones drop out
    assert root.delete(f"/api/v1/platform/unregistered-devices/{rows[0]['id']}/").status_code == 204
    assert root.get("/api/v1/platform/unregistered-devices/").json()["results"] == []
    assert AuditLog.objects.filter(action="platform.unregistered_dismissed").exists()
