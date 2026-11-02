"""Create the ``positions`` hypertable (spec A9).

Raw SQL because the ORM cannot declare a primary key that contains the partitioning column.
Requires PostgreSQL + PostGIS + TimescaleDB. Without TimescaleDB the migration stops with an error unless
REQUIRE_TIMESCALEDB=0 (then a plain table is created, for quick local tests only). Skipped on SQLite.
"""
from django.conf import settings
from django.db import migrations

TABLE_SQL = """
CREATE TABLE positions (
    id               bigint GENERATED ALWAYS AS IDENTITY,
    organization_id  bigint NOT NULL REFERENCES organizations (id) DEFERRABLE INITIALLY DEFERRED,
    device_id        bigint NOT NULL REFERENCES devices (id) DEFERRABLE INITIALLY DEFERRED,
    vehicle_id       bigint NULL REFERENCES vehicles (id) DEFERRABLE INITIALLY DEFERRED,
    device_time      timestamptz NOT NULL,
    server_time      timestamptz NOT NULL DEFAULT now(),
    latitude         double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude        double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    geom             geography(Point, 4326)
                     GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography) STORED,
    altitude         real,
    speed_kmh        real CHECK (speed_kmh IS NULL OR speed_kmh >= 0),
    heading          real,
    satellites       smallint,
    hdop             real,
    gps_valid        boolean NOT NULL,
    is_outlier       boolean NOT NULL DEFAULT false,
    is_late          boolean NOT NULL DEFAULT false,
    ignition         boolean,
    motion           boolean,
    odometer_km      double precision,
    engine_hours     double precision,
    battery_voltage  real,
    external_voltage real,
    battery_level    real,
    gsm_signal       smallint,
    event_code       integer,
    events           jsonb NOT NULL DEFAULT '[]'::jsonb,
    sensors          jsonb NOT NULL DEFAULT '{}'::jsonb,
    protocol         varchar(32) NOT NULL,
    raw_metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
    record_hash      varchar(64) NOT NULL,
    PRIMARY KEY (id, device_time),
    CONSTRAINT positions_dedup UNIQUE (device_id, device_time, record_hash)
)
"""

INDEXES = [
    "CREATE INDEX positions_device_dtime_idx ON positions (device_id, device_time DESC)",
    "CREATE INDEX positions_vehicle_time_idx ON positions (vehicle_id, device_time DESC) WHERE vehicle_id IS NOT NULL",
    "CREATE INDEX positions_org_time_idx ON positions (organization_id, device_time DESC)",
    "CREATE INDEX positions_geom_idx ON positions USING GIST (geom)",
    "CREATE INDEX positions_events_idx ON positions (device_id, device_time DESC) WHERE events <> '[]'::jsonb",
]


def forwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    conn = schema_editor.connection
    if conn.vendor != "postgresql":
        return
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        has_timescale = cur.fetchone() is not None
        if has_timescale:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        elif settings.REQUIRE_TIMESCALEDB:
            raise RuntimeError("TimescaleDB is not installed in this PostgreSQL server. Use the "
                               "timescale/timescaledb-ha image, or set REQUIRE_TIMESCALEDB=0 for a quick local "
                               "test database without compression and retention.")
        cur.execute(TABLE_SQL)
        if has_timescale:
            cur.execute("SELECT create_hypertable('positions', 'device_time', chunk_time_interval => INTERVAL '1 day')")
        for sql in INDEXES:
            cur.execute(sql)
        if has_timescale:
            cur.execute("ALTER TABLE positions SET (timescaledb.compress, "
                        "timescaledb.compress_segmentby = 'device_id', "
                        "timescaledb.compress_orderby = 'device_time DESC')")
            cur.execute("SELECT add_compression_policy('positions', INTERVAL '7 days')")
            cur.execute("SELECT add_retention_policy('positions', %s::interval)",
                        [f"{int(settings.POSITIONS_MAX_RETENTION_DAYS)} days"])


def backwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS positions CASCADE")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [
        ("tracking", "0001_initial"),
        ("devices", "0002_initial"),
        ("fleet", "0001_initial"),
        ("organizations", "0002_initial"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
