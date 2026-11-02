"""Dependency probes used by /readiness and the System Health page."""
from __future__ import annotations

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def check_database() -> tuple[bool, str]:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return True, "ok"
    except Exception as exc:
        return False, type(exc).__name__


def check_migrations() -> tuple[bool, str]:
    try:
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return (not pending), "ok" if not pending else f"{len(pending)} pending"
    except Exception as exc:
        return False, type(exc).__name__


def check_redis() -> tuple[bool, str]:
    try:
        import redis

        redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2).ping()
        return True, "ok"
    except Exception as exc:
        return False, type(exc).__name__
