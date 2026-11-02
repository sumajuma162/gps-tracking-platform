"""Phase 8 acceptance: the alert workflow with the real processor against PostgreSQL (spec A11).

Deduplication, cooldown, in-app push, email through the Celery task (with retries), quiet hours, delayed alerts,
acknowledge / resolve and automatic resolution.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from unittest import mock

import pytest
from django.core import mail
from django.db import connection
from django.utils import timezone

from apps.alerts.models import Alert, AlertRule, Notification, NotificationPreference
from apps.alerts.tasks import deliver_queued_notifications
from apps.analysis.tests.test_processor_analysis import LAT, LON, entries, insert_positions, journey, processor
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import Vehicle
from apps.organizations.models import Organization
from apps.tracking.tests.test_positions import ALL_APPS

pytest.importorskip("gpsproc.app", reason="add ../processor to PYTHONPATH")

pytestmark = [pytest.mark.skipif("connection.vendor != 'postgresql'"),
              pytest.mark.django_db(transaction=True, available_apps=[*ALL_APPS, "apps.analysis", "apps.geofences",
                                                                       "apps.alerts", "apps.commands"])]


class Layer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    async def group_send(self, group: str, message: dict) -> None:  # type: ignore[type-arg]
        self.sent.append((group, message))


async def feed(proc, batches):  # type: ignore[no-untyped-def]
    import asyncpg
    proc.pool = await asyncpg.create_pool(proc.config.database_url, min_size=1, max_size=2)
    try:
        for b in batches:
            await proc.handle(b)
    finally:
        await proc.pool.close()


def make_processor(layer: Layer):  # type: ignore[no-untyped-def]
    proc = processor()
    proc.layer = layer          # type: ignore[assignment]
    proc.alerts.layer = layer
    return proc


@pytest.fixture
def world(make_member):  # type: ignore[no-untyped-def]
    org = Organization.objects.create(name="Alerts Ltd", slug="alerts-ltd")
    vehicle = Vehicle.unscoped.create(organization=org, registration_number="T 900 ALR")
    device = Device.unscoped.create(organization=org, name="Alerting", identifier="356307042441013",
                                    device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    DeviceAssignment.unscoped.create(organization=org, device=device, vehicle=vehicle, assigned_at=timezone.now())
    from apps.tracking.models import DeviceState  # the gateway creates this on the first packet
    DeviceState.unscoped.create(organization=org, device=device, vehicle=vehicle, last_packet_at=timezone.now(),
                                gps_valid=True, latitude=LAT, longitude=LON, device_time=timezone.now())
    manager = make_member(org, "fleet_manager", "manager@alerts.test")
    night = make_member(org, "monitoring_officer", "night@alerts.test")
    night.quiet_hours_start, night.quiet_hours_end, night.time_zone = dtime(0, 0), dtime(23, 59), "UTC"
    night.save()
    NotificationPreference.unscoped.create(organization=org, user=manager, channel="email", alert_type="",
                                           min_severity="warning")
    speeding = AlertRule.unscoped.create(organization=org, name="Speeding", type="overspeed", params={"limit_kmh": 80},
                                         severity="warning", recipient_roles=["fleet_manager", "monitoring_officer"])
    offline = AlertRule.unscoped.create(organization=org, name="Offline", type="device_offline", severity="critical",
                                        recipient_roles=["fleet_manager", "monitoring_officer"])
    return org, vehicle, device, manager, night, speeding, offline


def test_alert_workflow(world, api):  # type: ignore[no-untyped-def]
    org, vehicle, device, manager, night, speeding, offline = world
    layer = Layer()
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    recs = journey(t0, n_trips=2)                         # two trips, each with one minute at 95 km/h
    insert_positions(device, vehicle, recs)
    asyncio.run(feed(make_processor(layer), [entries(device, recs)]))

    # Dedup + cooldown: two episodes 25 minutes apart -> one open alert, two occurrences
    alerts = list(Alert.unscoped.filter(type="overspeed"))
    assert len(alerts) == 1 and alerts[0].occurrences == 2 and alerts[0].severity == "warning"
    assert alerts[0].message == "T 900 ALR exceeded 80 km/h (top 95 km/h)" and not alerts[0].delayed
    # Both episodes fall in the same processing run: one notification round (cooldown 10 min, run time ~ now)
    inapp = Notification.unscoped.filter(alert=alerts[0], channel="in_app")
    assert {(n.user_id, n.status) for n in inapp} == {(manager.pk, "sent"), (night.pk, "suppressed")}
    assert Notification.unscoped.get(alert=alerts[0], channel="in_app", user=night).reason == "quiet_hours"
    pushed = [g for g, _ in layer.sent if g.startswith("notify.")]
    assert pushed == [f"notify.user.{manager.pk}"]       # quiet hours: no live push to the night officer
    email = Notification.unscoped.get(alert=alerts[0], channel="email")
    assert email.user_id == manager.pk and email.status == "queued"   # night officer: default email = critical only

    # Celery delivery: sent through Django's mail backend
    with mock.patch("django.core.mail.backends.locmem.EmailBackend.send_messages", return_value=1) as send:
        assert deliver_queued_notifications()["sent"] == 1
    assert send.called and Notification.unscoped.get(pk=email.pk).status == "sent"

    # Acknowledge and resolve through the API; a new episode afterwards opens a new alert
    client = api(manager)
    ack = client.post(f"/api/v1/alerts/{alerts[0].pk}/acknowledge/", {"notes": "Called the driver"}, format="json")
    assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
    assert client.post(f"/api/v1/alerts/{alerts[0].pk}/acknowledge/").status_code == 400
    assert client.post(f"/api/v1/alerts/{alerts[0].pk}/resolve/").json()["status"] == "resolved"
    later = journey(t0 + timedelta(hours=2), n_trips=1)
    for i, r in enumerate(later):
        r["record_hash"] = f"later{i}"
    insert_positions(device, vehicle, later)
    asyncio.run(feed(make_processor(layer), [entries(device, later)]))
    assert Alert.unscoped.filter(type="overspeed").count() == 2
    assert Alert.unscoped.filter(type="overspeed", status="new").count() == 1

    # Delayed: an alert from backlog data is stored with the label and nobody is notified
    sent_before = Notification.unscoped.count()

    backlog = journey(t0 - timedelta(hours=4), n_trips=1)
    for i, r in enumerate(backlog):
        r["is_late"], r["record_hash"] = True, f"bk{i}"
    insert_positions(device, vehicle, backlog, late=True)

    async def replay_backlog() -> None:
        import asyncpg
        proc = make_processor(layer)
        proc.pool = await asyncpg.create_pool(proc.config.database_url, min_size=1, max_size=2)
        try:
            await proc.handle(entries(device, backlog))
            await proc.analysis.run_due_recomputes(proc.pool, 0, on_items=proc._delayed_alerts)
        finally:
            await proc.pool.close()
    Alert.unscoped.filter(type="overspeed").update(status="resolved")
    asyncio.run(replay_backlog())
    delayed = Alert.unscoped.get(type="overspeed", status="new")
    assert delayed.delayed and Notification.unscoped.count() == sent_before

    # Offline -> critical alert to both (critical ignores quiet hours); back online -> resolved by the system
    with connection.cursor() as cur:
        cur.execute("UPDATE device_state SET last_packet_at = now() - interval '20 minutes', status = 'ONLINE_STOPPED' "
                    "WHERE device_id = %s", [device.pk])

    async def sweep_then_report() -> None:
        import asyncpg
        proc = make_processor(layer)
        proc.pool = await asyncpg.create_pool(proc.config.database_url, min_size=1, max_size=2)
        try:
            assert await proc.sweep() == 1
            now = datetime.now(UTC).replace(microsecond=0)
            back = [{**recs[-1], "device_time": now.isoformat(), "server_time": now.isoformat(), "record_hash": "back",
                     "latitude": LAT, "longitude": LON}]
            await proc.handle(entries(device, back))
        finally:
            await proc.pool.close()
    asyncio.run(sweep_then_report())
    off = Alert.unscoped.get(type="device_offline")
    assert off.severity == "critical" and off.status == "resolved" and "automatically" in off.notes
    assert {n.user_id for n in Notification.unscoped.filter(alert=off, channel="in_app", status="sent")} == {
        manager.pk, night.pk}


def test_email_retries_then_fails(world):  # type: ignore[no-untyped-def]
    org, *_rest = world
    manager = _rest[2]
    n = Notification.unscoped.create(organization=org, user=manager, channel="email", title="t", body="b",
                                     severity="critical", status="queued", next_attempt_at=timezone.now())
    with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("SMTP down")):
        for _ in range(4):
            deliver_queued_notifications()
            Notification.unscoped.filter(pk=n.pk).update(next_attempt_at=timezone.now())
    n.refresh_from_db()
    assert n.status == "failed" and n.attempts == 4 and "SMTP down" in n.error


def test_prepared_channels_fail_cleanly(world):  # type: ignore[no-untyped-def]
    org, *_rest = world
    n = Notification.unscoped.create(organization=org, user=_rest[2], channel="sms", title="t", body="b",
                                     status="queued", next_attempt_at=timezone.now())
    deliver_queued_notifications()
    n.refresh_from_db()
    assert n.status == "failed" and "no sms provider" in n.error and n.attempts == 1    # no retries for this
    _ = json, mail
