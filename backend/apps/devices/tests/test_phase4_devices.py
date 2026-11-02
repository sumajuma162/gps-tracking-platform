"""Connection-test diagnostics and registering trackers from the unregistered inbox."""
from __future__ import annotations

from unittest import mock

import pytest
from django.db import connection  # noqa: F401  (used by skipif)
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.device_catalog.models import DeviceModel
from apps.devices.diagnostics import similar_unregistered
from apps.devices.models import Device, DeviceConnection, UnregisteredDeviceAttempt
from apps.tracking.models import DeviceState

pytestmark = pytest.mark.django_db
IMEI = "356307042441013"


@pytest.fixture
def device(org_a):
    return Device.unscoped.create(organization=org_a, name="New tracker", identifier=IMEI,
                                  device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))


def attempt(identifier: str, protocol: str = "teltonika") -> UnregisteredDeviceAttempt:
    now = timezone.now()
    return UnregisteredDeviceAttempt.objects.create(identifier=identifier, protocol=protocol, transport="tcp",
                                                    port=5027, last_peer="203.0.113.7:5000", first_seen_at=now,
                                                    last_seen_at=now, attempts=4, sample_hex="000f")


@pytest.mark.skipif("connection.vendor != 'postgresql'", reason="needs PostgreSQL views")
def test_diagnostics_progress_through_the_stages(org_a, make_member, api, device):
    client = api(make_member(org_a, "org_admin"))
    url = f"/api/v1/devices/{device.pk}/diagnostics/"
    body = client.get(url).json()
    assert body["current_stage"] == "connected" and "raw_packets" not in body
    DeviceConnection.unscoped.create(organization=org_a, device=device, transport="tcp", port=5027,
                                     peer="203.0.113.7:5000", connected_at=timezone.now())
    DeviceState.unscoped.create(organization=org_a, device=device, gps_valid=True, latitude=-6.8, longitude=39.2)
    body = client.get(url).json()
    assert body["connection"]["peer"] == "203.0.113.7:5000"
    assert [s["done"] for s in body["stages"]] == [True, True, False, True, False]
    assert body["current_stage"] == "first_packet"


@pytest.mark.skipif("connection.vendor != 'postgresql'", reason="needs PostgreSQL views")
def test_diagnostics_suggest_imei_typos_only(org_a, make_member, api, device):
    attempt("356307042441014")          # one digit off: a typo
    attempt("356307042441103")          # two digits swapped
    attempt("352093086403655")          # someone else's tracker: must not be shown
    found = {r["identifier"] for r in similar_unregistered(IMEI)}
    assert found == {"356307042441014", "356307042441103"}
    body = api(make_member(org_a, "org_admin")).get(f"/api/v1/devices/{device.pk}/diagnostics/").json()
    assert {t["identifier"] for t in body["possible_typos"]} == found


@pytest.mark.skipif("connection.vendor != 'postgresql'", reason="needs PostgreSQL views")
def test_super_admin_sees_raw_packets(org_a, api, superadmin, device):
    from apps.organizations.models import BreakGlassGrant
    BreakGlassGrant.objects.create(user=superadmin, organization=org_a, reason="Support ticket 77",
                                   expires_at=timezone.now() + timezone.timedelta(hours=1))
    body = api(superadmin, org_a).get(f"/api/v1/devices/{device.pk}/diagnostics/").json()
    assert body["raw_packets"] == []


def test_register_from_inbox(org_a, org_b, api, superadmin, django_capture_on_commit_callbacks):
    entry = attempt("490154203237518")
    gt06 = DeviceModel.objects.get(slug="gt06-generic")
    root = api(superadmin)
    url = f"/api/v1/platform/unregistered-devices/{entry.pk}/register/"
    wrong = root.post(url, {"organization": org_b.pk, "device_model": gt06.pk, "name": "X"}, format="json")
    assert wrong.json()["error"]["code"] == "protocol_mismatch"
    with mock.patch("apps.devices.events._publish") as pub, django_capture_on_commit_callbacks(execute=True):
        resp = root.post(url, {"organization": org_b.pk, "device_model": DeviceModel.objects.get(
            slug="teltonika-fmc920").pk, "name": "Found in inbox"}, format="json")
    assert resp.status_code == 201 and resp.json()["organization"] == org_b.name
    device = Device.unscoped.get(identifier="490154203237518")
    assert device.organization_id == org_b.pk
    assert pub.call_args.args[0]["identifier"] == "490154203237518"
    assert AuditLog.objects.filter(action="devices.registered_from_inbox", organization=org_b).exists()
    assert root.get("/api/v1/platform/unregistered-devices/").json()["results"] == []
