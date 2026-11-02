"""Vehicles, groups, drivers and SIM cards (spec A9): validation, assignments, soft delete, permissions."""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.fleet.models import Driver, DriverAssignment, SimCard, Vehicle, VehicleGroup

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(org_a, make_member, api):
    return api(make_member(org_a, "org_admin"))


def test_vehicle_create_normalizes_and_enforces_uniqueness(admin):
    resp = admin.post("/api/v1/vehicles/", {"registration_number": "t 123  abc", "make": "Isuzu",
                                            "vehicle_type": "truck"}, format="json")
    assert resp.status_code == 201 and resp.json()["registration_number"] == "T 123 ABC"
    dup = admin.post("/api/v1/vehicles/", {"registration_number": "T 123 ABC"}, format="json")
    assert dup.json()["error"]["code"] == "vehicle_exists"
    bad_vin = admin.post("/api/v1/vehicles/", {"registration_number": "T 1 X", "vin": "SHORT"}, format="json")
    assert "vin" in bad_vin.json()["error"]["details"]
    assert admin.post("/api/v1/vehicles/", {"registration_number": "T 2 X", "year": 1800},
                      format="json").status_code == 400


def test_registration_reusable_after_soft_delete(admin):
    vid = admin.post("/api/v1/vehicles/", {"registration_number": "T 9 Z"}, format="json").json()["id"]
    assert admin.delete(f"/api/v1/vehicles/{vid}/").status_code == 204
    assert Vehicle.unscoped.get(pk=vid).deleted_at is not None
    assert admin.get(f"/api/v1/vehicles/{vid}/").status_code == 404
    assert admin.post("/api/v1/vehicles/", {"registration_number": "T 9 Z"}, format="json").status_code == 201


def test_groups_are_tenant_scoped(org_a, org_b, admin):
    mine = VehicleGroup.unscoped.create(organization=org_a, name="Upcountry")
    theirs = VehicleGroup.unscoped.create(organization=org_b, name="Theirs")
    ok = admin.post("/api/v1/vehicles/", {"registration_number": "T 5 G", "groups": [mine.pk]}, format="json")
    assert ok.json()["groups"] == [mine.pk]
    bad = admin.post("/api/v1/vehicles/", {"registration_number": "T 6 G", "groups": [theirs.pk]}, format="json")
    assert bad.status_code == 400
    listed = admin.get(f"/api/v1/vehicles/?group={mine.pk}").json()["results"]
    assert [v["registration_number"] for v in listed] == ["T 5 G"]


def test_driver_assignment_rules(org_a, admin):
    v1 = Vehicle.unscoped.create(organization=org_a, registration_number="T 1 D")
    v2 = Vehicle.unscoped.create(organization=org_a, registration_number="T 2 D")
    d1 = Driver.unscoped.create(organization=org_a, first_name="Hamisi", last_name="Juma")
    d2 = Driver.unscoped.create(organization=org_a, first_name="Rehema", last_name="Ali")
    url = lambda v: f"/api/v1/vehicles/{v.pk}/assign-driver/"  # noqa: E731
    assert admin.post(url(v1), {"driver_id": d1.pk}, format="json").json()["current_driver"]["name"] == "Hamisi Juma"
    admin.post(url(v2), {"driver_id": d1.pk}, format="json")          # driver moves: v1 assignment closes
    assert admin.get(f"/api/v1/vehicles/{v1.pk}/").json()["current_driver"] is None
    admin.post(url(v2), {"driver_id": d2.pk}, format="json")          # vehicle gets a new driver
    assert admin.get(f"/api/v1/drivers/{d1.pk}/").json()["current_vehicle"] is None
    assert admin.get(f"/api/v1/drivers/{d2.pk}/").json()["current_vehicle"]["registration_number"] == "T 2 D"
    assert DriverAssignment.unscoped.count() == 3
    assert DriverAssignment.unscoped.filter(unassigned_at__isnull=True).count() == 1
    history = admin.get(f"/api/v1/vehicles/{v2.pk}/history/").json()["drivers"]
    assert [h["name"] for h in history] == ["Rehema Ali", "Hamisi Juma"]
    Driver.unscoped.filter(pk=d1.pk).update(status="inactive")
    assert admin.post(url(v1), {"driver_id": d1.pk}, format="json").json()["error"]["code"] == "driver_inactive"


def test_database_forbids_two_open_assignments(org_a):
    v = Vehicle.unscoped.create(organization=org_a, registration_number="T 7 Q")
    d1 = Driver.unscoped.create(organization=org_a, first_name="A", last_name="B")
    d2 = Driver.unscoped.create(organization=org_a, first_name="C", last_name="D")
    DriverAssignment.unscoped.create(organization=org_a, driver=d1, vehicle=v, assigned_at=timezone.now())
    with pytest.raises(IntegrityError), transaction.atomic():
        DriverAssignment.unscoped.create(organization=org_a, driver=d2, vehicle=v, assigned_at=timezone.now())


def test_deleting_vehicle_closes_assignments(org_a, admin):
    v = Vehicle.unscoped.create(organization=org_a, registration_number="T 8 W")
    d = Driver.unscoped.create(organization=org_a, first_name="A", last_name="B")
    admin.post(f"/api/v1/vehicles/{v.pk}/assign-driver/", {"driver_id": d.pk}, format="json")
    admin.delete(f"/api/v1/vehicles/{v.pk}/")
    assert not DriverAssignment.unscoped.filter(unassigned_at__isnull=True).exists()


def test_driver_fields_and_license_expiry(admin):
    resp = admin.post("/api/v1/drivers/", {"first_name": "Neema", "last_name": "Kimaro", "phone": "+255 712 345 678",
                                           "license_expiry": "2020-01-01", "employee_number": "E-77"}, format="json")
    body = resp.json()
    assert resp.status_code == 201 and body["full_name"] == "Neema Kimaro" and body["license_expired"] is True
    assert admin.post("/api/v1/drivers/", {"first_name": "X", "last_name": "Y", "employee_number": "E-77"},
                      format="json").json()["error"]["code"] == "driver_exists"
    assert admin.post("/api/v1/drivers/", {"first_name": "X", "last_name": "Y", "phone": "call me"},
                      format="json").status_code == 400


def test_sim_password_is_write_only_and_sim_in_use_protected(org_a, admin):
    resp = admin.post("/api/v1/sims/", {"phone_number": "+255712000002", "apn": "internet", "apn_password": "s3cret",
                                        "iccid": "8925500000000000001"}, format="json")
    body = resp.json()
    assert resp.status_code == 201 and "apn_password" not in body and body["has_apn_password"] is True
    from apps.audit.models import AuditLog
    assert "s3cret" not in str(list(AuditLog.objects.values_list("metadata", flat=True)))
    from apps.device_catalog.models import DeviceModel
    from apps.devices.models import Device
    Device.unscoped.create(organization=org_a, name="T", identifier="490154203237518",
                           device_model=DeviceModel.objects.get(slug="teltonika-fmc920"),
                           sim=SimCard.unscoped.get(pk=body["id"]))
    assert admin.delete(f"/api/v1/sims/{body['id']}/").json()["error"]["code"] == "sim_in_use"
    assert admin.post("/api/v1/sims/", {"phone_number": "+255712000003", "iccid": "12"},
                      format="json").status_code == 400


@pytest.mark.parametrize("role,vehicles_manage,drivers_manage,sims_view", [
    ("org_admin", True, True, True), ("fleet_manager", True, True, True),
    ("monitoring_officer", False, False, False), ("viewer", False, False, False),
])
def test_fleet_permissions(org_a, make_member, api, role, vehicles_manage, drivers_manage, sims_view):
    client = api(make_member(org_a, role))
    assert (client.post("/api/v1/vehicles/", {"registration_number": f"T {role}"}, format="json")
            .status_code == 201) is vehicles_manage
    assert (client.post("/api/v1/drivers/", {"first_name": "A", "last_name": role}, format="json")
            .status_code == 201) is drivers_manage
    assert (client.get("/api/v1/sims/").status_code == 200) is sims_view
