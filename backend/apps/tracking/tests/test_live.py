"""Live snapshot, dashboard summary and map settings (spec A12)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import Driver, DriverAssignment, Vehicle, VehicleGroup
from apps.tracking.models import DeviceState

pytestmark = pytest.mark.django_db


@pytest.fixture
def fleet(org_a):
    model = DeviceModel.objects.get(slug="teltonika-fmc920")
    now = timezone.now()
    group = VehicleGroup.unscoped.create(organization=org_a, name="Upcountry")
    out = {}
    for i, (status, gsm, bat, seen) in enumerate([("ONLINE_MOVING", 4, 4.0, now), ("ONLINE_IDLE", 1, 3.4, now),
                                                  ("OFFLINE", 3, 4.0, now - timedelta(days=2))]):
        v = Vehicle.unscoped.create(organization=org_a, registration_number=f"T {i}00 LIV")
        v.groups.add(group)
        d = Device.unscoped.create(organization=org_a, name=f"Tracker {i}", identifier=f"35630704244101{i}",
                                   device_model=model)
        DeviceAssignment.unscoped.create(organization=org_a, device=d, vehicle=v, assigned_at=now)
        DeviceState.unscoped.create(organization=org_a, device=d, vehicle=v, status=status, latitude=-6.8 - i / 100,
                                    longitude=39.28, speed_kmh=40 if i == 0 else 0, gps_valid=True, gsm_signal=gsm,
                                    battery_voltage=bat, last_packet_at=seen, device_time=seen)
        out[i] = (v, d)
    Device.unscoped.create(organization=org_a, name="Spare", identifier="490154203237518", device_model=model)
    Vehicle.unscoped.create(organization=org_a, registration_number="T 999 NOTRK")
    driver = Driver.unscoped.create(organization=org_a, first_name="Hamisi", last_name="Juma", phone="+255712000001")
    DriverAssignment.unscoped.create(organization=org_a, driver=driver, vehicle=out[0][0], assigned_at=now)
    return out, group


def test_live_snapshot(org_a, make_member, api, fleet):
    (rows_by_i, group) = fleet
    rows = {r["device_name"]: r for r in api(make_member(org_a, "monitoring_officer")).get(
        "/api/v1/tracking/live/").json()}
    moving = rows["Tracker 0"]
    assert moving["status"] == "ONLINE_MOVING" and moving["vehicle"]["registration_number"] == "T 000 LIV"
    assert moving["driver"]["name"] == "Hamisi Juma" and moving["vehicle"]["groups"] == [group.pk]
    assert moving["latitude"] == pytest.approx(-6.8) and moving["speed_kmh"] == 40
    assert rows["Spare"]["vehicle"] is None and rows["Spare"]["status"] == "UNKNOWN"


def test_live_requires_permission(org_a, make_member, api, fleet):
    assert api(make_member(org_a, "auditor")).get("/api/v1/tracking/live/").status_code == 403


def test_org_admin_manual_positions_are_scoped_to_their_organization(
        org_a, org_b, make_member, api, fleet, monkeypatch, settings):
    import json
    from io import BytesIO

    model = DeviceModel.objects.get(slug="teltonika-fmc920")
    vehicle = Vehicle.unscoped.create(organization=org_b, registration_number="T 900 OTHER")
    other_device = Device.unscoped.create(organization=org_b, name="Other tracker", identifier="356307042441099",
                                          device_model=model)
    DeviceAssignment.unscoped.create(organization=org_b, device=other_device, vehicle=vehicle,
                                     assigned_at=timezone.now())
    devices, _ = fleet
    own_device = devices[0][1]
    settings.GATEWAY_MANUAL_INGEST_URL = "http://gateway.test/manual-position"

    class GatewayResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        return GatewayResponse(json.dumps({"accepted_records": 1, "latitude": -6.81, "longitude": 39.27,
                                           "device_time": "2026-10-28T10:00:00+00:00"}).encode())

    monkeypatch.setattr("apps.tracking.manual.urllib.request.urlopen", fake_urlopen)
    admin = api(make_member(org_a, "org_admin"), org_a)
    options = admin.get("/api/v1/tracking/manual-positions/")
    assert options.status_code == 200
    assert [org["id"] for org in options.json()["organizations"]] == [org_a.pk]
    assert {device["id"] for device in options.json()["devices"]} == {device.pk for _, device in devices.values()}

    own_response = admin.post("/api/v1/tracking/manual-positions/", {
        "device_id": own_device.pk, "latitude": -6.81, "longitude": 39.27,
        "speed_kmh": 35, "heading": 90, "ignition": True,
    }, format="json")
    other_response = admin.post("/api/v1/tracking/manual-positions/", {
        "device_id": other_device.pk, "latitude": -6.81, "longitude": 39.27,
    }, format="json")
    denied = api(make_member(org_a, "auditor"), org_a).get("/api/v1/tracking/manual-positions/")

    assert own_response.status_code == 200 and own_response.json()["device_id"] == own_device.pk
    assert other_response.status_code == 404
    assert denied.status_code == 403


def test_platform_manual_position_uses_gateway_and_returns_fix(org_a, api, superadmin, fleet, monkeypatch, settings):
    import json
    from io import BytesIO

    device = fleet[0][0][1]
    settings.GATEWAY_MANUAL_INGEST_URL = "http://gateway.test/manual-position"
    captured = {}

    class GatewayResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return GatewayResponse(json.dumps({"accepted_records": 1, "latitude": -6.81, "longitude": 39.27,
                                           "device_time": "2026-10-28T10:00:00+00:00"}).encode())

    monkeypatch.setattr("apps.tracking.manual.urllib.request.urlopen", fake_urlopen)
    response = api(superadmin).post("/api/v1/platform/manual-positions/", {
        "device_id": device.pk, "latitude": -6.81, "longitude": 39.27, "speed_kmh": 35, "heading": 90,
        "ignition": True,
    }, format="json")

    assert response.status_code == 200
    assert response.json()["latitude"] == -6.81 and response.json()["longitude"] == 39.27
    assert response.json()["accepted_records"] == 1
    assert captured["payload"]["identifier"] == device.identifier
    assert captured["authorization"].startswith("Bearer ") and captured["timeout"] == 5


def test_dashboard_summary(org_a, make_member, api, fleet):
    body = api(make_member(org_a, "fleet_manager")).get("/api/v1/dashboard/summary/").json()
    assert body["vehicles"] == 4 and body["vehicles_without_tracker"] == 1
    assert body["by_status"]["ONLINE_MOVING"] == 1 and body["by_status"]["ONLINE_IDLE"] == 1
    assert body["by_status"]["OFFLINE"] == 1
    assert body["devices"] == 4 and body["online_devices"] == 2
    health = body["health"]
    assert [h["name"] for h in health["never_connected"]] == ["Spare"]
    assert [h["name"] for h in health["weak_signal"]] == ["T 100 LIV"]
    assert [h["name"] for h in health["low_tracker_battery"]] == ["T 100 LIV"]
    assert [h["name"] for h in health["offline_over_24h"]] == ["T 200 LIV"]
    assert body["trips_today"] == 0 and body["distance_today_km"] == 0


def test_dashboard_counts_todays_trips_in_the_organization_time_zone(org_a, make_member, api, fleet):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from django.utils import timezone

    from apps.analysis.models import Trip
    from apps.devices.models import Device
    dev = Device.unscoped.filter(organization=org_a).exclude(name="Spare").first()
    midnight = timezone.now().astimezone(ZoneInfo(org_a.timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    for start, km in ((midnight + timedelta(minutes=5), 10.5), (midnight - timedelta(minutes=5), 99)):
        Trip.unscoped.create(organization=org_a, device=dev, vehicle_id=None, start_time=start,
                             end_time=start + timedelta(minutes=1), start_lat=0, start_lon=0, end_lat=0, end_lon=0,
                             distance_km=km, distance_source="gps", duration_s=60, driving_s=60, idle_s=0,
                             stopped_s=0, max_speed_kmh=50, avg_moving_speed_kmh=40, points=5,
                             detection_mode="ignition")
    body = api(make_member(org_a, "fleet_manager")).get("/api/v1/dashboard/summary/").json()
    assert (body["trips_today"], body["distance_today_km"]) == (1, 10.5)      # yesterday's trip excluded


def test_map_settings(org_a, make_member, api, settings):
    settings.MAP_TILE_URL = "https://tiles.example.com/{z}/{x}/{y}.png"
    body = api(make_member(org_a, "viewer")).get("/api/v1/settings/map/").json()
    assert body["tile_url"].startswith("https://tiles.example.com") and body["center"] == [-6.7924, 39.2083]
