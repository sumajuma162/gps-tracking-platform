# Testing

| Suite | Location | Count (2026-10-04) | What it covers |
| --- | --- | --- | --- |
| Backend | `backend/` (pytest) | 266 | API, permissions, RLS and route-by-route tenant isolation, audit, reports, alerts, settings, webhooks, API keys; end-to-end with the real gateway and processor (Teltonika, GT06, HTTP, text), Scenario 4 data integrity |
| Gateway | `gateway/tests` | 131 | Teltonika codecs (official samples), GT06 (official samples), generic formats, Hypothesis fuzzing of every decoder, listeners, limits, pipeline rules |
| Processor | `processor/tests` | 54 | status, trip/stop/idle/overspeed engine, alerts, per-organization settings, recovery after Redis loses the stream |
| Simulator | `simulator/tests` | 19 | scenarios against real listeners, GT06/HTTP clients, load IMEIs |
| Frontend | `frontend/src` (Vitest) | 79 | pages, forms, permissions in the UI, i18n |
| Browser | `e2e/` (Playwright, Chromium) | 27 | Scenario 1, Scenario 2 (steps 1-5, including the generic HTTP tracker), Scenario 3 (GT06 to the live map), driver portal, settings, API keys used from outside, maintenance, analytics, and each phase's flows, against the full stack |

Coverage gates in CI: backend ≥ 90% (measured 95.9%), gateway unit ≥ 85% (86.3%), gateway combined with the end-to-end
tests ≥ 90% (92%).

## Running

See `DEVELOPMENT.md` for setup. Backend tests need PostgreSQL with PostGIS and TimescaleDB and Redis;
`TEST_SQLITE=1` gives a quick run that skips the database-specific tests. End-to-end backend tests need
`PYTHONPATH=../gateway:../processor:../simulator`.

Browser tests against a running, seeded stack:

```bash
pip install -r e2e/requirements.txt && python -m playwright install chromium
E2E_BASE_URL=http://localhost E2E_PASSWORD=<SEED_DEMO_PASSWORD> E2E_TRACKER_HOST=localhost pytest e2e
```

### Without Docker

`scripts/e2e/native-stack.sh fresh` runs the same topology natively (edge Nginx, SPA, backend as `gps_app` under
RLS, Celery, gateway, processor) for machines without Docker; then run the suite with `-p no:django`. On
2026-10-04 the suite passed **27/27 three times in a row** this way (fresh database, then twice more on the same
data), in about 3 minutes 15 s per run.

## Acceptance scenarios (master spec A18)

| Scenario | Automated by |
| --- | --- |
| 1 Core tracking flow | `e2e/test_scenario1.py` |
| 2 Adding devices through the UI | `e2e/test_phase4_onboarding.py`, `e2e/test_phase2_fleet.py` (CSV import), `e2e/test_phase9b_to_12.py` and `backend/apps/devices/tests/test_phase10_protocols.py` (format, test parse, HTTP POST to the live map) |
| 3 New protocol as a plugin (GT06) | `gateway/tests/test_gt06.py`, `test_phase10_protocols.py`, `e2e/test_phase9b_to_12.py` |
| 4 Data integrity under failure | `backend/apps/tracking/tests/test_scenario4.py`, processor `test_recovery.py`, gateway listener tests |
| 5-7 (security, alerts, reports...) | see `docs/PROGRESS.md` per phase |

## Load testing

```bash
docker compose exec backend python manage.py create_load_devices --count 2000
PYTHONPATH=gateway:simulator python -m gpssim.load --devices 2000 --interval 15 --records-per-packet 3 \
  --duration 180 --warmup 50 --processor-health http://<processor>:8082/health
```

Results and how to read them: `SCALING.md`.
