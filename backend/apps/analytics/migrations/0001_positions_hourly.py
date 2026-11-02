"""``positions_hourly``: a TimescaleDB continuous aggregate of positions per device and hour (spec A13 Analytics).

Real-time aggregation is on (the newest, not yet materialized hours are computed on read), and a policy materializes
the last 3 days every 30 minutes, so backlog uploads up to 3 days late are folded in. The web app's restricted role
cannot read it directly (like ``positions``); it reads ``tenant_positions_hourly``, which applies the tenant predicate.
"""
from django.conf import settings
from django.db import migrations

CAGG_SQL = """
CREATE MATERIALIZED VIEW positions_hourly WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', device_time) AS bucket,
       organization_id, device_id, vehicle_id,
       count(*) AS records,
       count(*) FILTER (WHERE gps_valid AND NOT is_outlier) AS valid_fixes,
       count(*) FILTER (WHERE is_late) AS late_records,
       count(*) FILTER (WHERE speed_kmh >= 5 AND gps_valid AND NOT is_outlier) AS moving_records,
       max(speed_kmh) FILTER (WHERE gps_valid AND NOT is_outlier) AS max_speed_kmh
FROM positions
GROUP BY bucket, organization_id, device_id, vehicle_id
WITH NO DATA
"""

VIEW_SQL = """
CREATE VIEW tenant_positions_hourly WITH (security_barrier = true) AS
SELECT * FROM positions_hourly
WHERE coalesce(current_setting('app.bypass_rls', true), '') = 'on'
   OR organization_id = NULLIF(current_setting('app.current_org', true), '')::bigint
"""


def forwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    conn = schema_editor.connection
    if conn.vendor != "postgresql":
        return
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        if cur.fetchone() is None:
            if getattr(settings, "REQUIRE_TIMESCALEDB", True):
                raise RuntimeError("positions_hourly needs TimescaleDB (set REQUIRE_TIMESCALEDB=0 for a plain view).")
            cur.execute(CAGG_SQL.replace("WITH (timescaledb.continuous, timescaledb.materialized_only = false)", "")
                        .replace("time_bucket(INTERVAL '1 hour', device_time)", "date_trunc('hour', device_time)")
                        .replace("MATERIALIZED VIEW", "VIEW").replace("WITH NO DATA", ""))
        else:
            cur.execute(CAGG_SQL)
            cur.execute("SELECT add_continuous_aggregate_policy('positions_hourly', start_offset => INTERVAL '3 days',"
                        " end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '30 minutes')")
        cur.execute(VIEW_SQL)
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'gps_app'")
        if cur.fetchone():
            cur.execute('REVOKE ALL ON positions_hourly FROM gps_app')
            cur.execute('GRANT SELECT ON tenant_positions_hourly TO gps_app')


def backwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        cur.execute("DROP VIEW IF EXISTS tenant_positions_hourly")
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        kind = "MATERIALIZED VIEW" if cur.fetchone() else "VIEW"
        cur.execute(f"DROP {kind} IF EXISTS positions_hourly CASCADE")


class Migration(migrations.Migration):
    atomic = False           # continuous aggregates are created outside a transaction block
    dependencies = [("tracking", "0008_processingcheckpoint_state"), ("core", "0001_runtime_role")]
    operations = [migrations.RunPython(forwards, backwards)]
