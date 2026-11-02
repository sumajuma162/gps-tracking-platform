"""Analytics (spec A13): from the positions_hourly continuous aggregate and processed results; tenant-scoped."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.alerts.models import Alert
from apps.analysis.models import SpeedEvent, Trip
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device
from apps.fleet.models import Vehicle

pytestmark = [pytest.mark.django_db, pytest.mark.skipif("connection.vendor != 'postgresql'")]


def fleet(org, reg, imei):  # type: ignore[no-untyped-def]
    v = Vehicle.unscoped.create(organization=org, registration_number=reg)
    d = Device.unscoped.create(organization=org, name=reg, identifier=imei,
                               device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    return v, d


def test_series_come_from_aggregates_and_never_mix_tenants(org_a, org_b, make_member, api):
    now = timezone.now().replace(minute=30, second=0, microsecond=0)
    va, da = fleet(org_a, "T 1 ANA", "356307042441013")
    vb, db = fleet(org_b, "X 2 OTH", "490154203237518")
    for v, d, km in ((va, da, 40), (vb, db, 999)):
        Trip.unscoped.create(organization=v.organization, device=d, vehicle=v, start_time=now - timedelta(hours=2),
                             end_time=now - timedelta(hours=1), start_lat=-6.8, start_lon=39.2, end_lat=-6.7,
                             end_lon=39.3, distance_km=km, distance_source="gps", duration_s=3600, driving_s=3600,
                             idle_s=0, stopped_s=0, max_speed_kmh=90, avg_moving_speed_kmh=40, points=100,
                             detection_mode="ignition")
        Alert.unscoped.create(organization=v.organization, vehicle=v, type="crash", severity="critical",
                              message="x", occurred_at=now - timedelta(hours=1), last_occurred_at=now)
        with connection.cursor() as cur:     # 6 records an hour ago (one late), one hour of data
            cur.executemany("INSERT INTO positions (organization_id, device_id, vehicle_id, device_time, latitude, "
                            "longitude, speed_kmh, gps_valid, is_late, protocol, record_hash) "
                            "VALUES (%s,%s,%s,%s,-6.8,39.2,40,true,%s,'teltonika',%s)",
                            [(v.organization_id, d.pk, v.pk, now - timedelta(hours=1, minutes=i), i == 0, f"h{i}")
                             for i in range(6)])
    SpeedEvent.unscoped.create(organization=org_a, device=da, vehicle=va, start_time=now - timedelta(hours=2),
                               end_time=now - timedelta(hours=2), duration_s=30, max_speed_kmh=110,
                               threshold_kmh=80, severity="critical", lat=-6.8, lon=39.2, end_lat=-6.8, end_lon=39.2)
    client = api(make_member(org_a, "viewer"))
    body = client.get("/api/v1/analytics/").json()
    assert len(body["days"]) == 14 and body["vehicles"] == 1
    total = {k: sum(d[k] for d in body["days"]) for k in ("distance_km", "trips", "records", "late_records",
                                                          "alerts_critical", "overspeed_critical")}
    assert total == {"distance_km": 40.0, "trips": 1, "records": 6, "late_records": 1, "alerts_critical": 1,
                     "overspeed_critical": 1}
    assert sum(h["records"] for h in body["ingestion_hourly"]) == 6
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM timescaledb_information.continuous_aggregates "
                    "WHERE view_name = 'positions_hourly'")
        assert cur.fetchone()[0] == 1
    assert client.get("/api/v1/analytics/?start=2026-01-01T00:00:00Z&end=2026-06-01T00:00:00Z").status_code == 400
    assert api(make_member(org_a, "driver")).get("/api/v1/analytics/").status_code == 403
