"""Database-level tenant isolation for Phase 2 tables (spec A8 defense in depth).

Ordinary tables get Row-Level Security. ``positions`` cannot: TimescaleDB does not allow compression on a table
with row security ("compression cannot be used on table with row security"). Instead the runtime role gets no
privileges on ``positions`` at all and reads through the ``tenant_positions`` security-barrier view, which
applies the same tenant predicate. See docs/DECISIONS.md.
"""
from django.db import migrations

from common.rls import PostgresOnlySQL, enable_tenant_rls

TABLES = ["vehicles", "vehicle_groups", "drivers", "driver_assignments", "sim_cards", "devices",
          "device_assignments", "device_state"]
# organization_users is deliberately excluded: tenant resolution reads memberships before any tenant
# context exists, so RLS there would hide every membership and break sign-in.

VIEW_SQL = """
CREATE VIEW tenant_positions WITH (security_barrier = true) AS
SELECT * FROM positions
WHERE coalesce(current_setting('app.bypass_rls', true), '') = 'on'
   OR organization_id = NULLIF(current_setting('app.current_org', true), '')::bigint
"""


class Migration(migrations.Migration):
    dependencies = [("tracking", "0002_positions_hypertable"), ("core", "0001_runtime_role")]
    operations = [enable_tenant_rls(t) for t in TABLES] + [
        PostgresOnlySQL(sql=[VIEW_SQL], reverse_sql=["DROP VIEW IF EXISTS tenant_positions"]),
    ]
