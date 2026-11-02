#!/bin/bash
# Run the whole platform WITHOUT Docker, with the same topology as docker-compose.yml (edge Nginx in front of the
# SPA and the backend; backend as the restricted gps_app role under RLS; Celery worker and beat; gateway;
# processor), for the Playwright suite on machines where Docker isn't available. Needs PostgreSQL 16 with PostGIS
# and TimescaleDB, Redis 7, Nginx, Python deps of backend/gateway/processor, and a built frontend (npm run build).
#
#   scripts/e2e/native-stack.sh fresh   # new database gps_e2e, migrate, seed, start everything
#   scripts/e2e/native-stack.sh start | stop
#   E2E_BASE_URL=http://localhost E2E_PASSWORD=change-me-Demo-Pass-1 E2E_TRACKER_HOST=localhost \
#     E2E_SERVER_HOST=localhost E2E_MAIL_DIR=/tmp/e2e-mail python -m pytest e2e -p no:django
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export DJANGO_SETTINGS_MODULE=config.settings.dev DEBUG=1 ALLOWED_HOSTS=localhost,127.0.0.1 CSRF_TRUSTED_ORIGINS=http://localhost
export FRONTEND_URL=http://localhost REFRESH_COOKIE_SECURE=0 SEED_DEMO_PASSWORD=${SEED_DEMO_PASSWORD:-change-me-Demo-Pass-1}
export DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY:-e2e-local-0123456789abcdefghijklmnopqrstuvwxyz-0123456789}
export DATABASE_HOST=${DATABASE_HOST:-127.0.0.1} DATABASE_NAME=gps_e2e DATABASE_USER=${DATABASE_USER:-gps}
export DATABASE_PASSWORD=${DATABASE_PASSWORD:?set DATABASE_PASSWORD} REDIS_URL=${REDIS_URL:-redis://localhost:6379/2}
export THROTTLE_LOGIN=200/min THROTTLE_REFRESH=1000/min MAP_TILE_URL= TRACKER_SERVER_HOST=localhost
export EMAIL_BACKEND=django.core.mail.backends.filebased.EmailBackend EMAIL_FILE_PATH=/tmp/e2e-mail
export GATEWAY_HEALTH_URL=http://127.0.0.1:8081/health PROCESSOR_HEALTH_URL=http://127.0.0.1:8082/health
export GATEWAY_INTERNAL_URL=http://127.0.0.1:8081
export DATABASE_URL="postgresql://$DATABASE_USER:$DATABASE_PASSWORD@$DATABASE_HOST:5432/gps_e2e"
export PYTHONPATH="$ROOT/gateway:$ROOT/processor:$ROOT/simulator"
NGINX_CONF=/tmp/e2e-nginx.conf
stop() { for f in /tmp/e2e-svc-*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; done; rm -f /tmp/e2e-svc-*.pid
         [ -f $NGINX_CONF ] && nginx -c $NGINX_CONF -s stop 2>/dev/null; sleep 2; }
run() { name=$1; shift; setsid nohup "$@" > "/tmp/e2e-$name.log" 2>&1 < /dev/null & echo $! > "/tmp/e2e-svc-$name.pid"; }
case "${1:-start}" in
  stop) stop; exit 0 ;;
  fresh)
    stop
    PGPASSWORD=$DATABASE_PASSWORD psql -h "$DATABASE_HOST" -U "$DATABASE_USER" -d postgres -qc "DROP DATABASE IF EXISTS gps_e2e" -c "CREATE DATABASE gps_e2e"
    redis-cli -u "$REDIS_URL" flushdb > /dev/null
    rm -rf /tmp/e2e-mail && mkdir -p /tmp/e2e-mail
    (cd "$ROOT/backend" && DB_RUNTIME_ROLE= python3 manage.py migrate --noinput > /tmp/e2e-migrate.log 2>&1 \
      && DB_RUNTIME_ROLE= python3 manage.py collectstatic --noinput > /dev/null && python3 manage.py seed_demo | tail -1) ;;
esac
sed "s#__ROOT__#$ROOT#g" "$ROOT/scripts/e2e/nginx.conf.template" > $NGINX_CONF
cd "$ROOT/backend"
DB_RUNTIME_ROLE=gps_app run backend uvicorn config.asgi:application --host 127.0.0.1 --port 8000
run worker celery -A config worker --loglevel=INFO --concurrency=2
run beat celery -A config beat --loglevel=INFO --schedule=/tmp/e2e-celerybeat-schedule
GATEWAY_STORAGE=postgres GATEWAY_HEALTH_PORT=8081 GATEWAY_RECONCILE_SECONDS=5 run gateway python3 -m gpsgw.main
PROCESSOR_HEALTH_PORT=8082 run processor python3 -m gpsproc.main
nginx -c $NGINX_CONF
for _ in $(seq 1 30); do curl -fsS http://localhost/readiness > /dev/null 2>&1 && break; sleep 1; done
curl -s http://localhost/readiness; echo
