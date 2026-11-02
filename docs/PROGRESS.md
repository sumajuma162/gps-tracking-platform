# Progress

Last updated: 2026-10-04 (all phases complete; see `docs/COMPLETION_REPORT.md`).

## COMPLETED

### Phase 0: inspect and scaffold

Done in code and statically validated. **The `docker compose up --build` run itself is still unverified** (no Docker in the build sandbox; CI job `docker` performs it).

- Repository skeleton, `.env.example`, `.gitignore`, `.gitattributes` (LF line endings, CRLF for `.ps1`).
- `docker-compose.yml` (9 services), plus `docker-compose.dev.yml` and `docker-compose.prod.yml`.
- Dockerfiles for backend, gateway, processor and frontend: non-root, with healthchecks.
- Nginx edge configs for dev and prod (TLS), with security headers and WebSocket proxying.
- Django backend: split settings (base, dev, prod, test), Celery app, `/health` and `/readiness` (database, migrations, Redis).
- Processor service scaffold: health endpoint and Redis probe.
- Frontend status shell: React, TypeScript strict, Vite.
- Makefile, `scripts/dev.ps1` for Windows, and the GitHub Actions CI workflow.

### Phase 3, protocol core only (early, see DECISIONS.md)

- Teltonika Codec 8, Codec 8E, Codec 12 (commands and responses), TCP framing with partial reads, UDP framing, CRC-16/IBM, IMEI handshake, `0xFF` keepalive, and clean rejection of Codec 16 and other unknown codecs.
- `BaseProtocolDecoder` contract, `ProtocolRegistry` and entry-point plugin loading.
- `NormalizedLocation`, validators (coordinates, time sanity, GPS fix validity) and record hashing for deduplication.
- Data-driven IO mapping profiles with a seeded FMC920 profile (marked unverified until checked on a real device).
- TCP and UDP listeners enforcing spec A4:
  - ACK only after persistence.
  - Duplicate resends are ACKed but not stored twice.
  - Unknown IMEIs are refused and recorded in the inbox.
  - Per-IP connection limit, plus handshake and idle timeouts.
  - Malformed packets close only their own connection.
  - Disabled devices are disconnected.
- Packet encoder for the simulator and round-trip tests.

### Phase 1: Foundation (complete)

**Backend**

- **Accounts:** custom email-login `User` and `UserSession`.
- **Tokens:** a JWT access token held in memory only (10 minutes). The refresh token lives in an httpOnly, SameSite=Strict cookie scoped to `/api/v1/auth/`, rotates on every use, and **revokes the whole session if a rotated-out token is reused**.
- **CSRF** protection on refresh and logout.
- **Lockout** after 5 failures in 15 minutes. Login, refresh and password endpoints are throttled.
- **Account flows:** password change (ends other sessions), forgot and reset with single-use links, a session list with per-device and "all other devices" sign-out, profile and language, and one-time WebSocket tickets for Phase 5.
- **Organizations and memberships:** multi-organization users with explicit `X-Organization-Id` selection, soft deactivation, and an invitation email that uses the reset link.
- **RBAC:** a permission catalog of 34 permissions and 6 system roles (A8 table), custom per-organization roles, deny-by-default `required_permissions` on every action, and a last-administrator guard.
- **Tenancy:**
  - `TenantModel` with a scoped default manager that raises outside a tenant scope.
  - `TenantViewSet`: outside the tenant, objects return 404, never 403.
  - **PostgreSQL Row-Level Security.** Web requests run as the restricted role `gps_app` (non-superuser, NOBYPASSRLS), and the tenant is set transaction-locally per request (`ATOMIC_REQUESTS`).
- **Break-glass:** a reasoned, 2-hour, read-only Super Administrator access grant, recorded in the organization's own audit log.
- **Audit log:**
  - Append-only in the model, **and at the database level**: `gps_app` has only INSERT and SELECT.
  - Secrets are redacted.
  - Auth events are recorded once per organization the user belongs to.
  - Separate organization and platform views.
- **API plumbing:** a safe error envelope with a request ID, an OpenAPI schema (37 operations, zero warnings) with Swagger at `/api/docs/`, `seed_demo` (refuses to run when `DEBUG=0`) and `create_superadmin`.

**Frontend** (React, TypeScript strict, Tailwind 4, React Router, TanStack Query, Zustand, react-i18next, react-hook-form with zod)

- **Public pages:** the landing page (no API calls, no real coordinates), sign in, forgot password, and reset/invitation password.
- **App shell:** a collapsible, role-filtered sidebar, an organization switcher (clears cached data on switch), an English/Kiswahili switch (saved to the profile), and sign out.
- **Pages:**
  - Overview
  - Users: add, change role with confirmation, remove and restore access; card layout on phones
  - Roles
  - Audit log
  - Organizations: create, deactivate, and read-only support access
  - Profile: details, password, and signed-in devices
- **Session handling:** the session is restored from the cookie on reload, with one shared refresh for concurrent 401s. Return paths after sign-in are restricted to the same site (no open redirects).
- **Quality:** ESLint (blocks `dangerouslySetInnerHTML`), loading skeletons, empty and error states with retry, toasts, and accessible dialogs.

**Deployment and CI**

- The backend runs with `DB_RUNTIME_ROLE=gps_app`. Migrations and Celery use the owner role.
- New environment variables are documented in `.env.example`.
- The CI `docker` job now seeds demo data and runs the Playwright browser suite against the real stack.

### Phase 9, part A: Reports, exports, scheduled reports, Scenario 1 (complete)

Phase 9's scope (`docs/PHASE_PLAN.md`) is the largest in the plan. It is split into two parts: **A** delivers the done criteria; **B** delivers the remaining modules.

**Done when** "report and export tests pass; Scenario 1 passes end to end": **both are met**.

**Reports** (`apps/reports`, spec A13): a registry of **13** reports.

- **The reports:** daily movement, trips, distance, speed and overspeed, stops, idling, device offline, geofence events, alerts, drivers with a behaviour score, fleet summary, vehicle utilization, device health. The maintenance report arrives with the maintenance module in part B.
- **Filters:** vehicle, group, driver, period and status, per report.
- **On screen:** paginated, with a bar chart where it helps.
- **Exports:** CSV (UTF-8 with BOM, for Excel), Excel (typed cells, streamed) and PDF (landscape, repeating header, organization, period and generation time).
- **Large exports:** above **10,000 rows or 31 days** they run as Celery `report_jobs`, with progress, an owner-only download, a **24-hour link** (410 after expiry) and an hourly purge of expired files.
- **Audit:** every export is audited (`reports.exported`).
- **Scheduled reports:** daily (yesterday), weekly (last 7 days) or monthly (last calendar month), run by Celery beat and emailed as an attachment to chosen recipients, with errors recorded on the schedule and audited.
- **Behaviour score:** 100 minus penalty points per 100 km (overspeed 5, harsh acceleration/braking/cornering 3, crash 20), using at least 1 for the 100 km divisor.
- **Background scope:** jobs and schedules run inside the organization's scope with RLS set (`org_scope`), not with a bypass.

**Web app:**

- **Reports page:** report types, per-report filters, table and chart, CSV/Excel/PDF export (direct, or a background job with a progress bar), and a scheduled reports tab.
- **Dashboard:** alerts today, open count and the latest open alerts.

**Scenario 1, end to end** (`e2e/test_scenario1.py`, on the live stack, about 25 s):

1. The Super Admin creates an organization with its first admin. The admin sets a password **from the real invitation email**: the test stack uses Django's file email backend, and CI bind-mounts that folder.
2. The admin draws a geofence, creates two alert rules, adds a vehicle, and adds the FMC920 through the wizard.
3. The simulator drives 2 hours (overspeed scenario) through the public port; all 720 records are ACKed.
4. Every connection-test stage is done, and the live marker moves over WebSocket without a reload.
5. Route history plays at every speed.
6. Trips and stops are detected; the overspeed and geofence alerts are on the dashboard, and one is acknowledged.
7. The trip report is exported to CSV, Excel and PDF, and the exports are in the audit log.
8. The second organization sees no vehicle, trips or alerts, and the direct history link answers "Vehicle not found".

**Verification (2026-10-03):**

| Check | Result |
| --- | --- |
| Report and export tests | 8 PostgreSQL tests: all 13 reports run and **never show another organization's data**; values and formatting; behaviour score; filters; validation; valid CSV, Excel and PDF; audit; background job with progress, owner-only and expiring download, purge; over 31 days goes to a job; scheduled report emailed with an attachment; permissions |
| Browser | **22/22**, including Scenario 1 |
| Backend | 209 passed, 95% coverage; isolation test covers 35 routes; OpenAPI 179 operations, 0 warnings |
| Frontend | 64 Vitest tests, ESLint and TypeScript clean |
| Gateway, processor, simulator | 92 (mypy strict), 50, 17 |

**Bugs found and fixed:**

1. **New alert rules could be ignored for up to 30 s.** The web app's change event cleared the processor's geofence cache but not its **rule cache**; the full browser suite exposed it. Both are cleared now, with a unit test.
2. **`THROTTLE_REFRESH` wasn't passed through Compose**, so the setting had no effect in containers. Separately, the browser suite exceeded the 30/min refresh limit, because every page load restores the session. The production default stays at 30; the test stack raises it.
3. **Test-data robustness:** SIM numbers now have 8 random digits; list searches wait for dialogs to close (saving resets the list); the Phase 8 rule is scoped to its own vehicle and deleted afterwards; 9 stale rules were removed from the dev database.
4. **Playback clock precision:** the visible clock shows minutes, so slow speeds looked frozen to the test. The exact time is now exposed as `data-time`.

### Phase 12: Documentation and final verification (complete)

**Done:**

- The full documentation set (spec A17): `SYSTEM_ARCHITECTURE`, `INSTALLATION`, `DEVELOPMENT`, `WINDOWS_DEVELOPMENT`,
  `PRODUCTION_DEPLOYMENT`, `DEVICE_INTEGRATION`, `ADDING_DEVICES_VIA_UI`, `TELTONIKA_FMC920`, `GT06`, `API`, `DATABASE`,
  `SECURITY`, `TESTING`, `MONITORING`, `TROUBLESHOOTING`, `USER_GUIDE`, `ADMIN_GUIDE`, `BACKUP_RESTORE`, `SCALING`, with the
  11 required Mermaid diagrams; README rewritten; `COMPLETION_REPORT.md`.
- Gaps found while documenting, now built and tested:
  - **Driver portal** (spec A8 "own assigned vehicle and own trips only"): the `own_vehicle.view` permission existed
    but nothing used it, so the Driver role saw nothing. `GET /api/v1/my-vehicle/` and a **My vehicle** page (drivers
    land on it); drivers can be linked to a login in the Drivers form (members of the organization only); the demo
    driver is linked to T 123 ABC.
  - **Dashboard:** trips and distance today (spec A13), in the organization's time zone.
- PowerShell equivalents for every `make` target (`lint`, `backup`, `monitoring` added).

**Final verification (2026-10-04):** backend 266 passed, 95.9% coverage; gateway 131 passed, 86.3% unit and 92%
combined coverage, mypy strict clean; processor 54; simulator 19; frontend 79 with lint, types and build clean;
OpenAPI 224 operations with no warnings; `check` and `makemigrations --check` clean.

**Browser acceptance, run natively (2026-10-04):** Chromium turned out to be available, so the whole stack was
started without Docker in the same topology as Compose (`scripts/e2e/native-stack.sh`: edge Nginx with the repo's
security headers, the built SPA, the backend as `gps_app` under RLS, Celery worker and beat, gateway, processor).
**27/27 browser tests passed three runs in a row** (fresh database, then twice on the same data). Added
`e2e/test_phase9b_to_12.py` (4 tests): driver portal; settings saved and reloaded; an API key created in the UI and
used from outside (allowed scope 200, other scope 403); a maintenance schedule; analytics charts; a generic HTTP
tracker whose format is test-parsed and token issued in the UI, then a plain HTTP POST puts it on the live map
(Scenario 2 step 4); a GT06 logging in on 5023 and appearing on the live map (Scenario 3). Fixed on the way:

- `e2e/test_phase2_fleet.py` expected a hostname CI never sets (`gps.demo-logistics.co.tz`); it now reads
  `E2E_SERVER_HOST` (default: `.env.example`'s value).
- The diagnostics WebSocket logged "Task exception was never retrieved" when a browser closed it mid-send; the
  watcher now ends quietly on disconnects and still raises real errors (unit-tested).
- Known log noise: `redis.exceptions.TimeoutError` from channels_redis when a socket's pending read is cancelled at
  disconnect; no functional effect.

**Not verified here:** Docker image builds and runs, Trivy, the monitoring containers, point-in-time recovery,
Sentry delivery, the A15 load target, and physical trackers (see `COMPLETION_REPORT.md`).

### Phase 11: Production hardening (complete except what needs Docker or real infrastructure)

**Defects found and fixed while hardening:**

1. **Report downloads would fail under Docker Compose.** The Celery worker wrote export files into its own container; the backend serves them from the `media` volume. The worker now mounts the same volume.
2. **No global connection limit** (spec A4.11 requires per-IP *and* global caps). `GATEWAY_MAX_CONNECTIONS_PER_IP` (default 50) and `GATEWAY_MAX_CONNECTIONS` (default 10,000, across all listeners) are enforced and counted (`gpsgw_protocol_errors_total{code="connection_limit"}`).
3. **Positions could be skipped after Redis lost the stream** (spec A3: "if Redis is lost, it resumes from the database"). When a device's next streamed record is more than `CATCHUP_GAP_SECONDS` (90) newer than the last analysed one, the processor first reads the stored positions in between from the database. Results from them are stored; alerts from them are marked delayed.
4. **Six database round trips per packet.** The gateway now writes positions, live state and last-seen time in one atomic statement (data-modifying CTEs), and refreshes `devices.last_packet_at` at most every 30 s per device. On the load test below this cut ACK p50 from 6.75 s to 98 ms at the same load.
5. **Deadlocks when many trackers start a new TimescaleDB chunk at once.** Seen in the Scenario 4 test (PostgreSQL log: `deadlock detected` on chunk creation). The save is retried up to 3 times on deadlock or serialization failure before giving up (the tracker would resend in any case).
6. **`celery[redis]==5.6.3` couldn't be installed with `redis==8.1.0`** (resolver conflict); the extra is dropped.
7. **PyJWT 2.10.1 had 15 published advisories** (pip-audit). Upgraded to 2.15.0; the auth, WebSocket and share-link suites pass.
8. **`safeNext` hardened** against the React Router open-redirect advisory's bypass patterns (tabs, newlines, backslashes, schemes), with tests.

**Built:**

- **Metrics:** backend `/metrics` (requests and latency per route, WebSocket clients, trackers by status, queue sizes, positions stream length; bearer token `METRICS_TOKEN`; not routed by Nginx); processor `/metrics` on its health port; gateway metrics as before.
- **Prometheus and Grafana** (`monitoring/`, `docker compose --profile monitoring up -d`): 9 alert rules (service down, positions not stored, no ingestion, ingest lag, processor lag and errors, queue backlog, API 5xx rate, protocol errors), a provisioned 14-panel dashboard, Grafana bound to 127.0.0.1.
- **Sentry** in backend, gateway and processor when `SENTRY_DSN` is set (`send_default_pii=False`).
- **Production Compose:** `no-new-privileges`, read-only root filesystems with tmpfs for backend, workers, gateway and processor, resource limits, rotated logs.
- **Backups:** `scripts/backup/pg_backup.sh` (nightly `pg_dump -Fc` + SHA-256, 7 daily / 4 weekly / 6 monthly, optional rclone copy off-site) and `scripts/backup/restore_drill.sh`; pgBackRest WAL archiving for point-in-time recovery (`docker-compose.backup.yml`, `docker/pgbackrest/pgbackrest.conf`).
- **Load mode:** `python -m gpssim.load` (thousands of concurrent Teltonika trackers over TCP, batching, warm-up, ACK latency percentiles, processor lag) and `manage.py create_load_devices`.
- **CI:** Redis service for the gateway job; `security` job (pip-audit on all Python requirements, `npm audit --audit-level=high`); `monitoring-config` job (promtool); Trivy scan of the built images in the Docker job (fails on fixable HIGH/CRITICAL); combined gateway coverage now includes the Phase 10 and Scenario 4 end-to-end tests.
- **Seed data completed** (spec A17): a GT06 and a generic HTTP tracker in Coastal Transport, the depot circle and port polygon, and overspeed and geofence rules. Idempotent; tested.

**Verification (2026-10-04):**

| Check | Result |
| --- | --- |
| Scenario 4 (`apps/tracking/tests/test_scenario4.py`, real gateway + PostgreSQL + Redis + processor + simulator) | 3 passed: database outage (no ACK, resend, 7 rows, no duplicates); coverage-gap backlog, GPS jump, invalid fix, malformed packet and a deliberately late record (live marker at the newest record, 1 late record, recompute ran, routes exclude outliers and invalid fixes, the 50 km jump adds no distance, other trackers unaffected); stream loss (both trips rebuilt from the database) |
| Backend | 258 passed at the end of Phase 11 (262 after Phase 12), 95% coverage; `manage.py check`, `makemigrations --check` clean |
| Gateway | 131 passed, 86.3% unit coverage (floor 85); mypy strict, ruff clean |
| Processor / simulator | 54 / 19 passed |
| Frontend | 78 Vitest tests; ESLint, TypeScript clean |
| Backup and restore drill | Real run on a database with 24 h of simulated history put through the gateway (8,640 positions): checksum ok, all 12 checked tables identical (positions, trips, stops, devices...), both hypertables restored |
| Prometheus config | `promtool check config` and `check rules`: valid, 9 rules |
| Compose files | base, dev, prod and backup files valid against the Compose Specification JSON schema |
| pip-audit | no known vulnerabilities in backend, gateway or processor requirements (after the PyJWT upgrade) |
| npm audit | 0 high or critical; 4 moderate (React Router 6 and Vitest 4, fixed only in new major versions; assessed in `docs/SECURITY.md`) |

**Load test** (sandbox: **1 vCPU and 4 GB RAM shared** by PostgreSQL, Redis, gateway, processor and the load generator; steady state after a 50 s warm-up):

| Run | Result |
| --- | --- |
| 2,000 trackers, 3 records per packet every 15 s (400 records/s, 133 packets/s) | 2,000 connected, 0 errors, 400 records/s sustained; ACK p50 37 ms, p95 754 ms, p99 1.76 s; processor live-update lag median 21 ms, max 1.6 s |
| 3,000 trackers, 900 records/s offered | saturated at about 400 records/s (the single core is the limit) |

**Not verified here:** the A15 target (5,000 devices, 1,000 packets/s on 4 vCPU / 8 GB) needs that hardware; `docker compose up` of the hardened production stack, Trivy and the Grafana/Prometheus containers (no Docker); pgBackRest archiving and point-in-time restore (no object storage); Sentry delivery (no DSN).

### Phase 9, part B: complete (2026-10-03)

**Done in this part** (spec A13 integrations, other modules and analytics; A11 maintenance and SIM alerts):

- **Share links, global search, system health page** (earlier in part B; unchanged).
- **Webhooks** (`apps/integrations`): per organization, for `alert.created`, `trip.completed`, `geofence.event` and `device.offline`.
  - Signed with HMAC-SHA256 (`X-GPS-Signature: t=<unix>,v1=<hex of HMAC("<t>.<body>")>`); the secret is shown once.
  - The processor writes events into a transactional outbox (`webhook_events`) in the same transaction as the result, with a stable key, so a recompute or a stream redelivery never sends an event twice. Celery fans them out every 5 s and retries failures (1 min, 5 min, 30 min, 2 h, 6 h; 4xx except 408/429 are final). Every attempt is logged with status, body excerpt, error and duration; a webhook switches itself off after 50 failures in a row.
  - SSRF protection: https only, the host must resolve to public addresses (no loopback, private, link-local or metadata addresses), redirects are not followed. Development flags `WEBHOOK_ALLOW_HTTP` / `WEBHOOK_ALLOW_PRIVATE`.
  - "Send test" posts a signed `webhook.test` event once.
- **API keys**: `Authorization: Api-Key <key>` or `X-Api-Key`. Only a SHA-256 is stored; scopes must be a subset of the creator's permissions, and at request time are narrowed again to what the creator still holds. Keys work only on organization routes (never account, platform or WebSocket-ticket routes, never "any member" personal routes), only in their own organization; expiry, revocation and last use are recorded; creation and revocation are audited.
- **Maintenance** (`apps/maintenance`): schedules by distance, engine hours or days (whichever first) with due-soon margins; service records with cost and vendor. Usage since the last service comes from detected trips. Hourly Celery check raises `maintenance_due` alerts when a schedule becomes due soon or overdue (once per state change); recording a service resets the schedule and resolves the alert. A 14th report: **maintenance**.
- **SIM expiry alerts**: `params.days` before a SIM's expiry date (default 14), once per expiry date; renewing re-arms.
- **Settings** (`apps/settings_app`): organization settings (timezone, language, units, every A10 threshold, map centre and zoom, retention, email severity) over platform defaults (Super Admin, plus the map tile provider and geocoder). Validated (bounds, cross-field rules), audited with old and new values, and pushed to the processor, which now applies per-organization thresholds to status, trips, stops, idling, overspeed, geofence hysteresis, long-stop rules and email defaults. A daily task deletes each organization's positions beyond its retention.
- **Analytics** (`/api/v1/analytics/`, 9 charts): ingestion volume, late records and tracker uptime come from the new TimescaleDB continuous aggregate `positions_hourly` (real-time aggregation; refreshed every 30 min over the last 3 days; read through the tenant-filtering view `tenant_positions_hourly`). Distance, trips, utilization, alerts, overspeed and idling come from the processor's results; status distribution from `device_state`. Never raw positions.
- **Audit log search**: free text over action, resource, user and metadata; resource type and id; IP; date range; action and resource suggestions. User agents are now recorded on CRUD entries.
- **Web app**: Settings, Integrations (webhooks with delivery log, API keys), Maintenance, Analytics pages; audit search; all strings in English and Kiswahili.

**Verification (2026-10-03, PostgreSQL 16.15 + TimescaleDB 2.17.2 + PostGIS 3.4.2, Redis 7):**

| Check | Result |
| --- | --- |
| Backend | 245 passed at the end of part B (252 with Phase 10), 95% coverage; isolation test covers every router route including webhooks, API keys, maintenance and analytics |
| Processor against PostgreSQL | per-organization overspeed thresholds change detection; webhook events written once, not on redelivery |
| Webhooks | real local HTTP receiver: signature verified, 503 retried then delivered, 410 final, SSRF targets refused |
| OpenAPI | 215 operations, 0 warnings (`spectacular --validate --fail-on-warn`) |
| Processor | 53 passed |
| Frontend | 75 Vitest tests (78 with Phase 10); ESLint, TypeScript and production build clean |

### Phase 10: Extensibility (complete except browser runs)

**Done** (spec A6 GT06, A7 generic configurable protocol, A13 data forwarders):

- **GT06 plugin** (`gateway/gpsgw/protocols/gt06/`), registered through the `gps_platform.protocols` entry point:
  - CRC-ITU (X-25) reproduces the CRCs printed in the GT06 document for its login, login reply and location examples (fixtures in `gateway/tests/fixtures/gt06_official.py`).
  - Login 0x01 (BCD IMEI; ACK only for registered devices), location 0x12/0x22 (hemisphere and GPS-valid bits, ACC, re-upload flag, mileage), heartbeat 0x13 and alarm 0x16/0x26 (ACKed after storage). Heartbeats travel the normal data path with zero records, so the listener, pipeline and normalization are unchanged.
  - The model's IO profile (`gt06-generic`, seeded) maps the plugin's IO ids to ignition, GSM, battery level, odometer and alarm events; `time_offset_minutes` corrects clones that send local time.
- **Generic configurable protocol**:
  - Gateway: `GenericFormat` (pure data: field paths or column numbers, time formats iso8601/unix/unix_ms/strftime, speed units, direct normalized fields and custom sensors, separator, terminator, auth, ACK). `generic_text` decoder for delimited text over TCP and UDP (one format per listener port; editing the format restarts that listener). New HTTP listener for `generic_json`: `POST /<format slug>` (JSON object or list), or GET/form parameters; per-device token or registered-identifier auth; size limit, per-IP rate limit, unknown identifiers to the inbox, the format's ACK only after storage, 503 when storage fails.
  - Test parse runs **in the gateway** (`POST /test-parse` on its internal health port); Django proxies to it and never parses tracker data.
  - Web app: Tracker formats page (Super Admin) with definition editor and test-parse box; format choice on device models and on text listener ports; "HTTP token" on HTTP trackers (shown once, only a SHA-256 stored, rotating invalidates the gateway cache at once).
  - Seeds: `fleet-json` (JSON, token auth) on the generic HTTP model; `csv-basic` (delimited example); `generic_text` protocol; ports 5023/tcp and 5055/http enabled.
- **Data forwarders (Prepared)**: `DataForwarder` model, `Forwarder` interface, declarative payload mapping with tests; no delivery loop; no regulator specification assumed.
- **Simulator**: GT06 and HTTP JSON clients (`--protocol gt06|http --token …`), running every scenario.

**Verification:**

| Check | Result |
| --- | --- |
| Scenario 2 step 4 and Scenario 3 | `apps/devices/tests/test_phase10_protocols.py`: real gateway + PostgreSQL + Redis + processor. Format defined and test-parsed through the API, model and device registered, token issued; a plain HTTP POST is stored and the vehicle is on the live map; a GT06 registered through the API logs in, sends 1 h of locations and a heartbeat; its records land in `positions`, live state, a trip, an overspeed alert and the trip report; a delimited-text port bound to a UI format accepts lines over TCP |
| Gateway | 129 passed (GT06 13, generic 17 including 3,000 Hypothesis cases on the new parsers); mypy strict and ruff clean; unit coverage 86.2% (CI floor 85); combined with the backend end-to-end tests 91% (CI floor 90) |
| Backend | 252 passed, 95% coverage; OpenAPI 223 operations, 0 warnings |
| Simulator | 18 passed (GT06 and HTTP clients against the real listeners) |
| Frontend | 78 Vitest tests |

**Scenario 3 code-diff criterion:** the GT06 change set is `gateway/gpsgw/protocols/gt06/*`, one entry-point line in `gateway/pyproject.toml`, seed data (`backend/apps/device_catalog/seed/gt06_generic.json`, migration `0004_seed_phase10`), tests and the simulator client. One plugin-local adjustment came from the end-to-end test: the per-connection buffer limit had to allow a backlog burst (`max_frame_size` 16 KB, single packets still limited to 1 KB). There is no git history in this delivery, so the diff itself can't be shown; this list is the record.

**Not verified here:** the Playwright browser runs for Scenarios 2.4 and 3 (no browser download in the sandbox), Docker images.

### Phase 8: Alerts, notifications and commands (complete)

Scope from `docs/PHASE_PLAN.md`: alert rules, deduplication, cooldown, quiet hours, delayed flag, notification center, email channel, prepared SMS/WhatsApp/push interfaces, Codec 12 command queue with verified templates. **Done when:** alert workflow tests pass, and a test command round-trips with the simulator.

**Alert engine** (`processor/gpsproc/alerts.py`; pure decision functions plus a database layer):

- **Signals:**
  - from analysis: overspeed (keeps a critical source severity), geofence enter/exit/dwell, crash/towing (critical), GSM jamming, power disconnect, harsh driving, long idle, long stop
  - from records: ignition on/off, low battery
  - from the status sweep: tracker offline, GPS lost
  - unauthorized movement: ignition on outside the allowed hours, or with no assigned driver
- **Rules (spec A11):** type, type-specific parameters (speed limit, geofences, minutes, volts), scope (all vehicles, vehicles or groups), severity, active schedule (including overnight windows, in the organization's time zone), recipients by role or user, and cooldown.
- **Deduplication:** a partial unique index allows one open alert per rule, vehicle and type; repeats increment `occurrences`.
- **Cooldown:** per rule, default 10 min.
- **Delayed alerts:** alerts from backlog data (the recompute replay) are stored with `delayed` and never notified.
- **Automatic resolution:** the system resolves offline and GPS-lost alerts with a note when the condition clears.
- **Delivery:** for each recipient, preferences per channel and type (minimum severity; defaults: in-app everything, email critical only). **Quiet hours** apply per user in their time zone to non-critical alerts. Messages are rendered in the user's language (EN/SW).
- **Channels:** in-app is stored and pushed at once over `/ws/notifications/`. Email is queued and sent by the Celery beat task `alerts.deliver_queued_notifications` (every 15 s) with back-off retries (1, 5, 30 min), then marked failed with the error. Every attempt is logged on the notification row.
- **SMS, WhatsApp and push** have the `NotificationProvider` interface and per-organization settings (`notification_channels`). They can't be enabled until a provider is installed.
- **Phase 7 consolidation:** `GeofenceRule` was replaced by alert rules of the geofence types (one rule system for administrators).

**Commands (spec A6, A13):**

- **Queue:** `commands` (PENDING, SENT, ACKNOWLEDGED, FAILED, TIMEOUT) and `command_responses`. The API builds commands only from the tracker model's templates, and the model must support commands.
- **Output control** (dangerous) requires the model's `output_control` capability, a template **verified on hardware**, the `commands.send_output` permission, an explicit confirmation and an audit entry. Every command is audited (`commands.queued`).
- **Gateway `CommandService`:**
  - sends the oldest PENDING command when the tracker connects (handshake), or at once on the Redis `commands` event
  - keeps one command outstanding at a time, **committed as SENT before it is written to the socket**
  - matches responses to the outstanding command
  - times out SENT commands after 60 s without a response, and PENDING ones at `expires_at` (24 h)
- **Simulator:** answers Codec 12 commands while waiting for ACKs or idling (Teltonika-style replies to `getinfo`, `getver`, `getgps` and `getstatus`). `--hold N` keeps the link open, modelling the FMC920 "Open Link Timeout" setting.
- **FMC920 "Open Link Timeout":** the tracker closes its TCP link this many seconds after its last data packet. A command can only reach it while the link is open, so for prompt commands set it to at least the record period (for example 300 s); otherwise commands wait until the next connection.

**Web app:**

- a notification bell (live unread count, latest notifications, mark all read)
- the Alerts page: open/new/acknowledged/resolved/all filters and severity; text in the reader's language; delayed and occurrence badges; acknowledge and resolve with notes
- Alert rules: type-aware fields mapped to `params` and `schedule`, with the organization's roles from the API
- notification settings and quiet hours on the Profile page
- a Commands dialog per tracker: templates with "not verified" and "output control" labels, a confirmation for output control, and live history with responses

**Phase 8 verification (2026-10-03):**

| Check | Result |
| --- | --- |
| **Alert workflow (real processor, PostgreSQL)** | Two speeding episodes give one alert with 2 occurrences and one notification round (cooldown). In-app is pushed to the fleet manager. The monitoring officer in quiet hours gets a suppressed in-app notification and no push. Email is queued and sent by the Celery task. Acknowledge with notes, then resolve; a new episode opens a new alert. A backlog replay gives a `delayed` alert with **no** notifications. Offline produces a critical alert to both (critical ignores quiet hours) and is **resolved automatically** when the tracker reports. Email failures retry and are then marked failed with the error; prepared channels fail cleanly without retries |
| **Command round trip (real gateway + simulator)** | Queued while offline: PENDING, then delivered at the handshake and answered (`Ver:...`). Connected: API, Redis, gateway, simulator, response **in 0.05 s**. Silent tracker: SENT, then TIMEOUT |
| Alert decisions (unit) | 18 tests: schedules (overnight), scope, parameters, unauthorized movement, severity, quiet hours in the user's zone, preferences and defaults, EN/SW messages, signals from analysis |
| Command safety | Wrong model's template, disabled tracker, viewer: refused. Output control needs capability, verification, confirmation and the dedicated permission (a fleet manager is refused). Audited |
| Browser (live stack) | A rule created in the UI fires from a simulated overspeed drive. Acknowledged with notes, then resolved. `getinfo` sent from the Commands dialog shows the simulator's `RTC:` reply. **21/21 scenarios pass** |
| Backend | 198 passed, 95% coverage; isolation test covers 32 routes; OpenAPI 167 operations, 0 warnings |
| Frontend | 61 Vitest tests, ESLint and TypeScript clean |
| Gateway, processor, simulator | 92 (mypy strict), 49, 17 |

**Bugs found and fixed during Phase 8:**

1. **Lost command responses (race):** the gateway wrote the command before committing SENT, so a fast reply found no outstanding command and was discarded. SENT is now committed first, and a failed write marks the command FAILED.
2. **Alert rules couldn't be saved:** multi-choice fields with static choices rendered as single selects, and the payload builder crashed on the string (`d.every is not a function`, seen in real Chromium). Fixed in the generic form, with a test.
3. **Role names showed as translation keys** in the rule form; they now come from the roles API. One label key was also wrong.
4. **A field named `timezone` shadowed Django's `timezone` module** inside the User model; renamed to `time_zone`.
5. **OpenAPI:** the preferences endpoint had no serializer for the schema, and shared choice lists had colliding enum names.

**Deferred, and why:**

- **SIM-expiry and maintenance-due alerts** need the scheduled checks and maintenance module (Phases 9–10); the types exist.
- **Tampering** has no signal source yet (no FMC920 IO mapped).
- **Long stop** fires when the stop ends, not while the vehicle is still standing.
- **Alerts for trackers with no vehicle** aren't deduplicated, because NULL vehicles are distinct in the unique index.

### Phase 7: Analysis engine (complete)

Scope from `docs/PHASE_PLAN.md`: trips (3 modes), stops, idling, overspeed episodes, geofences with hysteresis and the geofence editor, driving behaviour, drift and outlier filters, late-data recompute. **Done when:** all A16 processing tests pass, including the backlog scenarios.

**Engine (`processor/gpsproc/analysis/engine.py`):** a pure, deterministic, per-device state machine with JSON-serializable state. It applies the spec A10 defaults (configurable via the processor environment):

- **Trips** in ignition, motion and speed modes. Motion mode needs movement beyond the 30 m radius. Trips under 0.5 km or 2 min are discarded. Distance comes from the odometer when the model has one, otherwise from GPS. Each trip records driving, idle and stopped time, top and average moving speed, and point count.
- **Stops:** at most 3 km/h for at least 3 min, with an engine-off flag.
- **Idling:** engine on and standing for at least 5 min.
- **Overspeed episodes:** above 80 km/h for at least 15 s, one episode per continuous stretch, critical from 100 km/h; spikes are ignored.
- **Geofences:** circle and polygon, with a bounding-box pre-filter. Entry and exit need **2 points or 20 s** (hysteresis). Dwell events fire after a per-geofence time. The first sight establishes membership silently.
- **Driving-behaviour events:** harsh acceleration, braking and cornering, crash, towing, jamming, power disconnect and device overspeed, located at the last valid fix.
- **Filters:** outliers and fixes without GPS never add distance or affect stops, overspeed or geofences. GPS drift while standing (within 30 m) adds nothing. Duplicate and out-of-order records are ignored.

**Processor integration:**

- Each batch feeds every device's new records, in device-time order, through its engine.
- Results are written idempotently: every result table is unique on (device, start).
- The engine state is saved with the checkpoint **in the same transaction**, so a restart resumes mid-trip.
- Geofences are cached per organization for 60 s and invalidated immediately through the Redis `geofences` channel.

**Late-data recompute (spec A10, Scenario 4):**

- Late records, and records older than the engine's last time, queue **one pending request per device**; further late data widens its window.
- After a 30 s quiet period (debounce), the processor replays from the start of any trip open at the window start, replaces that window's results in one transaction, and marks results from the first late record on as `delayed`.
- Results still open at the window end are left to the live engine.

**Backend:**

- New tables: `trips`, `stops`, `idle_events`, `speed_events`, `driving_behavior_events`, `geofences` (with a PostGIS `geography` column kept in sync), `geofence_rules` (scope and triggers for Phase 8 alerts), `geofence_events`, `recompute_requests`; checkpoint state; a per-vehicle trip-mode override. RLS is on every tenant table.
- APIs: trips, stops, idle, speed, driving and geofence events (filterable by vehicle, device, driver and time); geofence CRUD (validated, audited, area synced, processor notified); geofence rules.
- Route history now returns geofence events.

**Web app:**

- **Trips page:** vehicle filter, distance and its source, a "delayed" badge, and "View route", which opens history for that exact trip.
- **Geofences page:** list plus map, with a **drawing editor**. Click corners for a polygon (undo and clear), or the centre plus a radius for a circle. Name, colour and dwell are editable, the drawing previews live, and server errors appear in the editor.
- **Route history:** geofence event markers.
- **Live map:** a geofence overlay toggle (spec A12, missed in Phase 5).

**Phase 7 verification (2026-10-02):**

| Check | Result |
| --- | --- |
| **A16 processing tests (engine)** | 17 tests: trips in each mode; noise discard; odometer distance; drift (parked jitter starts no trip and adds no distance); outliers and no-fix records add no distance; stops; idling; overspeed spikes, episodes and severity; geofence entry/exit and dwell; **boundary flapping produces no events**; outliers never enter geofences; behaviour events; duplicates and out-of-order input; **state restored mid-trip gives identical results**. Engine coverage **99%** |
| **System test (real processor, PostgreSQL)** | A drive gives 2 trips (13.2 km, vehicle, driver and organization stamped), 2 stops, 2 overspeed episodes and depot enter/exit. A **restart mid-trip** resumes correctly. **Redelivery** adds nothing. A **2-hour backlog in several batches** queues one request, and its replay adds the earlier trip marked `delayed` with live trips untouched |
| Processor coverage | Unit plus end-to-end combined 86% (engine 99%, geometry 98%, store 88%) |
| Browser (live stack) | Geofence drawn in the editor, 2 hours driven through the real gateway, trips on the Trips page, geofence markers on the trip's route. **20/20 scenarios pass** |
| Backend | 175 passed, 95% coverage; isolation test covers 26 routes |
| Frontend | 56 Vitest tests, ESLint and TypeScript clean |
| Gateway, simulator | 92, 16 |

**Bugs found and fixed during Phase 7:**

1. **Migration order on an empty database:** the geofence migration could run before PostGIS existed. It now depends on, and creates, the extension.
2. **Interactive migration prompt:** adding a required field made `makemigrations` wait for input. The migration is now hand-written.
3. **Test assumptions:** a mid-trip restart index fell in a parking stretch; one scenario's route never reached the drawn geofence; a trips poll counted rows before they rendered.

**Deferred, and why:**

- **Alert dedup and cooldown** (listed with A16 processing) belong to the alert engine, Phase 8. Geofence rules are stored, ready for it.
- **Reverse-geocoded addresses** on trips and stops are blank until geocoding is added.
- **Driver scores** from behaviour events are Phase 9 reports.

### Spec now in the repository

`docs/MASTER_SPEC.md` (Part A, A1–A18) and `docs/PHASE_PLAN.md` (Parts B–D) are exported from the Claude Doc, revision 24. Until now `MASTER_SPEC.md` was only a link, contrary to execution rule A1.1, and Phase 6 had been built from a summary rather than the spec text.

### Phase 6 remediation (against spec A12 / A15 and the Part B done criterion)

Reading the actual spec showed that Phase 6 had used a paraphrased done criterion ("a day renders in < 2 s"). The real one is "**route tests pass; a 31-day route stays within the point budget**". The gaps found and closed:

| Spec | Before | Now |
| --- | --- | --- |
| Maximum range 31 days, configurable | 7 days, fixed | `HISTORY_MAX_DAYS=31` |
| Point budget (default 5,000); 31-day route in under 3 s (A15) | 5,000 cap, never measured at 31 days | **89,280 points → 1,116 in 0.80 s** (test) |
| Stop threshold 3 km/h (A10) | 5 km/h | `STOP_SPEED_KMH=3` |
| Summary: idle time, average moving speed | missing | added (drift while standing adds no distance, A4 rule 8) |
| Speeding segments in a different colour | missing | episodes above the overspeed warning (80 km/h) for at least 15 s, drawn in orange |
| Direction arrows on the polyline | missing | chevrons along the route |
| Full resolution only for the zoomed-in segment | whole-range refetch only | `bbox` detail for the visible area at zoom 15 and above, drawn over the route |
| Speeds 0.5x, 1x, 2x, 4x, 8x, 16x; play, pause, **stop, restart**; timeline **event ticks** | 1x–900x, play/pause, no ticks | spec speeds (plus 60x and 300x for long ranges); stop and restart; stop and speeding ticks |
| Panel: timestamp, speed, heading, coordinates, ignition | timestamp and speed | all five |

**Bug found during remediation:** when the forced points (stop boundaries and ignition changes) alone exceeded the budget, as on a busy 31-day range, the coarsening loop could **never terminate**. Forced points are now capped at half the budget in priority order (stop starts, stop ends, ignition changes, then an even sample), and the loop is bounded. A test with 2,500 stops covers it.

Still not done for A12 history, recorded honestly:

- Geofence event markers on the route (needs Phase 7 geofences).
- Speeding events here are display-only; stored overspeed episodes come from the Phase 7 processor.

Verification after remediation:

| Check | Result |
| --- | --- |
| Backend | 156 passed, 94% coverage; 129 on SQLite |
| OpenAPI and deploy check | 123 operations, 0 warnings; clean |
| Frontend | 53 Vitest |
| Gateway, processor, simulator | 92, 14, 16 |
| Browser | **19/19**: a day draws in 0.31 s and plays in 0.13 s |

### Phase 6: Route history and playback (complete)

**Backend** (`/api/v1/tracking/history/`, `routes.view_history`):

- **Query:** positions for one vehicle and a **half-open** range `[start, end)` of at most 7 days, read through the tenant-filtered view. It excludes outliers and fixes without GPS, includes backlog records (they're real history), and uses the vehicle snapshot, so a tracker's time in another vehicle never leaks in.
- **Simplification:** Douglas–Peucker in local metres, with a tolerance of about 1.5 screen pixels at the requested zoom. It never drops stop boundaries or ignition changes, and caps the payload at 5,000 points.
- **Stops:** below 5 km/h for at least 3 minutes (configurable), with engine-off flags.
- **Summary:** distance, driving and stopped time, top speed.
- **Speed series:** sampled by **time** (the maximum in each of about 600 buckets), so the chart shows peaks and real driving rather than geometry points.
- **Exports** (`/export/?type=csv|gpx|kml`): full resolution, audited (`routes.exported`), and require `routes.view_history` **and** `reports.export`. Monitoring officers and viewers can watch history but not download it.
- **Permissions:** an action can now require several permissions at once.

**Web app** (`/app/history`):

- **Controls:** vehicle, presets (today, yesterday, last 24 h) or a custom range; state lives in the URL, so views are shareable.
- **Summary:** distance, driving, stopped, top speed and stops.
- **Map:** the route on a canvas renderer, with start, end and stop pins.
- **Playback:** a timeline slider, play/pause and 1×–900× speed. The cursor moves on every animation frame without re-rendering React; the UI updates at most 10 times a second.
- **Speed chart:** with the playhead marked; click to seek.
- **Stops list:** click to seek and pan to a stop.
- **Downloads:** CSV, GPX and KML, for roles that may export.
- **Map interface:** `MapAdapter` gained route, pin and cursor methods; Leaflet implements them.

**Simulator:** at the end of a route the vehicle parks with the engine off, then drives back, giving a realistic working day of trips and stops. `--backfill-hours 24` produces one through the real gateway.

### Phase 6 verification (2026-10-02)

| Check | Result |
| --- | --- |
| **Done criterion (real browser, live stack)** | A full day (8,640 records sent by the simulator through the real gateway): route **drawn in 0.25–0.33 s** and **playback moving in 0.11–0.15 s** across four runs. Target was under 2 s |
| History API | A synthetic day builds in well under the 1.5 s limit asserted by the test. Simplification keeps stop boundaries and ignition changes. Higher zoom gives more detail. Range limits, empty ranges, unknown vehicles. Permissions (fleet manager views and exports; monitoring officer and viewer view only; auditor and driver neither). Exports parse (CSV rows, GPX trackpoints, KML line and stop pins), are full resolution and audited. Consecutive windows never share a point. Vehicle snapshot respected |
| Isolation | A "history" policy in the automatic test: another tenant's vehicle answers 404 `not_found` for both view and export, and the test checks it's our 404, not a routing artefact |
| Backend | 151 passed on PostgreSQL + TimescaleDB, 94% coverage; 126 passed on SQLite; OpenAPI 123 operations, 0 warnings; deploy check clean |
| Frontend | ESLint and TypeScript clean; 52 Vitest tests, covering playback maths, route/pins/seek/play, the time-based speed readout, downloads, and the hidden export for non-exporting roles |
| Browser | **19 scenarios** (Phases 1, 2, 4, 5, 6), all passing on consecutive runs |
| Other suites | Gateway 92 (93% combined coverage), processor 14, simulator 16; all pass **while the dev stack runs alongside** |

Bugs found and fixed during Phase 6:

1. **`?format=csv` returned 404:** DRF reserves `format` to choose a renderer, so the parameter is now `type`. The isolation test had also "passed" for that wrong reason; it now asserts our own `not_found` code.
2. **Inclusive range end:** consecutive exports both contained the boundary point. Ranges are now half-open.
3. **Speed chart flat at zero:** it was drawn from geometry-simplified points, most of them stop boundaries at 0 km/h. It now uses a time-sampled series.
4. **Readout at a stop:** it showed the braking speed from the stop's bucket. Inside a detected stop it now shows 0.
5. **Speed chart Y axis** was given a `dataKey` meant for vertical layouts, which broke rendering.
6. **Test helper:** the wizard's vehicle selection passed a regex where Playwright needs a value.
7. **Tests interfered with a running dev stack:** they used the same Redis stream and consumer group, so the live processor consumed the tests' entries. Redis-using tests now run on database 15.
8. **Browser-problem detector:** it now fails on any 404 for non-API resources (missing scripts, styles, images) while allowing handled API 404s.

### Phase 5: Live tracking (complete)

**Processor service (`processor/gpsproc`):**

- **Stream consumer:** reads the gateway's `positions` Redis Stream as consumer group `processor`. It reads its own unacknowledged entries first after a crash, and acknowledges only after its effects are stored (at-least-once).
- **Per record:**
  - deduplicates by record hash (bounded memory)
  - skips backlog records for live display, so the map never jumps backwards
  - computes status: moving at 5 km/h or more; idle with the engine on; stopped with the engine off; motion flag for trackers without ignition; no GPS after 10 minutes without a fix
  - updates `device_state.status` and a per-device `processing_checkpoints` row
- **Publishing:** one batched message per organization into the Channels group `tracking.org.<id>`.
- **Sweep:** every 15 s, silent vehicles become **OFFLINE** after 5 minutes, or **NO_GPS**.
- **Recovery:** if Redis loses the stream or consumer group (restart without persistence, flush), the processor recreates the group itself.
- **Configuration and health:** thresholds are configurable; `/health` reports stats, Redis and database checks.

**Web app:**

- **Live map** (`/app/live`):
  - vehicle list with search (vehicle, driver, IMEI), status filter chips with live counts, group filter, and ticking "last update" times
  - clustered markers that rotate to the heading, coloured by status, moving smoothly (static under reduced motion)
  - fit-all, follow-selected, and a detail panel with every spec A12 popup field
  - a banner when updates are interrupted
  - stacks on phones, with no sideways scrolling
- **Dashboard** (home page for roles with `dashboard.view`):
  - live status cards (vehicles, moving, idling, stopped, offline, no GPS, trackers online)
  - fleet map and status chart
  - tracker health: never connected, offline over 24 h, weak signal, low tracker battery
  - roles without the dashboard keep the welcome page
- **Map abstraction (spec A12):** pages use a `MapAdapter` interface. Leaflet is the implementation behind `maps/index.ts`, so switching to MapLibre changes one export. Tile URL and attribution come from server settings.
- **Live data client:** REST snapshot first, then WebSocket updates. It **resynchronizes after every (re)connect** and when an unknown tracker appears, reconnects with backoff, and falls back to polling.

**Backend:**

- `/api/v1/tracking/live/` (snapshot), `/api/v1/dashboard/summary/` and `/api/v1/settings/map/`.
- WebSocket `/ws/tracking/`:
  - one-time ticket, origin check, and the same tenant permission rules as REST
  - organization from `?org=`, or implied for single-membership users
  - updates batched once per second, newest per tracker
  - optional viewport filter, used for fleets over 500 vehicles
  - session rechecked every 30 s
- Break-glass support access now includes viewing the live map (still read-only).

**Logs:** the gateway and processor emit one-line JSON **including structured fields**, which were previously dropped, hiding error details.

### Phase 5 verification (2026-10-02, PostgreSQL 16.15 + TimescaleDB 2.17.2, Redis 7)

| Check | Result |
| --- | --- |
| **Tracker → browser latency (system test)** | Real gateway, processor, Redis channel layer and tracking WebSocket: 5 packets, every one under 0.26 s with a 0.25 s batch window; the offline sweep pushed OFFLINE to the socket; another organization's socket received nothing |
| **Tracker → browser latency (real browser, live stack)** | Measured per animation frame: **0.8–1.0 s** over three runs, with the production 1 s batching window. Target was under 2 s |
| WebSocket isolation | Another organization's ID, a malformed ID, a role without live tracking, and multi-membership without `org` are all rejected; group messages never cross organizations; viewport filter |
| Processor | 14 tests: status rules, dedup, thresholds, update shape, **recovery after Redis loses the stream and group** (real Redis), JSON logs |
| Backend | 135 passed on PostgreSQL + TimescaleDB, 94% coverage; 120 passed on SQLite; isolation test covers 18 routes, including a "summary" policy (another tenant's data never counted); OpenAPI 121 operations, 0 warnings; deploy check clean |
| Gateway | 92 tests; 93% combined coverage |
| Frontend | ESLint and TypeScript clean; **46** Vitest tests, covering tracking client resync, unknown-tracker refresh, polling fallback, live map filters/search/panel/follow, banner, live dashboard counts, role-based landing, and clone field errors |
| Browser | **18 scenarios** (Phases 1, 2, 4, 5), 4 consecutive clean runs against Nginx, Django as `gps_app`, real gateway and processor processes, and Redis |
| Nginx | Edge config valid; production TLS config valid (its `http2 on;` needs nginx 1.25.1+, provided by the pinned `nginx:1.27-alpine` image) |
| Compose | base/dev/prod valid, now checked with Compose's own parse-then-substitute order |

Bugs found and fixed during Phase 5:

1. **Live map without tiles:** marker clustering crashed when no tile source was configured (the map had no max zoom). Max zoom is now set on the map itself.
2. **Processor stuck after losing its consumer group:** it never recovered after Redis lost the stream and group (71 errors in a row until restart). It now recreates the group; proven by a real-Redis test and by flushing Redis mid-run on the live stack.
3. **Error details missing from logs:** the gateway and processor logs dropped structured fields, so "stream batch failed" had no reason attached. Both now use a JSON formatter.
4. **Catalog search did nothing:** the device catalog API ignored its search box.
5. **Clone dialog hid the reason:** it showed only "Some fields are invalid." It now shows the server's reason under the field, e.g. a duplicate slug.
6. **Broken tile URL from Compose defaults:** the default tile URL contained braces, and Compose ends `${VAR-default}` at the first `}`, so a deployment without `MAP_TILE_URL` would have received a mangled URL. The default now lives in `.env.example`.
7. **Test harness:** an un-awaited async `act()` silently blocked rendering in later frontend tests; jsdom lacks `ResizeObserver` (a test-only stub was added).
8. **Test data collisions in browser tests:** three-digit random suffixes collided after dozens of runs, and growing lists pushed users and models to page 2. Tests now use UUID suffixes and search like a user.

### Phase 4: Onboarding UI and simulator (complete)

**Web app**

- **Add Device wizard** (`/app/devices/new`), following the A7 steps:
  - model (protocols still in development are visible but locked)
  - IMEI with live length and check-digit validation, name, serial, firmware
  - SIM: existing, new inline, or none
  - vehicle (optional)
  - review
  - connect: setup instructions with the real host, port and APN, beside the live connection test

  Server rejections return to the step that needs fixing, and a retry after a partial failure never creates the SIM or tracker twice.
- **Live connection test:** five stages (registered, connected, first data, valid GPS fix, readings understood), each derived only from what the gateway stored. It shows plain-language guidance for the current stage, latest decoded readings in units, connection details with readable close reasons, raw packets for Super Administrators, and **likely IMEI typos** (unregistered identifiers within two characters; never the wider inbox). Pushed over a WebSocket, with automatic fallback to polling. Also available as "Connection test" on every tracker.
- **Unregistered Devices** (Super Administrator): IMEI, protocol, port, attempts, peer, first bytes. **Register** puts it into an organization with a model of the matching protocol, recorded in that organization's audit log, and the tracker is accepted on its next attempt. **Dismiss** clears the entry.
- **IO mapping editor** (`/app/device-catalog/io-profiles/:id`):
  - editable rows, plus a **preview** applying the draft to the latest real IO values of a tracker using the profile (or a sample), with changed values highlighted
  - save as a new version with notes, and version history with rollback
  - server validation errors listed row by row
- **Clone a model** now copies its IO profile, so editing the clone never changes the original model's trackers.

**Backend**

- Diagnostics endpoint, WebSocket `/ws/devices/{id}/diagnostics/` (Channels):
  - one-time ticket auth, with a session check every 30 s
  - origin validation
  - the same tenant permission rules as REST
  - pushes only on change
- Profile preview endpoint, with a contract test proving it matches the gateway's mapper.
- Inbox registration endpoint.
- Catalog changes publish a `*` event so the gateway drops its whole device cache.
- Uniqueness conflicts are reported against the offending field (`details.identifier`, etc.).

**Gateway:** handles the `*` catalog-change event.

**Simulator (`simulator/gpssim`):**

- Speaks real Teltonika Codec 8E over TCP or UDP and never touches the database.
- Drives approximate Dar es Salaam routes (Morogoro Road, Port–Airport, Bagamoyo Road) with realistic acceleration, stops, engine on/off, 24 V readings and a rising odometer.
- Acts like device firmware: records stay queued until acknowledged and are resent after failures.
- Covers all 11 A16 scenarios, with time acceleration and backfill.
- CLI: `python -m gpssim` (defaults to the seed_demo fleet). Docker: `docker compose --profile sim run --rm simulator --scenario overspeed`.

### Phase 4 verification (2026-10-02)

| Check | Result |
| --- | --- |
| Gateway | 92 unit tests; 94% combined coverage |
| Simulator | 15 tests, every A16 scenario through an in-process gateway: overspeed above 100 km/h, 8-minute idle, jump flagged as outlier, invalid fixes stored invalid, 30-record backlog uploaded, resend stored once, unknown IMEI in the inbox, malformed packet recovered, go-offline stops |
| Backend | 124 passed on PostgreSQL + TimescaleDB, 93% coverage; 110 passed / 14 skipped on SQLite; OpenAPI 118 operations, 0 warnings; strict deploy check clean |
| WebSocket | Live updates; rejected without a ticket, with a forged ticket, from a foreign origin (ticket stays usable), on ticket reuse, for another organization, and for unknown trackers |
| Frontend | ESLint and TypeScript clean; 39 Vitest tests |
| **Scenario 2, steps 1, 2, 3, 5 (browser, live stack: Nginx, Django as `gps_app`, real gateway process, Redis, real simulator CLI)** | **Pass**: tracker registered in the wizard and accepted by the gateway without restart (panel turns green live over WebSocket); unknown IMEI in the inbox, registered there, next attempt accepted; cloned model with IO 66 scale edited to 0.01 makes IO 66 = 1265 read **12.65 V**; disabling closes the open connection **within 5 s** and refuses reconnection |
| All browser scenarios | 15 of 15, stable over 3 consecutive runs |

Bugs found and fixed during Phase 4:

1. **Cloning shared the IO profile.** Editing the clone's mapping would have silently changed every FMC920. Clone now copies the profile.
2. **IMEI-taken error stayed on Review.** The wizard didn't return to the IMEI step because the API sent no field detail; the frontend unit test had mocked one the server never sends. Fixed on both sides, and the test now covers both shapes.
3. **Raw close codes shown to users** ("Closed (device_closed)") are now plain language in English and Kiswahili.
4. **IO editor layout:** truncated "Kind" and target columns, and disabled inputs showing misleading hints.
5. **Test reliability:** three browser tests assumed new records appear on page 1 of paginated lists; they now search like a user. A test name collided with a button label; selectors are now exact.
6. **OpenAPI warning:** an untyped response failed the strict deploy check; now a proper schema.
7. **Leftover test database connections:** WebSocket worker threads blocked test teardown; tests now close connections after each use.

### Phase 3: Gateway and Teltonika (complete)

**Gateway (`gateway/gpsgw`)**

- **PostgreSQL storage (asyncpg):** one transaction per batch.
  - `positions` gets a bulk `INSERT ... ON CONFLICT DO NOTHING RETURNING` (true inserted count) with the organization and vehicle snapshot.
  - `device_state` gets the latest IO, plus position fields that **only move forward in device time**.
  - `devices.last_packet_at` is updated.
  - The ACK goes out only after commit. A database failure means no ACK.
- **Late and jump detection:** backlog records get `is_late`; impossible jumps (more than 300 km/h, more than 0.5 km) get `is_outlier`. The state advances only after a successful write, so a resend after a failed write isn't marked late. It's seeded from stored state after a restart.
- **Clock correction:** the per-model offset is applied (GT06 clones sending local time).
- **Registry:** a cached lookup with a 60-second TTL and a negative cache against unknown-IMEI floods. Inactive devices, organizations and protocols are refused. A broken IO profile still stores data, with raw IO kept.
- **Re-read on every packet:** disable, vehicle reassignment and IO profile edits apply to the very next packet of an open connection.
- **Hot reload (Redis pub/sub `device_registry`):** registration invalidates the cache; disable closes open connections; reconnects with backoff. The TTL covers missed events.
- **Listener manager:** reconciles TCP/UDP listeners with the `listener_ports` table every 10 s and immediately on `listener_ports` events. It skips protocols this gateway doesn't implement, reports ports in use, and keeps running listeners when the database is unreachable.
- **Startup sync:** the gateway marks which decoders it actually runs in `device_protocols.implemented`.
- **Unknown-device inbox:** throttled per identifier (one write per 10 s, attempts aggregated).
- **Connection history** (`device_connections`) and per-tracker raw packet logging (`raw_packets`), both best effort, never blocking ingestion.
- **Positions stream:** stored records are published to the Redis Stream `positions` (length-capped; the database is the source of truth).
- **Prometheus `/metrics`:** connections, packets, records by outcome (stored, duplicate, late, outlier, rejected), protocol errors, unknown devices, persistence failures, ingest-lag histogram, listeners.

**Backend:** the `device_connections` table (RLS), `unregistered_device_attempts`, the `raw_packets` hypertable (14-day retention, RLS), a Super Admin inbox API (registered identifiers drop out; dismiss is audited), listener-port change events, and the demo fleet in `seed_demo` (spec A17: T 123 ABC, T 456 XYZ, T 789 DEF with FMC920 trackers, SIMs, drivers and a group; plus T 321 CTL for the second organization).

**Deployment:** the gateway runs in PostgreSQL mode by default and starts after the backend is healthy, so migrations have run. Production is no longer blocked. The CI Docker job sends Teltonika's sample packet to the real stack and checks the row in PostgreSQL. CI enforces 85% gateway unit coverage, plus 90% combined with the end-to-end test.

### Phase 3 verification (PostgreSQL 16.15 + TimescaleDB 2.17.2 + PostGIS 3.4.2, Redis 7)

| Check | Result |
| --- | --- |
| Gateway unit tests | 91 passed |
| Gateway lint and types | ruff clean; mypy strict clean (34 files) |
| Gateway coverage | 86% unit; **94% combined** with the end-to-end test (`postgres.py` 98%, `app.py` 97%) |
| Backend | 114 passed, 93% coverage, including the end-to-end gateway test; 105 passed on SQLite |
| End-to-end gateway test (real `Gateway` against the test database and Redis) | All official fixtures over real TCP and UDP stored with correct values: external voltage 24.079 V, GSM 3, odometer metres→km, unmapped IO preserved, all JSON stored as objects. Resend stored once; backlog records flagged late; UDP ACKs byte-identical to Teltonika's documentation. Unknown IMEI refused and in the inbox. Reassignment changes the vehicle on the next record. Live state shows the newest valid fix. Connection history, raw packets, Redis Stream and metrics all verified. |
| Hot reload (measured) | registration accepted in **3 ms** (target 10 s); disabled tracker's connection closed in **27 ms** (target 5 s); new listener port live in **under 1 ms** (target 10 s) |
| Real process | the gateway process in PostgreSQL mode against the dev database: listeners loaded from the database; the official sample plus 3 live records from demo tracker T 123 ABC stored with vehicle snapshot and mapped IO; tracker's "last data" visible through the web API; `/metrics` correct |
| Frontend and browser | 30 unit tests; 11 browser scenarios still pass |
| Compose | base, dev and prod valid against the official schema |

Bugs found and fixed during Phase 3:

1. **JSON double-encoding:** asyncpg's codec already serializes `jsonb` parameters, so pre-serialized values for device-state IO and protocol transports would have been stored as JSON *strings*. Caught in review; the end-to-end test now asserts `jsonb_typeof` for every JSON column.
2. **Late flags after a failed write:** the annotator originally advanced its state before the write, so a resend after a database failure would have been flagged late. State now commits only after a successful write (tested).
3. **Test conflict:** two committed-transaction tests clashed through Django's `serialized_rollback` + `available_apps` (content type IDs). Seed data is now restored by the same seed functions the migrations use.
4. **CI would have failed:** CI's 90% gateway threshold counted only unit tests. Replaced with an honest pair of gates (unit 85%, combined 90%).

### Phase 2: Catalog and fleet (complete)

**Backend**

- **Device catalog (platform-wide):** manufacturers, protocols, device models (capabilities, identifier rules, trip detection mode, time offset, setup instructions), versioned IO mapping profiles with rollback, listener ports with reachability warnings, and command templates.
- **Catalog access:** members read it; only Super Administrators change it; every change is audited.
- **Catalog seed:** FMC920 (with the gateway's IO profile), FMB920 and FMC130 clones, GT06 and generic HTTP models, ports 5027/5023/5055, and four read-only FMC920 commands. Nothing is marked hardware-verified.
- **Contract test:** the backend validates the gateway's profile and checks that the seed copy is identical.
- **Fleet (tenant-owned, soft delete, audited):** SIM cards (APN password write-only), vehicle groups, vehicles (registration normalized, VIN checked), drivers (licence expiry flag), and trackers.
- **Trackers:**
  - Identifiers are validated per model: IMEI with Luhn check, serial, or custom pattern.
  - Each identifier is unique **across all organizations**, enforced by a database constraint, and the error never reveals the owner.
  - The identifier can't be changed after registration.
  - Install and remove trackers, enable and disable them, and retire them on delete (the identifier can then be reused).
  - Setup instructions are generated with the real server host, port and the SIM's APN.
  - CSV bulk import: preview first, then commit the valid rows in one transaction with one audit entry.
- **Assignment history:** tracker-to-vehicle and driver-to-vehicle, with database constraints allowing at most one open assignment per tracker, driver and vehicle, plus row locking against concurrent edits.
- **Gateway events:** `device_registry` Redis events are published after commit (created, updated, assigned, enabled, disabled, deleted), ready for the gateway's hot reload in Phase 3.
- **`positions` TimescaleDB hypertable:**
  - 1-day chunks, deduplication via `UNIQUE (device_id, device_time, record_hash)`, coordinate checks, and a generated PostGIS geography column
  - 5 indexes
  - compression after 7 days and a 730-day retention policy
- **`device_state`** table for the live map.
- **Database-level isolation:**
  - Row-Level Security on vehicles, groups, drivers, assignments, SIMs, devices and device state.
  - Positions are readable only through the `tenant_positions` view, because TimescaleDB forbids RLS on compressed tables. See DECISIONS.md.
- **Safer scoped manager:** unscoped tenant querysets may be built (DRF needs to), but any read or write raises.

**Frontend**

- **Generic resource page:** one implementation for every list:
  - search, filters and pagination
  - desktop table and phone cards
  - generated forms with labels, placeholders, required markers and inline and server-side errors
  - a ⋯ menu for edit and delete, and a delete confirmation
  - loading, empty and error states
- **Pages:**
  - Vehicles: groups, assign or remove a driver, history
  - Trackers: install or remove from a vehicle, enable or disable, setup instructions, CSV import with preview
  - Drivers
  - SIM cards
  - Vehicle groups
  - Device catalog for Super Administrators: IO mapping view, clone a model
  - Listener ports
- **Supporting pieces:** a safe Markdown renderer (no raw HTML), truly modal dialogs, and English and Kiswahili for every new string.

**Deployment**

- Compose publishes 5023, 5055 and the reserved range 5100–5109 (TCP and UDP) for UI-assigned listeners.
- `TRACKER_SERVER_HOST` and `THROTTLE_LOGIN` are configurable.
- CI raises the login limit for browser tests.

### Phase 2 verification (2026-10-01, PostgreSQL 16.15 + PostGIS 3.4.2 + TimescaleDB 2.17.2 built from source, Redis 7)

| Check | Result |
| --- | --- |
| Backend tests on PostgreSQL + TimescaleDB | 111 passed, 94% coverage |
| Backend tests on SQLite | 103 passed, 8 skipped |
| Tenant isolation | Automatic test over all 16 router routes, including the "catalog" policy (readable, but writes forbidden for organization admins) |
| Hypertable | Compression, policies, dimension and indexes asserted |
| Positions isolation as `gps_app` | Direct `positions` access denied; writes through the view denied; view shows only the tenant's rows **including rows in a compressed chunk**; no rows without tenant context |
| Chunk exclusion | EXPLAIN on the view with a 2-day filter scans fewer chunks than exist (the view does not defeat TimescaleDB) |
| OpenAPI | 112 operations, zero warnings |
| Frontend | ESLint, `tsc` and Vitest (30 tests) all pass; build 159 KB gzip |
| Browser (Chromium through the Nginx edge, backend as `gps_app`) | 11 scenarios, stable over 3 consecutive runs, 0 console errors and 0 CSP violations |

The 11 browser scenarios cover:

- create a SIM card and a vehicle, and edit the vehicle through the ⋯ menu
- register a tracker, with an IMEI typo caught first
- install the tracker in the vehicle
- setup instructions showing the real host, port and APN
- disable and re-enable the tracker
- driver phone validation, driver assignment, and history
- CSV preview and import
- the same IMEI refused for a second organization without revealing the owner
- viewer restrictions
- cloning a catalog model and viewing listener ports
- phone layouts without sideways scrolling

Bugs found and fixed during Phase 2 verification:

1. **RLS on memberships would have broken every sign-in.** Tenant resolution reads memberships before any tenant context exists. Caught in review before running.
2. **TimescaleDB's automatic index name collided** with mine (`positions_device_time_idx`).
3. **TimescaleDB forbids RLS on compressed tables.** Replaced with no-privilege-plus-security-barrier-view isolation, verified on compressed chunks.
4. **Schema generation crashed, and the earlier "37 operations" number was stale.** DRF builds uniqueness-validator querysets through the scoped manager. The fix: scope errors now fire on evaluation, not construction.
5. **A duplicate listener port returned 500 instead of 400.** Cleanup SQL ran inside an already-failed transaction.
6. **Required dropdowns started blank and blocked saving.**
7. **Blank optional numbers were sent as `null`.** Adding a vehicle failed with "This field may not be null" (odometer). Found only by the browser test, because the unit test asserted the wrong behaviour.
8. **Dialogs were never modal and lived inside table rows.** A pending "assign driver" request could close a dialog the user had opened since. Dialogs now use `showModal()` and a portal.
9. **Five buttons per row wrapped and crowded the table.** Moved to a ⋯ menu.
10. **Compose didn't publish the ports the API reported as reachable.**
11. **Mistranslation:** "pickup" was rendered as *pikipiki* (motorcycle) in Kiswahili.

## Verification run (2026-10-01, sandbox: Python 3.12.3, Node 22, PostgreSQL 16.15 + PostGIS 3.4, Redis 7)

### Phase 1 checks

| Check | Result |
| --- | --- |
| Backend tests on PostgreSQL | 46 passed, 93% coverage (`apps`, `common`) |
| Backend tests on SQLite | 43 passed, 3 skipped (the RLS tests need PostgreSQL) |
| Tenant isolation | Automatic test over every router route (list, detail, update, delete, header spoofing). Mutation-tested: an injected leak in `RoleViewSet` was caught. |
| Unauthenticated access | Every non-public `/api/v1/` route (more than 20 enumerated) returns 401 |
| RLS inside PostgreSQL | Verified as `gps_app`: other tenants invisible, cross-tenant insert rejected, audit UPDATE and DELETE denied, nothing visible without a tenant context |
| Live server as `gps_app` | Login, refresh with and without CSRF (200 / 403), organization-header spoof (404), viewer forbidden, logout then 401; 0 server errors |
| Frontend | ESLint clean; `tsc` clean; Vitest 21 passed; production build 145 KB gzip |
| Browser (Chromium 141 through the Nginx edge with the production CSP) | 5 scenarios passed: landing; admin journey (validation, wrong password, sign-in, add user, reload restores session, audit, Kiswahili, sign-out, redirect); viewer restrictions; Super Admin organizations; mobile with no sideways scroll. **0 console errors and 0 CSP violations.** |
| Deploy check | `check --deploy --fail-level WARNING` clean |

Bugs found and fixed during verification:

1. **Refresh-token reuse detection was rolled back by its own exception.** A stolen session would have stayed alive. Fixed and covered by a test.
2. **Restricted role had no table grants.** `post_migrate` never fired for the model-less `core` app, so `gps_app` would have failed every query at runtime.
3. **`/health` opened a database transaction.** That made liveness depend on the database, so Docker would restart a healthy backend whenever the database blipped.
4. **Org admins couldn't see their users' logins.** Auth events were recorded without an organization.
5. **Mobile users table unusable.** Role selects were crushed and "Remove access" was unreachable.
6. **Role dropdown changed roles instantly.** It now asks for confirmation.

### Phase 0 checks

| Check | Result |
| --- | --- |
| Gateway tests (pytest) | 74 passed, including official Teltonika packets, 4,500 Hypothesis fuzz cases, and real-socket TCP/UDP sessions |
| Gateway coverage | 92% overall; Teltonika decoders 98-100% |
| Gateway lint and types | `ruff check` clean; `mypy --strict` clean (28 files) |
| Gateway process smoke test | Real process: handshake `01`, TCP ACK `00000004`, UDP ACK `0005cafe010302`, unknown IMEI `00` |
| Backend | `manage.py check` clean; `makemigrations --check` clean; `check --deploy` clean with prod settings; 4 tests passed (SQLite) |
| Processor | 1 test passed |
| Frontend | `tsc --noEmit` clean; Vitest 3 passed; production build OK |
| Compose | base, base+dev and base+prod validated against the official Compose Specification JSON schema; PostgreSQL and Redis never published publicly |
| Nginx | `nginx -t` passes for dev, prod (TLS) and SPA configs; live proxy smoke test passed for `/health`, `/readiness`, SPA fallback and security headers |
| CI workflow | Validated against the GitHub Actions workflow schema |

## NOT YET VERIFIED

- `docker compose up --build`: Docker is unavailable in the build sandbox. The CI `docker` job builds the stack, waits for `/readiness` and sends a Teltonika handshake to port 5027. Run it once locally or in CI and record the result here.
- The `timescale/timescaledb-ha:pg16` Docker image itself: local verification used TimescaleDB 2.17.2 built from source on PostgreSQL 16. The CI backend job runs the same migrations and tests against the real image.
- Physical FMC920: see Part D of the master spec.

## CURRENTLY IMPLEMENTING

Nothing in progress.

## REMAINING (in order)

1. Operator: the production checklist in `docs/COMPLETION_REPORT.md`, including physical tracker confirmation (Part D)
   and a load test on production-sized hardware.

## Known issues

- Kiswahili translations need review by a native speaker before launch.
- Device models, IO profiles and FMC920 commands are all marked **not verified** until checked on real hardware (Part D).
- The IO mapping editor with live preview, and the Add Device wizard with its connection test, are Phase 4. Phase 2 provides a read-only mapping view and API-level versioning.
- Email verification of new accounts and MFA are "Prepared" scope (not built). Invitations already prove email ownership through the set-password link.

- The public OpenStreetMap tile servers are configured by default for development only; production needs a tile provider (`MAP_TILE_URL`).
- History requests a fixed simplification zoom (15). Refetching on large zoom changes could add detail for very long ranges.
- The history vehicle picker lists up to 200 vehicles; larger fleets need a searchable picker.
- The processor runs as a single consumer. Scaling out needs per-device sharding to keep ordering (Phase 11 load tests).
