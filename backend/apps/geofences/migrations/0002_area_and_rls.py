"""PostGIS geography for geofences (kept in sync by the API) and RLS on the Phase 7 tenant tables."""
from django.db import migrations

from common.rls import PostgresOnlySQL, enable_tenant_rls

TENANT_TABLES = ["geofences", "geofence_rules", "geofence_events", "trips", "stops", "idle_events", "speed_events",
                 "driving_behavior_events"]


class Migration(migrations.Migration):
    # PostGIS must exist first: in an empty database this could otherwise run before the positions migration.
    dependencies = [("geofences", "0001_initial"), ("analysis", "0001_initial"), ("core", "0001_runtime_role"),
                    ("tracking", "0002_positions_hypertable")]
    operations = [
        PostgresOnlySQL(sql=["CREATE EXTENSION IF NOT EXISTS postgis",
                             "ALTER TABLE geofences ADD COLUMN area geography(Polygon, 4326)",
                             "CREATE INDEX geofences_area_idx ON geofences USING GIST (area)"],
                        reverse_sql=["ALTER TABLE geofences DROP COLUMN area"]),
        *[enable_tenant_rls(t) for t in TENANT_TABLES],
    ]
