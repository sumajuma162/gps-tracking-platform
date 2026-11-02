"""The positions hypertable (spec A9) and its database-level tenant isolation, inside PostgreSQL."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device
from apps.tracking.models import Position
from common.rls import rls_bypass, set_current_org

pytestmark = [pytest.mark.django_db, pytest.mark.skipif("connection.vendor != 'postgresql'")]

#: Declaring the apps makes the post-test flush use TRUNCATE ... CASCADE, which the positions foreign keys need.
#: (Seed data removed by that flush is restored by the ``_seed_data`` fixture in conftest.py.)
ALL_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
            "apps.core", "apps.accounts", "apps.organizations", "apps.rbac", "apps.audit", "apps.device_catalog",
            "apps.fleet", "apps.devices", "apps.tracking"]

INSERT = """INSERT INTO positions (organization_id, device_id, device_time, latitude, longitude, gps_valid,
            protocol, record_hash) VALUES (%s, %s, %s, %s, %s, true, 'teltonika', %s)
            ON CONFLICT (device_id, device_time, record_hash) DO NOTHING"""


def has_timescale() -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        return cur.fetchone() is not None


@pytest.fixture
def devices(org_a, org_b):
    model = DeviceModel.objects.get(slug="teltonika-fmc920")
    a = Device.unscoped.create(organization=org_a, name="A", identifier="490154203237518", device_model=model)
    b = Device.unscoped.create(organization=org_b, name="B", identifier="356307042441013", device_model=model)
    return a, b


def insert(cur, device, when, lat=-6.8161, lon=39.2804, h="h1") -> int:
    cur.execute(INSERT, [device.organization_id, device.pk, when, lat, lon, h])
    return cur.rowcount


@pytest.fixture
def timescale() -> None:
    if not has_timescale():
        pytest.skip("TimescaleDB is not installed in this PostgreSQL server")


def test_hypertable_compression_policies_and_indexes(timescale):
    with connection.cursor() as cur:
        cur.execute("SELECT compression_enabled FROM timescaledb_information.hypertables "
                    "WHERE hypertable_name = 'positions'")
        assert cur.fetchone() == (True,)
        cur.execute("SELECT proc_name FROM timescaledb_information.jobs WHERE hypertable_name = 'positions' "
                    "ORDER BY 1")
        assert [r[0] for r in cur.fetchall()] == ["policy_compression", "policy_retention"]
        cur.execute("SELECT column_name FROM timescaledb_information.dimensions WHERE hypertable_name = 'positions'")
        assert cur.fetchone() == ("device_time",)
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'positions'")
        names = {r[0] for r in cur.fetchall()}
    assert {"positions_dedup", "positions_device_dtime_idx", "positions_vehicle_time_idx", "positions_org_time_idx",
            "positions_geom_idx", "positions_events_idx"} <= names


def test_dedup_check_constraints_and_generated_geometry(devices):
    a, _ = devices
    now = timezone.now()
    with connection.cursor() as cur:
        assert insert(cur, a, now) == 1
        assert insert(cur, a, now) == 0                    # resend: ignored, never duplicated
        assert insert(cur, a, now, h="h2") == 1            # same time, different content: kept
        with pytest.raises(DatabaseError), transaction.atomic():
            insert(cur, a, now, lat=91, h="bad")           # latitude out of range rejected by the database
        cur.execute("SELECT ST_X(geom::geometry), ST_Y(geom::geometry) FROM positions LIMIT 1")
        assert cur.fetchone() == pytest.approx((39.2804, -6.8161))


# Real commits: rolling back a transaction that ran compress_chunk leaves stale relation-cache entries in the
# session (TimescaleDB); production never does that because compression runs as a committed background job.
@pytest.mark.django_db(transaction=True, available_apps=ALL_APPS)
def test_runtime_role_reads_only_its_tenant_including_compressed_chunks(devices):
    a, b = devices
    old, now = timezone.now() - timedelta(days=10), timezone.now()
    with connection.cursor() as cur:
        for dev in (a, b):
            insert(cur, dev, old, h=f"old{dev.pk}")
            insert(cur, dev, now, h=f"new{dev.pk}")
        if has_timescale():
            cur.execute("SELECT count(*) FROM (SELECT compress_chunk(c) FROM show_chunks('positions', "
                        "older_than => INTERVAL '5 days') c) x")
            assert cur.fetchone()[0] >= 1
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("SET LOCAL ROLE gps_app")
        with pytest.raises(DatabaseError), transaction.atomic():
            cur.execute("SELECT count(*) FROM positions")      # no direct access to the hypertable
        with pytest.raises(DatabaseError), transaction.atomic():
            cur.execute("INSERT INTO tenant_positions (organization_id, device_id, device_time, latitude, longitude, "
                        "gps_valid, protocol, record_hash) VALUES (%s, %s, now(), 0, 0, true, 'x', 'x')",
                        [a.organization_id, a.pk])
        cur.execute("SELECT set_config('app.current_org', %s, true)", [str(a.organization_id)])
        cur.execute("SELECT DISTINCT device_id, count(*) OVER () FROM tenant_positions")
        assert cur.fetchall() == [(a.pk, 2)]                   # both of A's rows (one compressed), none of B's
        cur.execute("SELECT set_config('app.current_org', '', true)")
        cur.execute("SELECT count(*) FROM tenant_positions")
        assert cur.fetchone()[0] == 0
        cur.execute("RESET ROLE")
    with connection.cursor() as cur:  # leave no compressed data behind for the database flush
        cur.execute("SELECT count(*) FROM (SELECT decompress_chunk(c, true) FROM show_chunks('positions') c) x")
        cur.execute("DELETE FROM positions")


def test_orm_reads_through_the_tenant_view(devices):
    a, b = devices
    with connection.cursor() as cur:
        insert(cur, a, timezone.now(), h="x1")
        insert(cur, b, timezone.now(), h="x2")
    set_current_org(a.organization_id)
    assert list(Position.objects.values_list("device_id", flat=True)) == [a.pk]
    set_current_org(None)
    assert Position.objects.count() == 0
    with rls_bypass():
        assert Position.objects.count() == 2


def test_view_keeps_chunk_exclusion(devices, timescale):
    """Filtering the view by time must still prune old chunks (the view must not defeat TimescaleDB)."""
    a, _ = devices
    with connection.cursor() as cur:
        for days in (1, 5, 9):
            insert(cur, a, timezone.now() - timedelta(days=days), h=f"d{days}")
        cur.execute("SELECT count(*) FROM show_chunks('positions')")
        chunks = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.current_org', %s, true)", [str(a.organization_id)])
        cur.execute("EXPLAIN SELECT * FROM tenant_positions WHERE device_id = %s AND device_time > now() - "
                    "INTERVAL '2 days'", [a.pk])
        plan = "\n".join(r[0] for r in cur.fetchall())
    scanned = plan.count("_hyper_")
    assert chunks >= 3 and 0 < scanned < chunks, plan
