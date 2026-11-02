"""Database role wiring: runtime SET ROLE and post-migrate grants."""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from common.rls import APPEND_ONLY_TABLES, NO_ACCESS_TABLES, READ_ONLY_TABLES, ROLE_RE

APP_ROLE = "gps_app"


@receiver(connection_created)
def set_runtime_role(sender: Any, connection: Any, **kwargs: Any) -> None:
    role = getattr(settings, "DB_RUNTIME_ROLE", "")
    if role and connection.vendor == "postgresql":
        if not ROLE_RE.match(role):
            raise ValueError("DB_RUNTIME_ROLE has an invalid name")
        with connection.cursor() as cur:
            cur.execute(f'SET ROLE "{role}"')


@receiver(post_migrate)
def grant_runtime_role(sender: Any, using: str = "default", **kwargs: Any) -> None:
    from django.db import connections

    conn = connections[using]
    if conn.vendor != "postgresql" or sender.name != "apps.core":
        return
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [APP_ROLE])
        if cur.fetchone() is None:
            return
        cur.execute(f'GRANT USAGE ON SCHEMA public TO "{APP_ROLE}"')
        cur.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{APP_ROLE}"')
        cur.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{APP_ROLE}"')
        cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'p')")
        existing = {row[0] for row in cur.fetchall()}
        for table in sorted(APPEND_ONLY_TABLES & existing):
            cur.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON "{table}" FROM "{APP_ROLE}"')
        for table in sorted(READ_ONLY_TABLES & existing):
            cur.execute(f'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON "{table}" FROM "{APP_ROLE}"')
        for table in sorted(NO_ACCESS_TABLES & existing):
            cur.execute(f'REVOKE ALL ON "{table}" FROM "{APP_ROLE}"')
