# Development

## Layout

| Path | Contents |
| --- | --- |
| `gateway/` | asyncio gateway: `gpsgw/protocols/<name>/` plugins (Teltonika, GT06, generic), `normalization/`, `listeners/` (TCP, UDP, HTTP), `services/` (PostgreSQL, Redis, pipeline) |
| `processor/` | stream consumer: status, analysis engine (`analysis/engine.py`), alerts, per-organization settings, webhook outbox |
| `backend/` | Django project `config/`, apps in `apps/`, shared code in `common/` (tenancy, RLS, CRUD, metrics) |
| `frontend/` | React + TypeScript + Vite; `features/` per area, `i18n/` English and Kiswahili |
| `simulator/` | `gpssim` scenarios, backfill and load mode |
| `e2e/` | Playwright browser acceptance tests |
| `monitoring/`, `nginx/`, `scripts/`, `docker/` | operations |

## Run the stack

```bash
cp .env.example .env            # DEBUG=1 for development
docker compose up --build -d
docker compose exec backend python manage.py seed_demo
docker compose --profile sim run --rm simulator --scenario normal_day --steps 120 --speedup 5
```

For live reload of the backend and PostgreSQL/Redis on 127.0.0.1 (never a public interface):
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build`.

## Without Docker (what CI does)

PostgreSQL 16 with PostGIS and TimescaleDB, and Redis 7, on localhost.

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt -r gateway/requirements-dev.txt -r processor/requirements.txt
pip install --no-deps -e gateway -e simulator
export DJANGO_SETTINGS_MODULE=config.settings.test DATABASE_HOST=localhost DATABASE_PASSWORD=... \
       REDIS_URL=redis://localhost:6379/0 PYTHONPATH=$PWD/gateway:$PWD/processor:$PWD/simulator
(cd backend && pytest -q)          # 266 tests, including the real gateway/processor end-to-end tests
(cd gateway && pytest -q)          # 131
(cd processor && pytest -q)        # 54
(cd simulator && pytest -q)        # 19
(cd frontend && npm ci && npm run lint && npm run typecheck && npm test && npm run build)
```

## Conventions

- Python 3.12, `ruff` (line length 120), `mypy --strict` for the gateway. Type hints everywhere.
- Every organization-owned model subclasses `common.tenancy.TenantModel`, gets `enable_tenant_rls(...)` in its
  migration, and every new router route gets a factory in `apps/organizations/tests/test_tenant_isolation.py`
  (the test fails until it has one).
- Organization API views subclass `TenantViewSet` / `TenantCrudViewSet` and declare `required_permissions` per
  action; new permissions go in `apps/rbac/catalog.py` plus a sync migration.
- Changes to data are audited (`apps.audit.services.record`), never with secrets in metadata.
- No protocol parsing in Django: the gateway owns it (format validation and test parse call the gateway).
- UI strings exist in both `frontend/src/i18n/en.ts` and `sw.ts` (TypeScript enforces the same shape).
- Record decisions in `docs/DECISIONS.md` and progress in `docs/PROGRESS.md`.

## Useful commands

| Task | Command |
| --- | --- |
| Migrations | `docker compose exec backend python manage.py makemigrations <app>` |
| OpenAPI check | `python manage.py spectacular --validate --fail-on-warn --file /tmp/schema.yml` |
| Gateway logs | `docker compose logs -f gateway` (JSON lines) |
| Simulate one tracker | `python -m gpssim --imei 356307042441013 --scenario overspeed --speedup 10` |
| Load test | `python manage.py create_load_devices --count 2000`, then `make load-test DEVICES=2000` |
| Register load devices | refuses with `DEBUG=0` unless `--force` |
