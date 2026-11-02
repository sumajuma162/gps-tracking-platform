"""Share links (spec A13): time-limited, revocable, audited, showing one vehicle's position or one trip only."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection  # noqa: F401  (used by the skipif condition)
from django.utils import timezone
from rest_framework.test import APIClient

from apps.analysis.models import Trip
from apps.audit.models import AuditLog
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device
from apps.fleet.models import Vehicle
from apps.sharing.models import ShareLink
from apps.tracking.models import DeviceState

pytestmark = [pytest.mark.django_db, pytest.mark.skipif("connection.vendor != 'postgresql'")]


@pytest.fixture
def vehicle(org_a):  # type: ignore[no-untyped-def]
    v = Vehicle.unscoped.create(organization=org_a, registration_number="T 123 SHR")
    d = Device.unscoped.create(organization=org_a, name="Shared", identifier="356307042441013",
                               device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    DeviceState.unscoped.create(organization=org_a, device=d, vehicle=v, latitude=-6.8, longitude=39.28, speed_kmh=42,
                                heading=90, status="ONLINE_MOVING", gps_valid=True, device_time=timezone.now(),
                                last_packet_at=timezone.now())
    return v


def public(token_url: str):  # type: ignore[no-untyped-def]
    token = token_url.rsplit("/", 1)[-1]
    return APIClient().get(f"/api/v1/public/share/{token}/")             # no credentials


def test_live_share_shows_only_the_position_and_can_be_revoked(org_a, make_member, api, vehicle):
    admin = api(make_member(org_a, "org_admin"))
    made = admin.post("/api/v1/share-links/", {"vehicle": vehicle.pk, "hours": 2, "label": "Customer"}, format="json")
    assert made.status_code == 201 and "/share/" in made.json()["url"]
    token = made.json()["url"].rsplit("/", 1)[-1]
    link = ShareLink.unscoped.get()
    assert token not in link.token_hash and len(link.token_hash) == 64            # only the hash is stored
    body = public(made.json()["url"]).json()
    assert set(body) == {"vehicle", "label", "expires_at", "position"}
    assert body["vehicle"] == "T 123 SHR" and body["position"]["lat"] == -6.8 and body["position"]["speed_kmh"] == 42
    assert "356307042441013" not in str(body)                                       # no tracker or other data
    public(made.json()["url"])
    assert ShareLink.unscoped.get().views == 2
    assert AuditLog.objects.filter(action="share_links.created").exists()
    assert admin.post(f"/api/v1/share-links/{link.pk}/revoke/").json()["active"] is False
    assert public(made.json()["url"]).status_code == 404
    assert AuditLog.objects.filter(action="share_links.revoked").exists()
    assert APIClient().get(f"/api/v1/public/share/{token}x/").status_code == 404


def test_expiry_trip_links_and_rules(org_a, org_b, make_member, api, vehicle):
    admin = api(make_member(org_a, "org_admin"))
    made = admin.post("/api/v1/share-links/", {"vehicle": vehicle.pk, "hours": 1}, format="json").json()
    ShareLink.unscoped.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert public(made["url"]).status_code == 404
    t0 = timezone.now() - timedelta(hours=2)
    device = Device.unscoped.get()
    trip = Trip.unscoped.create(organization=org_a, device=device, vehicle=vehicle, start_time=t0,
                                end_time=t0 + timedelta(minutes=20), distance_km=8.2, distance_source="gps",
                                duration_s=1200, driving_s=1100, idle_s=0, stopped_s=100, max_speed_kmh=60,
                                avg_moving_speed_kmh=30, points=3, detection_mode="ignition")
    with connection.cursor() as cur:
        for i in range(3):
            cur.execute("INSERT INTO positions (organization_id, device_id, vehicle_id, device_time, latitude, "
                        "longitude, speed_kmh, gps_valid, is_outlier, protocol, record_hash) "
                        "VALUES (%s, %s, %s, %s, %s, 39.28, 40, true, false, 'teltonika', %s)",
                        [org_a.pk, device.pk, vehicle.pk, t0 + timedelta(minutes=5 * i), -6.8 + i * 0.01, f"s{i}"])
    shared = admin.post("/api/v1/share-links/", {"vehicle": vehicle.pk, "trip": trip.pk}, format="json").json()
    body = public(shared["url"]).json()
    assert "position" not in body and body["trip"]["distance_km"] == 8.2 and len(body["trip"]["points"]) >= 2
    other = Vehicle.unscoped.create(organization=org_a, registration_number="T 2 SHR")
    assert admin.post("/api/v1/share-links/", {"vehicle": other.pk, "trip": trip.pk},
                      format="json").status_code == 400                             # trip of another vehicle
    foreign = Vehicle.unscoped.create(organization=org_b, registration_number="X 1 SHR")
    assert admin.post("/api/v1/share-links/", {"vehicle": foreign.pk}, format="json").status_code == 400
    assert admin.post("/api/v1/share-links/", {"vehicle": vehicle.pk, "hours": 500},
                      format="json").status_code == 400                             # at most 7 days
    viewer = api(make_member(org_a, "viewer"))
    assert viewer.post("/api/v1/share-links/", {"vehicle": vehicle.pk}, format="json").status_code == 403
