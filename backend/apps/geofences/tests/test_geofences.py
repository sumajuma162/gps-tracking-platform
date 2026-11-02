"""Geofence API: validation, PostGIS area, processor notification, rules, events (spec A10)."""
from __future__ import annotations

from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from apps.fleet.models import Vehicle
from apps.geofences.models import Geofence, GeofenceEvent

pytestmark = pytest.mark.django_db
PORT = [[-6.82, 39.28], [-6.82, 39.30], [-6.84, 39.30], [-6.84, 39.28]]


@pytest.fixture
def admin(org_a, make_member, api):
    return api(make_member(org_a, "org_admin"))


def area_m2(pk: int) -> float:
    with connection.cursor() as cur:
        cur.execute("SELECT ST_Area(area) FROM geofences WHERE id = %s", [pk])
        return float(cur.fetchone()[0])


@pytest.mark.skipif("connection.vendor != 'postgresql'")
def test_create_circle_and_polygon_with_postgis_area(admin, django_capture_on_commit_callbacks):
    with mock.patch("redis.Redis.from_url") as client, django_capture_on_commit_callbacks(execute=True):
        circle = admin.post("/api/v1/geofences/", {"name": "Depot", "kind": "circle", "center_lat": -6.80,
                                                   "center_lon": 39.20, "radius_m": 200, "dwell_minutes": 30},
                            format="json")
    assert circle.status_code == 201, circle.json()
    assert area_m2(circle.json()["id"]) == pytest.approx(3.14159 * 200 ** 2, rel=0.02)
    assert client.return_value.publish.call_args.args[0] == "geofences"
    poly = admin.post("/api/v1/geofences/", {"name": "Port", "kind": "polygon", "polygon": PORT}, format="json")
    assert poly.status_code == 201
    assert area_m2(poly.json()["id"]) == pytest.approx(2210 * 2210, rel=0.02)      # ~2.2 km x 2.2 km
    moved = admin.patch(f"/api/v1/geofences/{circle.json()['id']}/", {"radius_m": 400}, format="json")
    assert moved.status_code == 200 and area_m2(circle.json()["id"]) == pytest.approx(3.14159 * 400 ** 2, rel=0.02)


@pytest.mark.parametrize("body,field", [
    ({"kind": "circle", "center_lat": -6.8, "center_lon": 39.2}, "radius_m"),
    ({"kind": "polygon", "polygon": [[-6.8, 39.2], [-6.81, 39.21]]}, "polygon"),
    ({"kind": "polygon", "polygon": [[-6.8, 39.2], [-6.8, 39.2], [-6.8, 39.2]]}, "polygon"),
    ({"kind": "polygon", "polygon": [[-96, 39.2], [-6.8, 39.3], [-6.9, 39.2]]}, "polygon"),
    ({"kind": "circle", "center_lat": -6.8, "center_lon": 39.2, "radius_m": 5}, "radius_m"),
    ({"kind": "circle", "center_lat": -6.8, "center_lon": 39.2, "radius_m": 100, "color": "blue"}, "color"),
])
def test_validation(admin, body, field):
    resp = admin.post("/api/v1/geofences/", {"name": "Bad", **body}, format="json")
    assert resp.status_code == 400 and field in resp.json()["error"]["details"]


def test_names_unique_and_permissions(org_a, make_member, api, admin):
    body = {"name": "Depot", "kind": "circle", "center_lat": -6.8, "center_lon": 39.2, "radius_m": 200}
    with mock.patch("apps.geofences.services.notify_processor"):
        assert admin.post("/api/v1/geofences/", body, format="json").status_code == 201
        dup = admin.post("/api/v1/geofences/", body, format="json")
    assert dup.json()["error"]["details"]["name"]
    viewer = api(make_member(org_a, "viewer"))
    assert viewer.get("/api/v1/geofences/").status_code == 200
    assert viewer.post("/api/v1/geofences/", {**body, "name": "X"}, format="json").status_code == 403


def test_geofence_events_list_and_filters(org_a, make_member, api):
    from apps.device_catalog.models import DeviceModel
    from apps.devices.models import Device
    fence = Geofence.unscoped.create(organization=org_a, name="Depot", kind="circle", center_lat=-6.8, center_lon=39.2,
                                     radius_m=200)
    v = Vehicle.unscoped.create(organization=org_a, registration_number="T 3 GEO")
    d = Device.unscoped.create(organization=org_a, name="G", identifier="356307042441013",
                               device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    now = timezone.now()
    for i, kind in enumerate(["enter", "dwell", "exit"]):
        GeofenceEvent.unscoped.create(organization=org_a, geofence=fence, device=d, vehicle=v, event=kind,
                                      occurred_at=now + timezone.timedelta(minutes=i), lat=-6.8, lon=39.2)
    client = api(make_member(org_a, "monitoring_officer"))
    rows = client.get(f"/api/v1/geofence-events/?vehicle={v.pk}").json()["results"]
    assert [r["event"] for r in rows] == ["exit", "dwell", "enter"] and rows[0]["geofence_name"] == "Depot"
    assert rows[0]["vehicle_registration"] == "T 3 GEO"
    later = (now + timezone.timedelta(seconds=90)).isoformat().replace("+00:00", "Z")
    assert len(client.get(f"/api/v1/geofence-events/?start={later}").json()["results"]) == 1
    assert client.get("/api/v1/geofence-events/?vehicle=abc").status_code == 400
