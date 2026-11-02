"""Maintenance schedules, service records, due alerts and SIM-expiry alerts (spec A11, A13)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.models import Alert, AlertRule, Notification
from apps.analysis.models import Trip
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import SimCard, Vehicle
from apps.maintenance.models import MaintenanceSchedule
from apps.maintenance.tasks import check_due

pytestmark = pytest.mark.django_db
NOW = timezone.now().replace(microsecond=0)


@pytest.fixture
def fleet(org_a, make_member):  # type: ignore[no-untyped-def]
    admin = make_member(org_a, "org_admin")
    v = Vehicle.unscoped.create(organization=org_a, registration_number="T 321 MNT")
    dev = Device.unscoped.create(organization=org_a, name="M", identifier="356307042441013",
                                 device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    DeviceAssignment.unscoped.create(organization=org_a, device=dev, vehicle=v, assigned_at=NOW)
    for i in range(4):     # 4 x 120 km, 2 h driving + 0.5 h idle each, over the last days
        start = NOW - timedelta(days=4 - i)
        Trip.unscoped.create(organization=org_a, device=dev, vehicle=v, start_time=start,
                             end_time=start + timedelta(hours=3), start_lat=-6.8, start_lon=39.2, end_lat=-6.7,
                             end_lon=39.3, distance_km=120, distance_source="odometer", duration_s=10800,
                             driving_s=7200, idle_s=1800, stopped_s=1800, max_speed_kmh=90,
                             avg_moving_speed_kmh=60, points=500, detection_mode="ignition")
    return admin, v, dev


def test_due_status_and_alert_lifecycle(org_a, api, fleet):
    admin, v, _ = fleet
    client = api(admin)
    AlertRule.unscoped.create(organization=org_a, name="Service due", type="maintenance_due", severity="info",
                              recipient_roles=["org_admin"], cooldown_minutes=0)
    resp = client.post("/api/v1/maintenance/schedules/", {
        "vehicle": v.pk, "name": "Oil change", "interval_km": 1000, "due_soon_km": 600,
        "last_service_at": (NOW - timedelta(days=5)).isoformat()}, format="json")
    assert resp.status_code == 201, resp.json()
    status = resp.json()["status"]
    assert (status["state"], status["km_since"], status["km_left"]) == ("due_soon", 480.0, 520.0)
    assert status["hours_since"] == 10.0                               # ignition-on hours from trips
    assert client.post("/api/v1/maintenance/schedules/", {"vehicle": v.pk, "name": "x"},
                       format="json").status_code == 400                # needs an interval

    assert check_due()["maintenance"] == 1
    alert = Alert.unscoped.get(type="maintenance_due")
    assert alert.message == "T 321 MNT: Oil change due soon" and alert.severity == "info"
    assert Notification.unscoped.filter(alert=alert, channel="in_app", status="sent").count() == 1
    assert check_due()["maintenance"] == 0                             # same state: no repeat
    MaintenanceSchedule.unscoped.update(interval_km=400)                # now overdue: escalates
    assert check_due()["maintenance"] == 1
    alert.refresh_from_db()
    assert alert.severity == "warning" and alert.occurrences == 2 and alert.message_params["state"] == "overdue"

    sched = MaintenanceSchedule.unscoped.get()
    rec = client.post("/api/v1/maintenance/records/", {
        "vehicle": v.pk, "schedule": sched.pk, "title": "Oil and filter", "performed_at": NOW.isoformat(),
        "cost": "185000.00", "vendor": "Kariakoo Motors"}, format="json")
    assert rec.status_code == 201, rec.json()
    sched.refresh_from_db()
    alert.refresh_from_db()
    assert sched.last_service_at == NOW and sched.alert_state == "ok" and alert.status == "resolved"
    assert client.get("/api/v1/maintenance/schedules/").json()["results"][0]["status"]["km_since"] == 0
    future = client.post("/api/v1/maintenance/records/", {"vehicle": v.pk, "title": "x",
                                                           "performed_at": (NOW + timedelta(days=2)).isoformat()},
                         format="json")
    assert future.status_code == 400


def test_days_interval_and_permissions(org_a, make_member, api, fleet):
    _, v, _ = fleet
    viewer = api(make_member(org_a, "viewer"))
    assert viewer.get("/api/v1/maintenance/schedules/").status_code == 403
    manager = api(make_member(org_a, "fleet_manager"))
    resp = manager.post("/api/v1/maintenance/schedules/", {
        "vehicle": v.pk, "name": "Inspection", "interval_days": 30,
        "last_service_at": (NOW - timedelta(days=31)).isoformat()}, format="json")
    assert resp.json()["status"]["state"] == "overdue" and resp.json()["status"]["days_left"] == -1.0


def test_sim_expiry_alert_once_per_date(org_a, fleet):
    _, v, dev = fleet
    AlertRule.unscoped.create(organization=org_a, name="SIM", type="sim_expiry", params={"days": 10},
                              recipient_roles=["org_admin"], cooldown_minutes=0)
    sim = SimCard.unscoped.create(organization=org_a, phone_number="+255700000009",
                                  expiry_date=timezone.localdate() + timedelta(days=5))
    Device.unscoped.filter(pk=dev.pk).update(sim=sim)
    SimCard.unscoped.create(organization=org_a, phone_number="+255700000010",
                            expiry_date=timezone.localdate() + timedelta(days=60))       # outside the window
    assert check_due()["sims"] == 1
    alert = Alert.unscoped.get(type="sim_expiry")
    assert alert.vehicle_id == v.pk and "+255700000009 expires on" in alert.message
    assert check_due()["sims"] == 0                                    # once per expiry date
    SimCard.unscoped.filter(pk=sim.pk).update(expiry_date=timezone.localdate() - timedelta(days=1))
    assert check_due()["sims"] == 1                                    # a new date re-arms (now expired)


def test_no_rule_no_alert(org_a, fleet):
    _, v, _ = fleet
    MaintenanceSchedule.unscoped.create(organization=org_a, vehicle=v, name="x", interval_km=10,
                                        last_service_at=NOW - timedelta(days=10))
    check_due()
    assert not Alert.unscoped.exists()
