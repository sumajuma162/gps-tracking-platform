"""Alert rule validation, notification center and preferences APIs (spec A11, A13)."""
from __future__ import annotations

import pytest
from django.utils import timezone

from apps.alerts.models import Notification
from apps.fleet.models import Vehicle
from apps.geofences.models import Geofence

pytestmark = pytest.mark.django_db
RULES = "/api/v1/alert-rules/"


@pytest.fixture
def admin(org_a, make_member, api):
    return api(make_member(org_a, "org_admin"))


@pytest.mark.parametrize("body,field", [
    ({"type": "overspeed", "params": {"minutes": 5}}, "params"),
    ({"type": "overspeed", "params": {"limit_kmh": -5}}, "params"),
    ({"type": "overspeed", "applies_to_all": False}, "vehicles"),
    ({"type": "overspeed", "recipient_roles": []}, "recipient_roles"),
    ({"type": "overspeed", "recipient_roles": ["wizard"]}, "recipient_roles"),
    ({"type": "overspeed", "schedule": {"from": "25:00", "to": "06:00"}}, "schedule"),
    ({"type": "geofence_enter", "params": {"geofences": [999999]}}, "params"),
])
def test_rule_validation(admin, body, field):
    resp = admin.post(RULES, {"name": "R", "recipient_roles": ["fleet_manager"], **body}, format="json")
    assert resp.status_code == 400 and field in resp.json()["error"]["details"], resp.json()


def test_rule_create_scoped_and_permissions(org_a, org_b, make_member, api, admin):
    mine = Vehicle.unscoped.create(organization=org_a, registration_number="T 1 RUL")
    fence = Geofence.unscoped.create(organization=org_a, name="Depot", kind="circle", center_lat=-6.8, center_lon=39.2,
                                     radius_m=200)
    other_fence = Geofence.unscoped.create(organization=org_b, name="Theirs", kind="circle", center_lat=-6.8,
                                           center_lon=39.2, radius_m=200)
    ok = admin.post(RULES, {"name": "Depot arrivals", "type": "geofence_enter", "params": {"geofences": [fence.pk]},
                            "applies_to_all": False, "vehicles": [mine.pk], "recipient_roles": ["fleet_manager"],
                            "schedule": {"days": [0, 1, 2, 3, 4], "from": "18:00", "to": "06:00"}}, format="json")
    assert ok.status_code == 201, ok.json()
    leak = admin.post(RULES, {"name": "X", "type": "geofence_enter", "params": {"geofences": [other_fence.pk]},
                              "recipient_roles": ["fleet_manager"]}, format="json")
    assert leak.status_code == 400                                    # another tenant's geofence
    assert api(make_member(org_a, "monitoring_officer")).post(RULES, {"name": "Y", "type": "crash",
                                                                      "recipient_roles": ["org_admin"]},
                                                              format="json").status_code == 403


def test_notification_center(org_a, make_member, api):
    user = make_member(org_a, "monitoring_officer")
    other = make_member(org_a, "viewer", "other@a.test")
    for i in range(3):
        Notification.unscoped.create(organization=org_a, user=user, channel="in_app", title=f"N{i}", status="sent")
    Notification.unscoped.create(organization=org_a, user=user, channel="email", title="E", status="sent")
    Notification.unscoped.create(organization=org_a, user=other, channel="in_app", title="not yours", status="sent")
    client = api(user)
    assert client.get("/api/v1/notifications/unread/").json() == {"unread": 3}
    items = client.get("/api/v1/notifications/").json()["results"]
    assert {n["title"] for n in items} == {"N0", "N1", "N2"}          # own, in-app only
    assert client.post(f"/api/v1/notifications/{items[0]['id']}/read/").json()["read_at"]
    theirs = Notification.unscoped.get(title="not yours")
    assert client.post(f"/api/v1/notifications/{theirs.pk}/read/").status_code == 404
    assert client.post("/api/v1/notifications/read-all/").json() == {"updated": 2}
    assert client.get("/api/v1/notifications/unread/").json() == {"unread": 0}


def test_preferences_and_quiet_hours(org_a, make_member, api):
    user = make_member(org_a, "fleet_manager")
    client = api(user)
    url = "/api/v1/notification-preferences/"
    body = {"preferences": [{"channel": "email", "alert_type": "", "min_severity": "warning", "enabled": True},
                            {"channel": "in_app", "alert_type": "ignition_on", "min_severity": "info",
                             "enabled": False}],
            "quiet_hours_start": "22:00", "quiet_hours_end": "06:00", "time_zone": "Africa/Dar_es_Salaam"}
    saved = client.put(f"{url}replace/", body, format="json").json()
    assert len(saved["preferences"]) == 2 and saved["quiet_hours_start"] == "22:00:00"
    assert client.put(f"{url}replace/", {**body, "quiet_hours_end": None}, format="json").status_code == 400
    assert client.put(f"{url}replace/", {**body, "time_zone": "Mars/Olympus"}, format="json").status_code == 400
    assert "overspeed" in client.get(url).json()["alert_types"]


def test_channels_prepared_but_not_enabled(org_a, admin):
    sms = admin.post("/api/v1/notification-channels/", {"kind": "sms", "provider": "", "settings": {"sender": "FLEET"},
                                                        "is_enabled": True}, format="json")
    assert sms.status_code == 400 and "is_enabled" in sms.json()["error"]["details"]
    stored = admin.post("/api/v1/notification-channels/", {"kind": "sms", "settings": {"sender": "FLEET"}},
                        format="json")
    assert stored.status_code == 201 and stored.json()["is_enabled"] is False


def test_alert_list_filters_and_counts(org_a, make_member, api):
    from apps.alerts.models import Alert
    now = timezone.now()
    for kind, sev, st in [("crash", "critical", "new"), ("overspeed", "warning", "acknowledged"),
                          ("overspeed", "warning", "resolved")]:
        Alert.unscoped.create(organization=org_a, type=kind, severity=sev, status=st, message=kind, occurred_at=now,
                              last_occurred_at=now)
    client = api(make_member(org_a, "viewer"))
    assert len(client.get("/api/v1/alerts/?status=open").json()["results"]) == 2
    assert len(client.get("/api/v1/alerts/?severity=critical").json()["results"]) == 1
    assert client.get("/api/v1/alerts/counts/").json() == {"open": 2, "critical_open": 1, "today": 3}
    one = client.get("/api/v1/alerts/").json()["results"][0]["id"]
    assert client.post(f"/api/v1/alerts/{one}/acknowledge/").status_code == 403   # viewers can't acknowledge
