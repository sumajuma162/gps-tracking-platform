"""raw_packets hypertable (14-day retention, never compressed, so RLS applies) and RLS for gateway tables."""
from django.conf import settings
from django.db import migrations

from common.rls import enable_tenant_rls

TABLE_SQL = """
CREATE TABLE raw_packets (
    id              bigint GENERATED ALWAYS AS IDENTITY,
    organization_id bigint NOT NULL REFERENCES organizations (id) DEFERRABLE INITIALLY DEFERRED,
    device_id       bigint NOT NULL REFERENCES devices (id) DEFERRABLE INITIALLY DEFERRED,
    received_at     timestamptz NOT NULL DEFAULT now(),
    direction       varchar(3) NOT NULL CHECK (direction IN ('in', 'out')),
    transport       varchar(4) NOT NULL,
    payload_hex     text NOT NULL,
    decode_result   varchar(60) NOT NULL,
    PRIMARY KEY (id, received_at)
)
"""


def forwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    conn = schema_editor.connection
    if conn.vendor != "postgresql":
        return
    with conn.cursor() as cur:
        cur.execute(TABLE_SQL)
        cur.execute("CREATE INDEX raw_packets_device_idx ON raw_packets (device_id, received_at DESC)")
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        if cur.fetchone():
            cur.execute("SELECT create_hypertable('raw_packets', 'received_at', chunk_time_interval => INTERVAL '1 day')")
            cur.execute("SELECT add_retention_policy('raw_packets', %s::interval)",
                        [f"{int(settings.RAW_PACKET_RETENTION_DAYS)} days"])


def backwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS raw_packets")


class Migration(migrations.Migration):
    dependencies = [("tracking", "0005_rawpacket"), ("devices", "0003_unregistereddeviceattempt_deviceconnection")]
    operations = [
        migrations.RunPython(forwards, backwards),
        enable_tenant_rls("raw_packets"),
        enable_tenant_rls("device_connections"),
    ]
