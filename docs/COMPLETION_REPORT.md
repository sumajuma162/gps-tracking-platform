# Completion report

Multi-Device GPS Tracking Platform, against `MASTER_SPEC.md` (spec A18). Date: 2026-10-04.
Everything below was run in the build environment (Ubuntu 24.04, PostgreSQL 16.15 + TimescaleDB 2.17.2 + PostGIS
3.4.2, Redis 7, Python 3.12, Node 22) unless marked **not verified**. Physical FMC920 and GT06 confirmation is Part D
of the spec and is the operator's to do.

## 1. Implemented modules

| Area | Modules |
| --- | --- |
| Gateway | Protocol plugin system (entry points); Teltonika Codec 8/8E/12 over TCP and UDP; GT06 plugin; generic JSON-over-HTTP and delimited-text protocols from UI-defined formats; normalization with per-model IO mapping profiles; validation, deduplication, late and outlier flags; ACK after a one-statement atomic write; registry with pub/sub invalidation; listener ports reconciled from the database; per-IP and global connection limits; unregistered-device inbox; raw packet logging; command queue; metrics; internal test-parse endpoint |
| Processor | Live status; trip/stop/idle/overspeed engine with per-device checkpoints; geofence entry/exit/dwell with hysteresis; late-data recompute; database catch-up after stream loss; alert engine (rules, scope, schedules, dedup, cooldown, recipients, preferences, quiet hours, delayed alerts); per-organization thresholds; webhook outbox; metrics |
| Backend | Accounts (JWT with rotating refresh cookies, sessions, lockout, password reset); organizations and memberships; RBAC with custom roles; audit log with search; fleet (vehicles, groups, drivers, SIM cards); devices (wizard, CSV import, setup instructions, connection test, diagnostics, commands, HTTP tokens); device catalog (manufacturers, protocols, models, IO profiles with versions, listener ports, generic formats); tracking (live, dashboard, history and exports, map settings); analysis (trips, stops, geofences); alerts and notifications (in-app, email); reports (14, CSV/Excel/PDF, background jobs, schedules); share links; global search; system health; settings (organization and platform); integrations (webhooks, API keys, data forwarders — Prepared); maintenance; analytics; driver portal; metrics; Sentry |
| Web app | All of the above in English and Kiswahili (React + TypeScript) |
| Simulator | Teltonika, GT06 and HTTP clients; 11 scenarios; backfill; load mode |
| Operations | Docker Compose (base, dev, prod hardening, backup), Nginx with TLS and security headers, Prometheus rules, Grafana dashboard, backup and restore drill scripts, pgBackRest, CI with scanning |

## 2. Files

662 files outside `node_modules` and build output: gateway 152, backend 307, frontend sources 90, processor 26,
simulator 17, browser tests 12, docs 23 (plus this report), monitoring 7, scripts 3, nginx 4, docker 1, and root
files (Compose files, `.env.example`, Makefile, CI workflow, README).

## 3. Database tables

72 tables in `public` (PostGIS's `spatial_ref_sys` and Django's own included), notably: `positions` and
`raw_packets` (TimescaleDB hypertables), `positions_hourly` (continuous aggregate), `device_state`,
`organizations`, `organization_users`, `users`, `user_sessions`, `roles`, `permissions`, `role_permissions`,
`vehicles`, `vehicle_groups`, `drivers`, `driver_assignments`, `sim_cards`, `devices`, `device_assignments`,
`device_connections`, `device_manufacturers`, `device_protocols`, `device_models`, `io_mapping_profiles`,
`io_mapping_profile_versions`, `listener_ports`, `generic_formats`, `unregistered_device_attempts`, `trips`, `stops`,
`idle_events`, `speed_events`, `driving_behavior_events`, `geofences`, `geofence_events`, `processing_checkpoints`,
`recompute_requests`, `alert_rules` (+ vehicles, groups, recipients), `alerts`, `notifications`,
`notification_preferences`, `notification_channels`, `commands`, `command_templates`, `command_responses`,
`report_jobs`, `scheduled_reports`, `share_links`, `audit_logs`, `break_glass_grants`, `organization_settings`,
`platform_settings`, `webhooks`, `webhook_events`, `webhook_deliveries`, `api_keys`, `data_forwarders`,
`maintenance_schedules`, `service_records`. Details: `DATABASE.md`.

## 4. API endpoints

224 operations under `/api/v1/` in the OpenAPI schema (validated with no warnings). Interactive documentation at
`/api/docs/`. Summary by area: `API.md`.

## 5. WebSocket endpoints

`/ws/tracking/` (live positions per organization), `/ws/notifications/` (per user), `/ws/devices/<id>/diagnostics/`
(connection test and live diagnostics). Authenticated with single-use 30-second tickets.

## 6. Tracker ports

| Port | Protocol |
| --- | --- |
| 5027 TCP and UDP | Teltonika (Codec 8, 8E; Codec 12 commands) |
| 5023 TCP | GT06 |
| 5055 HTTP | generic JSON formats (`/<format>`) |
| 5100-5109 TCP/UDP | reserved for listener ports assigned in the UI (e.g. delimited-text formats) |

## 7. Supported protocols and device models

Protocols: `teltonika`, `gt06`, `generic_json`, `generic_text`. Seeded models: Teltonika FMC920 (verified IO profile
for the common elements), GT06 (generic), Generic HTTP JSON; more are added in the catalog by cloning.

## 8. User roles

Super Administrator (platform), Organization Administrator, Fleet Manager, Monitoring Officer, Viewer, Auditor,
Driver (driver portal), plus custom roles per organization.

## 9. Test results (2026-10-04)

| Suite | Result | Coverage |
| --- | --- | --- |
| Backend (pytest, real PostgreSQL/TimescaleDB/PostGIS and Redis; includes the real gateway and processor end to end) | 266 passed | 95.9% (CI floor 90%) |
| Gateway | 131 passed; mypy strict and ruff clean | 86.3% unit (floor 85%); 92% combined with the end-to-end tests (floor 90%) |
| Processor | 54 passed | — |
| Simulator | 19 passed | — |
| Frontend (Vitest) | 79 passed; ESLint, TypeScript, production build clean | — |
| OpenAPI | 224 operations, 0 warnings | — |
| Browser (Playwright + Chromium, 27 tests in `e2e/`) | **27/27, three consecutive runs**, against the full stack run natively with the same topology as Compose (`scripts/e2e/native-stack.sh`; Docker itself isn't available here) | — |

Acceptance scenarios:

| Scenario | Status |
| --- | --- |
| 1 Core tracking | `e2e/test_scenario1.py` passed in the browser (all 8 steps) |
| 2 Adding devices | All 6 steps passed in the browser (`test_phase4_onboarding.py`, `test_phase2_fleet.py`, `test_phase9b_to_12.py`: format test-parsed and token issued in the UI, plain HTTP POST, vehicle on the live map) |
| 3 GT06 plugin | `test_gt06.py`, `test_phase10_protocols.py` and the browser test passed (login, location, heartbeat through positions, live map, trip, alert and report); change set limited to the plugin, its entry point, seed data and tests (no git history to show a diff) |
| 4 Data integrity | `test_scenario4.py` passed: database outage, coverage backlog (records hours late, live marker never moves back, a late record flagged and recomputed), bad data, malformed packets, stream loss |

## 10. Load test against the A15 targets

| Target | Measured |
| --- | --- |
| 5,000 trackers, 1,000 packets/s, live updates < 2 s, on 4 vCPU / 8 GB | **Not verified** (that hardware wasn't available) |
| Build sandbox, **1 vCPU / 4 GB shared by every service and the load generator**: 2,000 trackers, 3 records per packet every 15 s | 2,000 connected, 0 errors, 400 records/s (133 packets/s) sustained; ACK p50 37 ms, p95 754 ms; live-update lag median 21 ms, max 1.6 s |
| Same sandbox, 900 records/s offered | saturated at about 400 records/s (CPU-bound) |

Method and how to repeat it on production-sized hardware: `SCALING.md`.

## 11. Docker services

`postgres`, `redis`, `backend`, `celery-worker`, `celery-beat`, `gateway`, `processor`, `frontend`, `nginx`; optional
profiles `sim` (`simulator`) and `monitoring` (`prometheus`, `grafana`). All Compose files validate against the
Compose Specification schema. **Images were not built or started here** (no Docker); CI builds, scans and starts
them.

## 12. Default development users

Created only by `manage.py seed_demo` (refuses when `DEBUG=0`); passwords from `SEED_DEMO_PASSWORD` or printed once:
`superadmin@example.com`, `admin@demo.local`, `fleet@demo.local`, `monitor@demo.local`, `viewer@demo.local`,
`auditor@demo.local`, `driver@demo.local` (driver portal, T 123 ABC), `admin@coastal.local` (second organization).

## 13. How to start

`INSTALLATION.md` (README has the bash and PowerShell quick start). In short: copy `.env.example` to `.env`, set the
secrets, `docker compose up --build -d`, `docker compose exec backend python manage.py seed_demo`, open
`http://localhost`, run the simulator.

## 14. Connecting an FMC920

`TELTONIKA_FMC920.md`: register the IMEI, point the tracker at your host on 5027 TCP (Codec 8 or 8E) with the
carrier's APN, watch the connection test. Parameter IDs and SMS syntax are marked "verify against Teltonika's
documentation for your firmware".

## 15. Adding a device and a device model through the UI

`ADDING_DEVICES_VIA_UI.md` (5 cases: known model, unregistered inbox, new model by cloning with the IO mapping
editor, generic JSON/text format with test parse, new binary protocol).

## 16. Adding a protocol plugin

`DEVICE_INTEGRATION.md`; the GT06 plugin is the worked example.

## 17. Known limitations

- Not verified in the build environment: Docker image builds and runs (the stack was verified natively with the
  same topology instead), Trivy, Grafana and Prometheus containers, pgBackRest point-in-time recovery, Sentry
  delivery, the A15 load target on 4 vCPU / 8 GB.
- When a browser closes a WebSocket, Channels' Redis layer sometimes logs `redis.exceptions.TimeoutError` while
  its pending read is cancelled (about 9 times per 27-test browser run). Teardown noise from channels_redis 4.3 with
  redis-py 8; no request or update is affected.
- No physical tracker was available: FMC920 support is built from Teltonika's official protocol documentation and
  sample packets; GT06 from the published protocol document. Confirm both with real units (Part D).
- Role permissions apply organization-wide; there's no per-vehicle restriction of what a role sees (rules and reports
  can be scoped).
- Engine hours for maintenance are ignition-on time during trips (an approximation).
- Data forwarders are Prepared only (no delivery loop, no regulator format).
- SMS, WhatsApp and push notification channels are defined but only in-app and email deliver.
- 4 moderate npm advisories accepted until a React Router 7 / Vitest 5 upgrade (`SECURITY.md`).
- Scenario 3's "code diff" can't be shown from this delivery (no git history); the change set is listed in
  `PROGRESS.md`.

## 18. Security notes

Row-Level Security under a restricted database role plus an isolation test over every API route; Argon2 passwords,
lockout and rate limits; 10-minute access tokens in memory, rotating 7-day refresh cookies with reuse detection;
single-use WebSocket tickets; hashed API keys and device tokens; signed webhooks with SSRF protection; ACK only
after persistence; only 80/443 and tracker ports published; non-root, read-only, no-new-privileges containers in
production; audit log; dependency and image scanning in CI. Details: `SECURITY.md`.

## 19. Production checklist

- [ ] `DEBUG=0`, strong `DJANGO_SECRET_KEY`, database and Redis passwords; `.env` mode 600, not in git
- [ ] `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `FRONTEND_URL`, `TRACKER_SERVER_HOST` set
- [ ] TLS certificates installed and renewing; HTTPS redirect works
- [ ] Firewall: only 22 (restricted), 80, 443 and the tracker ports in use
- [ ] A licensed map tile provider in `MAP_TILE_URL`; email settings tested
- [ ] `manage.py check --deploy` clean; `create_superadmin` run; no demo data
- [ ] Monitoring up (`METRICS_TOKEN`, Grafana password) and alerts routed to someone
- [ ] Nightly dumps scheduled and copied off-site; pgBackRest stanza created; **restore drill passed**
- [ ] Load test on the production-sized server recorded in `SCALING.md`
- [ ] One real FMC920 (and GT06, if used) confirmed end to end (Part D)
- [ ] Retention (`retention_days`) agreed with the organizations (personal data)
