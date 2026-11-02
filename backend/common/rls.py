"""PostgreSQL Row-Level Security helpers (spec A8, defense in depth).

The database owner (used for migrations, Celery and the gateway) is a superuser
and therefore bypasses RLS. Web requests run as the restricted role
``settings.DB_RUNTIME_ROLE`` (``SET ROLE`` on connect), so policies apply there.
Per request, ``app.current_org`` is set transaction-locally; platform code that
must cross tenants uses ``rls_bypass()``.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection
from django.db.migrations.operations import RunSQL

ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
#: Tables the runtime role may only SELECT/INSERT (enforced again on every migrate).
APPEND_ONLY_TABLES: set[str] = set()
#: Tables/views the runtime role may only SELECT.
READ_ONLY_TABLES: set[str] = set()
#: Tables the runtime role may not touch at all (read through a filtering view instead).
NO_ACCESS_TABLES: set[str] = set()


def is_postgres() -> bool:
    return connection.vendor == "postgresql"


def set_current_org(org_id: int | None) -> None:
    if is_postgres():
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, true)", ["" if org_id is None else str(org_id)])


@contextmanager
def rls_bypass() -> Iterator[None]:
    """Temporarily allow cross-tenant access inside the current transaction."""
    if not is_postgres():
        yield
        return
    with connection.cursor() as cur:
        cur.execute("SELECT coalesce(current_setting('app.bypass_rls', true), '')")
        previous = cur.fetchone()[0]
        cur.execute("SELECT set_config('app.bypass_rls', 'on', true)")
    try:
        yield
    finally:
        # A failed transaction rolls back, which discards this transaction-local setting anyway;
        # issuing SQL in it would raise and mask the original error.
        if not connection.needs_rollback:
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.bypass_rls', %s, true)", [previous])


class PostgresOnlySQL(RunSQL):
    """RunSQL that is skipped on non-PostgreSQL databases (e.g. quick SQLite runs)."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):  # type: ignore[no-untyped-def]
        if schema_editor.connection.vendor == "postgresql":
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):  # type: ignore[no-untyped-def]
        if schema_editor.connection.vendor == "postgresql":
            super().database_backwards(app_label, schema_editor, from_state, to_state)


def _policy_expr(column: str) -> str:
    return (f"(coalesce(current_setting('app.bypass_rls', true), '') = 'on' "
            f"OR {column} = NULLIF(current_setting('app.current_org', true), '')::bigint)")


def enable_tenant_rls(table: str, column: str = "organization_id") -> PostgresOnlySQL:
    """Migration operation: enable + force RLS with the standard tenant policy."""
    expr = _policy_expr(column)
    return PostgresOnlySQL(
        sql=[
            f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
            f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
            f'CREATE POLICY tenant_isolation ON "{table}" USING {expr} WITH CHECK {expr}',
        ],
        reverse_sql=[
            f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"',
            f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY',
            f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY',
        ],
    )
