"""The real processor (against PostgreSQL) applies per-organization thresholds and writes webhook events in the same
transaction as the results, once each (spec A10 "configurable per organization", A13 webhooks)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection  # noqa: F401  (used by the skipif condition)

from apps.analysis.models import SpeedEvent, Trip
from apps.analysis.tests.test_processor_analysis import entries, fleet, insert_positions, journey, processor, run
from apps.integrations import dispatch
from apps.integrations.models import Webhook, WebhookDelivery, WebhookEvent
from apps.settings_app.models import OrganizationSettings
from apps.tracking.tests.test_positions import ALL_APPS

pytest.importorskip("gpsproc.app", reason="add ../processor to PYTHONPATH")

pytestmark = [pytest.mark.skipif("connection.vendor != 'postgresql'"),
              pytest.mark.django_db(transaction=True, available_apps=[
                  *ALL_APPS, "apps.analysis", "apps.geofences", "apps.alerts", "apps.integrations",
                  "apps.settings_app"])]
_ = fleet   # the fixture, imported for use here


def test_org_thresholds_and_webhook_outbox(fleet):  # type: ignore[no-untyped-def]  # noqa: F811
    org, vehicle, driver, device, depot = fleet
    Webhook.unscoped.create(organization=org, name="ERP", url="http://127.0.0.1:9/never", secret="s",
                            events=["trip.completed", "geofence.event"])
    # This organization treats 95 km/h as acceptable: warning at 100, critical at 120.
    OrganizationSettings.unscoped.create(organization=org, values={"overspeed_warning_kmh": 100,
                                                                   "overspeed_critical_kmh": 120})
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=3)
    recs = journey(t0, n_trips=2)
    insert_positions(device, vehicle, recs)
    asyncio.run(run(processor(), [entries(device, recs)]))
    assert Trip.unscoped.count() == 2
    assert SpeedEvent.unscoped.count() == 0                     # the default (80 km/h) would have produced 2
    events = list(WebhookEvent.unscoped.order_by("occurred_at").values_list("event", flat=True))
    assert events.count("trip.completed") == 2 and "geofence.event" in events
    trip_event = WebhookEvent.unscoped.filter(event="trip.completed").first()
    assert trip_event.payload["vehicle_id"] == vehicle.pk and trip_event.payload["distance_km"] > 10
    # Replaying the same records (stream redelivery) doesn't add events.
    before = WebhookEvent.unscoped.count()
    asyncio.run(run(processor(), [entries(device, recs)]))
    assert WebhookEvent.unscoped.count() == before
    assert dispatch.fan_out() == before
    assert WebhookDelivery.unscoped.filter(status="pending").count() == before


def test_no_webhook_no_events(fleet):  # type: ignore[no-untyped-def]  # noqa: F811
    org, vehicle, driver, device, depot = fleet
    recs = journey(datetime.now(UTC).replace(microsecond=0) - timedelta(hours=3), n_trips=1)
    insert_positions(device, vehicle, recs)
    asyncio.run(run(processor(), [entries(device, recs)]))
    assert Trip.unscoped.count() == 1 and SpeedEvent.unscoped.count() == 1     # default thresholds
    assert WebhookEvent.unscoped.count() == 0
