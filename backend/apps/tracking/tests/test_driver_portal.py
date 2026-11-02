"""Driver portal (spec A8): a driver sees only the vehicle assigned to them and its trips."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.analysis.models import Trip
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import Driver, DriverAssignment, Vehicle

pytestmark = pytest.mark.django_db


def trip(org, device, vehicle, hours_ago, km):  # type: ignore[no-untyped-def]
    start = timezone.now() - timedelta(hours=hours_ago)
    return Trip.unscoped.create(organization=org, device=device, vehicle=vehicle, start_time=start,
                                end_time=start + timedelta(minutes=30), start_lat=-6.8, start_lon=39.2, end_lat=-6.7,
                                end_lon=39.3, distance_km=km, distance_source="gps", duration_s=1800, driving_s=1800,
                                idle_s=0, stopped_s=0, max_speed_kmh=70, avg_moving_speed_kmh=40, points=50,
                                detection_mode="ignition")


def test_driver_sees_only_their_vehicle(org_a, org_b, make_member, api):
    model = DeviceModel.objects.get(slug="teltonika-fmc920")
    mine = Vehicle.unscoped.create(organization=org_a, registration_number="T 1 MINE")
    other = Vehicle.unscoped.create(organization=org_a, registration_number="T 2 OTHER")
    dev = Device.unscoped.create(organization=org_a, name="d", identifier="356307042441013", device_model=model)
    DeviceAssignment.unscoped.create(organization=org_a, device=dev, vehicle=mine, assigned_at=timezone.now())
    user = make_member(org_a, "driver")
    me = Driver.unscoped.create(organization=org_a, first_name="Hamisi", last_name="Juma", employee_number="D1",
                                user=user)
    DriverAssignment.unscoped.create(organization=org_a, driver=me, vehicle=mine,
                                     assigned_at=timezone.now() - timedelta(days=1))
    trip(org_a, dev, mine, 3, 12.5)
    trip(org_a, dev, other, 2, 99)
    trip(org_a, dev, mine, 24 * 9, 50)                      # older than 7 days
    client = api(user)
    body = client.get("/api/v1/my-vehicle/").json()
    assert body["vehicle"]["registration_number"] == "T 1 MINE" and body["driver"] == "Hamisi Juma"
    assert [t["distance_km"] for t in body["trips"]] == [12.5]
    assert body["live"]["vehicle"]["id"] == mine.pk
    for forbidden in ("/api/v1/vehicles/", "/api/v1/tracking/live/", "/api/v1/trips/", "/api/v1/reports/"):
        assert client.get(forbidden).status_code == 403, forbidden
    # no assignment -> nothing; other roles don't have the portal
    DriverAssignment.unscoped.update(unassigned_at=timezone.now())
    assert client.get("/api/v1/my-vehicle/").json() == {"vehicle": None, "live": None, "trips": []}
    assert api(make_member(org_a, "viewer")).get("/api/v1/my-vehicle/").status_code == 403


def test_link_driver_to_a_member_only(org_a, org_b, make_member, api):
    admin = api(make_member(org_a, "org_admin"))
    member = make_member(org_a, "driver", "d1@a.test")
    outsider = make_member(org_b, "driver", "d2@b.test")
    d = admin.post("/api/v1/drivers/", {"first_name": "A", "last_name": "B"}, format="json").json()
    assert admin.patch(f"/api/v1/drivers/{d['id']}/", {"user": outsider.pk}, format="json").status_code == 400
    resp = admin.patch(f"/api/v1/drivers/{d['id']}/", {"user": member.pk}, format="json")
    assert resp.status_code == 200 and resp.json()["user"] == member.pk
    assert admin.patch(f"/api/v1/drivers/{d['id']}/", {"user": None}, format="json").json()["user"] is None
