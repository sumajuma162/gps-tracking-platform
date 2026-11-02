# Multi-Device GPS Tracking Platform

A multi-tenant fleet-tracking platform for Teltonika FMC920 and other GPS trackers. It has a protocol-plugin gateway, Django/PostGIS/TimescaleDB storage, live maps and reports.

> **Status: all twelve phases of `docs/MASTER_SPEC.md` are built.** What was verified, how, and what still needs
> real hardware or infrastructure (Docker image runs, the 5,000-tracker load target, physical FMC920 and GT06
> confirmation) is in `docs/PROGRESS.md` and the completion report `docs/COMPLETION_REPORT.md`.

## What it does

- **Trackers:** Teltonika FMC920 and family (Codec 8 / 8E over TCP and UDP, commands over Codec 12), GT06 (as a
  plugin), and any tracker that sends JSON over HTTP or delimited text, defined in the UI with a test-parse box.
  Records are stored **before** the tracker gets its ACK; duplicates, backlog uploads and GPS jumps are handled.
- **Onboarding without code:** an Add tracker wizard ending in a live connection test, CSV import, an inbox of
  unregistered trackers, a device catalog with an IO mapping editor (live preview, versions), listener ports and
  tracker formats managed in the UI.
- **Live map and dashboard** updating about a second after a tracker reports; route history with playback and
  CSV/GPX/KML export; share links.
- **Analysis:** trips, stops, idling, speeding and geofence entry/exit/dwell, with correct results when data arrives
  late; per-organization thresholds.
- **Alerts:** 20 rule types, deduplication and cooldowns, in-app and email notifications with preferences and quiet
  hours, tracker commands.
- **Reports and analytics:** 14 reports (CSV, Excel, PDF, scheduled by email) and trend charts from a TimescaleDB
  continuous aggregate.
- **Fleet operations:** vehicles, drivers (with a driver portal), SIM cards (expiry alerts), maintenance schedules
  and service history.
- **Integrations:** signed webhooks with retries and a delivery log, scoped API keys, OpenAPI documentation.
- **Multi-tenant and secure:** PostgreSQL Row-Level Security under a restricted database role, an isolation test over
  every API route, roles and permissions, audit log, rotating refresh tokens, lockout, rate limits.
- **Operations:** Prometheus metrics and alert rules, a Grafana dashboard, Sentry, backups with a tested restore
  drill, point-in-time recovery, a load-test mode, CI with dependency and image scanning.
- English and Kiswahili throughout.

## Quick start: Linux, macOS or WSL

```bash
git clone <repository> gps-tracking-platform && cd gps-tracking-platform
cp .env.example .env              # then set DJANGO_SECRET_KEY, DATABASE_PASSWORD, REDIS_PASSWORD
docker compose up --build -d
docker compose ps                 # all services should become "healthy"
curl http://localhost/readiness   # {"status": "ready", ...}
docker compose exec backend python manage.py seed_demo   # demo users (development only)
# a week of history, generated through the real gateway:
docker compose --profile sim run --rm simulator --scenario normal_day --backfill-hours 168
```

Open http://localhost and sign in as `admin@demo.local` with the `SEED_DEMO_PASSWORD` from `.env`. The other demo users are `superadmin@example.com`, `fleet@demo.local`, `monitor@demo.local`, `viewer@demo.local`, `auditor@demo.local`, `driver@demo.local` and `admin@coastal.local` (a second organization, for testing isolation).

For production, create the first administrator instead of seeding: `docker compose exec backend python manage.py create_superadmin you@company.co.tz`.

## Quick start: Windows 11 (PowerShell)

Requires Docker Desktop with the WSL2 backend, plus Git. Run `git config --global core.autocrlf input` before cloning, so shell scripts keep LF line endings.

```powershell
git clone <repository> gps-tracking-platform; cd gps-tracking-platform
Copy-Item .env.example .env       # then edit .env; generate secrets with: .\scripts\dev.ps1 secret
.\scripts\dev.ps1 up
.\scripts\dev.ps1 seed            # demo organizations and users
.\scripts\dev.ps1 tracker-test    # sends a Teltonika handshake to port 5027; expects "01 (accepted)"
```

## Run the tests

```bash
cd gateway  && pip install -r requirements-dev.txt && pip install --no-deps -e . && pytest
cd backend  && pip install -r requirements-dev.txt && PYTHONPATH=../gateway:../processor:../simulator pytest
cd frontend && npm ci && npm run lint && npm run typecheck && npm test && npm run build
# Browser tests against a running, seeded stack (27 tests):
pip install -r e2e/requirements.txt && python -m playwright install chromium
E2E_BASE_URL=http://localhost E2E_PASSWORD=<SEED_DEMO_PASSWORD> E2E_TRACKER_HOST=localhost pytest e2e -p no:django
# No Docker? scripts/e2e/native-stack.sh fresh runs the same topology natively first (see docs/TESTING.md).
```

Backend tests need PostgreSQL with PostGIS and TimescaleDB, and Redis (`DATABASE_HOST`, `DATABASE_PASSWORD`,
`REDIS_URL`); they include end-to-end runs of the real gateway and processor. `TEST_SQLITE=1` gives a quick run that
skips everything database-specific. Details: `docs/TESTING.md`.

API documentation: `http://localhost/api/docs/` (sign-in required when `DEBUG=0`).

## Repository layout

| Path | Contents |
| --- | --- |
| `gateway/` | Device gateway: protocol plugins (`gpsgw/protocols`), normalization and IO mapping, TCP/UDP listeners |
| `backend/` | Django API, Celery, Channels |
| `processor/` | Stream processor: live status, trips, stops, idling, speeding, geofences, alerts, webhook events |
| `simulator/` | Simulated trackers (Teltonika, GT06, HTTP), scenarios, backfill and load mode |
| `monitoring/` | Prometheus configuration and alert rules, Grafana dashboard |
| `scripts/` | PowerShell helpers, backup and restore drill |
| `frontend/` | React and TypeScript web app |
| `nginx/` | Edge proxy configs for dev and prod with TLS |
| `e2e/` | Playwright browser acceptance tests |
| `docs/` | Specification, progress, decisions and the full documentation set (start with `docs/INSTALLATION.md`) |

Set `TRACKER_SERVER_HOST` in `.env` to the public hostname your trackers connect to; it appears in every tracker's setup instructions.

## Connecting a real FMC920

Physical device setup is Part D of the master spec. In short:

1. Register the tracker's IMEI under **Trackers**, and install it in a vehicle.
2. Open **Setup** on the tracker for the exact server, port and APN.
3. Point the tracker at your server on port 5027 (TCP, Codec 8 or 8E).

The tracker's **Connection test** turns green stage by stage as it connects. Watch `docker compose logs -f gateway` while it connects. If the IMEI was mistyped, it appears under **Unregistered devices** (Super Admin). Full guide: `docs/TELTONIKA_FMC920.md`; GT06: `docs/GT06.md`.

To try the pipeline without hardware, run the simulator. It drives the three demo trackers with the real protocol:

```bash
docker compose --profile sim run --rm simulator --scenario normal_day --steps 60 --speedup 5
# or, from a checkout:  PYTHONPATH=simulator:gateway python -m gpssim --scenario overspeed --speedup 0
```

Open a demo tracker's **Connection test** to watch the stages turn green. For a day of route history to explore, run `--backfill-hours 24` with one tracker's `--imei`, then open **Route history**.
