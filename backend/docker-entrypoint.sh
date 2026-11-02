#!/bin/sh
# Waits for PostgreSQL, then (only when RUN_MIGRATIONS=1) migrates and collects static files.
set -eu
python - <<'PY'
import os, sys, time
import psycopg
dsn = dict(host=os.environ.get("DATABASE_HOST", "postgres"), port=os.environ.get("DATABASE_PORT", "5432"),
           dbname=os.environ.get("DATABASE_NAME", "gps"), user=os.environ.get("DATABASE_USER", "gps"),
           password=os.environ.get("DATABASE_PASSWORD", ""), connect_timeout=3)
for attempt in range(60):
    try:
        psycopg.connect(**dsn).close()
        break
    except psycopg.OperationalError:
        print(f"waiting for database ({attempt + 1}/60)", flush=True)
        time.sleep(2)
else:
    sys.exit("database not reachable after 120 s")
PY
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  # Migrations run as the database owner; only the web process switches to the restricted RLS role.
  DB_RUNTIME_ROLE= python manage.py migrate --noinput
  DB_RUNTIME_ROLE= python manage.py collectstatic --noinput
fi
exec "$@"
