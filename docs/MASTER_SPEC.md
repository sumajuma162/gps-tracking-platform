<!-- Exported from the Claude Doc "GPS Tracking Platform — Master Build Specification v2" (https://claude.ai/code/artifact/94741c6e-cdd6-411e-9076-14888fc4ddbd), revision 24, on 2026-10-02. Edit the Doc, then re-export. -->

# GPS Tracking Platform — Master Build Specification v2

2026-10-01 · @juma

## How to use this document

This is a build specification for an AI coding agent (Claude Code or similar), split into four parts. Do not paste everything as one task and expect a finished platform in one run; build it phase by phase.

1. **Part A — Master Specification.** Save it in the repository as `docs/MASTER_SPEC.md`. It is the single source of truth that every phase reads.
2. **Part B — Phase plan.** Thirteen phases (0–12), each with a definition of done. The agent completes and verifies one phase before starting the next.
3. **Part C — Phase kickoff prompts.** Paste these into the agent one at a time, starting a fresh session for each phase.
4. **Part D — Human checklist.** Steps only you can do, mainly connecting a physical Teltonika FMC920.

The agent keeps two files updated so any new session can resume accurately: `docs/PROGRESS.md` (what is done, in progress, and next) and `docs/DECISIONS.md` (technical choices and why).

What "adding a new GPS device through the UI" covers is defined precisely in section A7:

- A new unit of an already supported model: entirely through the UI, no code, no restart.
- A new model of an already supported protocol (for example another Teltonika model): entirely through the UI.
- A simple text or JSON tracker format: through the UI using a configurable generic protocol.
- A brand-new binary protocol: needs one decoder plugin written in code, then is enabled and configured through the UI. No core changes.

# Part A — Master Specification

## A1. Mission, agent role and execution rules

**Project:** Multi-Device Real-Time GPS Tracking, Fleet Monitoring and Route Management Platform.

**Your role:** Act as a senior software architect, full-stack developer, GIS engineer, GPS/IoT protocol engineer, DevOps engineer, database engineer, security engineer, QA engineer, UI/UX designer and technical writer.

**Mission:** Build a runnable, production-ready, multi-tenant web platform that receives GPS/AVL data from real trackers over the Internet, stores full location history in PostgreSQL/PostGIS/TimescaleDB, and shows live tracking, route history, playback, trips, stops, alerts, geofences and reports. It must support the Teltonika FMC920 first. Administrators must be able to add new tracker units and new tracker models through the web UI, and the platform must accept additional protocols as plugins without changing core business logic.

### Execution rules

1. **Inspect first.** At the start of every session, read `docs/MASTER_SPEC.md`, `docs/PROGRESS.md` and `docs/DECISIONS.md`, then inspect the repository. Never destroy working functionality.
2. **One phase at a time.** Work only on the phase named in the kickoff prompt (Part B). Do not start the next phase until the current phase's definition of done passes.
3. **Verify, don't assume.** After writing code, run the relevant checks: tests, `python manage.py check`, `makemigrations --check`, TypeScript type checks, frontend build, and `docker compose config`. When something fails, investigate, fix and rerun.
4. **No placeholders in scope.** Do not leave `TODO`, `pass`, mock returns or fake data in features the current phase requires. Items marked "Prepared" in A2 get real interfaces, models and documentation, but not full implementations.
5. **Honesty.** Never claim a feature works unless a test or a command proved it. You cannot connect a physical FMC920; prove the device path with real Teltonika packet fixtures sent over TCP to the gateway, and list physical testing as a human step.
6. **Keep records.** At the end of every phase, update `PROGRESS.md` with COMPLETED / CURRENTLY IMPLEMENTING / REMAINING, the commands you ran and their results, and known issues. Record every significant technical choice in `DECISIONS.md`.
7. **Ask only when blocked.** Make reasonable decisions and record them. Ask the human only when a decision is irreversible or requires information you cannot know, such as a domain name or real credentials.
8. **Code quality.** Use type hints, small focused modules, services separate from views, protocol decoders separate from database code, no duplicated logic, and docstrings on public interfaces. Format and lint with `ruff`, type-check with `mypy` (backend and gateway), and use ESLint with TypeScript strict mode on the frontend.

## A2. Scope tiers and technology stack

### Scope tiers

**MVP — fully implemented and tested:** Teltonika FMC920 over TCP and UDP (Codec 8, 8 Extended, Codec 12 commands), GT06 as the second binary protocol, a UI-configurable generic HTTP/JSON and delimited-text protocol, device onboarding through the UI (A7), multi-tenancy, RBAC, JWT authentication, vehicles, devices, drivers, SIM cards, live map, route history, playback, trips, stops, idling, overspeed, geofences, alerts, web and email notifications, reports with CSV/Excel/PDF export, dashboard analytics, audit logs, settings, share links, webhooks, API keys, maintenance reminders, English and Swahili UI, simulator, tests, Docker, Nginx, monitoring, backups and documentation.

**Prepared — interfaces, models and docs only:** MFA/TOTP, SMS/WhatsApp/push notification providers, MQTT ingestion, the Concox/Queclink/Jimi/SinoTrack decoders, a dealer/reseller tenancy level, regulator data forwarding (for example LATRA in Tanzania), road speed limits from OpenStreetMap, map matching, and the MapLibre map adapter.

**Deferred — documented only:** native mobile apps, multi-region deployment, Kubernetes, and billing/subscriptions.

### Technology stack

Use the latest stable versions at or above these minimums. Record exact pinned versions in `DECISIONS.md` and lock files.

| Layer | Choice |
|---|---|
| Backend | Python 3.12+, Django 5.2 LTS, Django REST Framework, djangorestframework-simplejwt, drf-spectacular (OpenAPI), django-filter, Django Channels 4 with channels-redis, Celery 5 with Celery Beat, structlog, argon2 password hashing |
| ASGI server | Uvicorn or Daphne behind Nginx |
| Gateway and processor | Python asyncio with uvloop, asyncpg, redis-py asyncio, pydantic, prometheus_client, structlog |
| Database | PostgreSQL 16+ with PostGIS 3.4+ and TimescaleDB 2.x (use the `timescale/timescaledb-ha` image, which bundles PostGIS) |
| Cache and messaging | Redis 7 with AOF persistence: Redis Streams for the ingestion pipeline, pub/sub for registry updates, Channels layer, Celery broker |
| Frontend | React 18+ with TypeScript strict, Vite, Tailwind CSS, React Router, TanStack Query, Zustand, Axios, react-hook-form with zod, react-i18next, Recharts, Leaflet with a clustering library behind a map adapter interface |
| Reports | openpyxl (Excel), WeasyPrint or ReportLab (PDF) |
| Monitoring | django-prometheus, Prometheus, Grafana, Sentry SDK (optional by environment variable) |
| Testing | Pytest, pytest-django, pytest-asyncio, Vitest, React Testing Library, Playwright (end-to-end), Locust or a custom asyncio load generator |
| Deployment | Docker, Docker Compose (base, dev override, prod override), Nginx, Certbot |
| CI | GitHub Actions |

## A3. Architecture and repository structure

The database is the source of truth. The gateway saves records before acknowledging them, then publishes them to a Redis Stream that the processor consumes. Protocol parsing never touches Django views or business logic.

```
GPS tracker → GSM/SIM → Internet
   ↓
GATEWAY (asyncio TCP/UDP/HTTP listeners, one protocol per port)
   → protocol identification → decoder plugin → validation → IO mapping
   → NormalizedLocation → batch insert (ON CONFLICT DO NOTHING) → ACK to device
   → XADD to Redis Stream "positions"
   ↓
PROCESSOR (Redis Stream consumer group, ordered per device)
   → device state machine → trips / stops / idling / overspeed / geofences
   → alerts → device_state update → publish to Channels group per organization
   ↓
BACKEND API (Django + DRF, REST /api/v1, OpenAPI) + WEBSOCKETS (Channels)
   ↓
CELERY (reports, exports, notifications, offline checks, retention, late-data recompute)
   ↓
FRONTEND (React SPA) via NGINX
```

The processor keeps a checkpoint of the last processed position per device in the database. If Redis is lost, it resumes from the database, so no event is skipped.

### Docker services

`nginx`, `frontend` (static build), `backend` (ASGI), `gateway`, `processor`, `celery-worker`, `celery-beat`, `postgres` (TimescaleDB + PostGIS), `redis`. Optional Compose profiles add `prometheus` and `grafana` (profile `monitoring`) and `simulator` (profile `sim`).

### Repository structure

```
gps-tracking-platform/
  backend/
    config/                 settings split: base, dev, prod, test
    apps/
      accounts/  organizations/  rbac/  devices/  device_catalog/  vehicles/
      drivers/  sims/  tracking/  processing/  trips/  geofences/  alerts/
      notifications/  reports/  analytics/  maintenance/  sharing/
      integrations/  commands/  audit/  settings_app/  health/
    common/                 tenant base models and managers, permissions, pagination
  gateway/
    main.py
    listeners/              tcp.py  udp.py  http.py  tls.py  manager.py
    protocols/
      base.py               BaseProtocolDecoder, DecodeResult, ProtocolRegistry
      teltonika/            handshake.py  codec8.py  codec8e.py  codec12.py
                            udp_framing.py  crc.py  ack.py  io_ids.py
      gt06/                 decoder.py  crc_itu.py
      generic/              json_http.py  delimited_text.py  (UI-configured)
    normalization/          normalized_location.py  io_mapper.py  validators.py
    services/               device_registry.py  persistence.py  stream_publisher.py
                            unknown_devices.py  connection_tracker.py  commands.py
    tests/fixtures/         real hex packets
  processor/                consumer.py  state_machine.py  detectors/  checkpoints.py
  frontend/src/
    app/  layouts/  pages/  features/  components/  maps/ (adapter interface)
    services/  hooks/  store/  types/  utils/  i18n/ (en, sw)
  simulator/                scenarios/  protocol_clients/ (teltonika, gt06, http)
  docker/  nginx/  scripts/  monitoring/  docs/  .github/workflows/
  docker-compose.yml  docker-compose.dev.yml  docker-compose.prod.yml
  .env.example  Makefile  README.md
```

You may improve this structure. Record any change in `DECISIONS.md`.

## A4. Ingestion correctness rules

These rules are mandatory for every protocol. Each one needs an automated test.

1. **Acknowledge only after persistence.** Send the device ACK only after its records are committed to PostgreSQL. If the database write fails, send no ACK (or a zero count) so the device keeps the records and resends them.
2. **Deduplicate.** Resent records are normal. Enforce a unique constraint on `(device_id, device_time, record_hash)` and insert with `ON CONFLICT DO NOTHING`. Count duplicates as accepted in the ACK so the device clears them.
3. **Batch writes.** Insert all records from one packet in a single transaction using a multi-row insert or `COPY`.
4. **Out-of-order and backlog data.** Devices upload hours of buffered records after losing coverage.
  - The current position is the record with the newest **device** timestamp, never simply the last received record. The live marker must never jump backwards.
  - Mark records older than the device's last processed timestamp as `late`. Do not push live alerts for them; store any resulting events with `delayed = true`.
  - Queue a Celery job to recompute trips, stops and daily aggregates for the affected device and time window.
  - Expose ingestion lag (server time minus device time) as a metric and in device diagnostics.
5. **Invalid fixes.** Records with 0 satellites, coordinates of exactly 0,0, or an invalid GPS flag are stored with `gps_valid = false`. They keep IO and status data but are excluded from routes, distance, geofences and overspeed.
6. **Coordinate and time validation.** Reject latitude outside −90..90 or longitude outside −180..180. Reject device timestamps more than 10 minutes in the future or before 2015-01-01 (both configurable), and log them as protocol errors.
7. **Jump filter.** Flag a record as `outlier` if the implied speed from the previous valid point exceeds a configurable limit (default 300 km/h). Store it, but exclude it from routes and distance.
8. **Stationary drift.** While ignition is off or speed stays below the stop threshold, do not add distance from GPS drift. Use a configurable minimum movement radius (default 30 m).
9. **Distance source.** Prefer the device odometer when the device model's capabilities include it. Otherwise compute distance from valid points with PostGIS geography distance. Record which source each trip used.
10. **Raw packet storage.** When enabled per device or globally, store raw packets in hex with direction, timestamp and decode result. Retain them for a configurable period (default 14 days).
11. **Resource limits.** Enforce a maximum packet size (default 64 KB for Teltonika TCP), a read timeout (default 5 minutes idle), a handshake timeout (default 30 seconds), a maximum number of connections per source IP, and a global connection limit. Malformed packets close the connection and increment an error metric; they never crash the gateway.
12. **Organization snapshot.** Each position stores the `organization_id` and `vehicle_id` that the device was assigned to at the moment of ingestion. Reassigning a device later never moves history to a new owner.

## A5. Teltonika FMC920 protocol requirements

Implement the protocol from Teltonika's official data sending protocol documentation, and verify every byte layout against it. The FMC920 sends AVL data over TCP or UDP, never over HTTP POST.

### TCP

- **IMEI handshake:** a 2-byte length followed by the IMEI in ASCII. Reply with one byte: `0x01` if the IMEI belongs to an active registered device, `0x00` otherwise. Unknown IMEIs go to the unregistered-device inbox (A7) and the connection is closed.
- **AVL packet:** 4 zero bytes (preamble), 4-byte data length, codec ID, record count, records, record count repeated, then a 4-byte CRC field holding a CRC-16/IBM over codec ID through the second count. Verify the length, both counts and the CRC. On mismatch, send no ACK and log a protocol error.
- **ACK:** a 4-byte big-endian integer equal to the number of records accepted, sent only after persistence (A4).

### UDP

UDP framing differs from TCP and must be implemented separately. Each datagram has a channel header (length, packet ID, one unused byte) and an AVL packet header (AVL packet ID, IMEI length, IMEI), followed by the codec data without the TCP preamble or CRC. The response echoes the packet ID and AVL packet ID with the accepted record count. The IMEI is checked on every datagram.

### Codecs

- **Codec 8 (****`0x08`****):** 1-byte IO IDs and counts.
- **Codec 8 Extended (****`0x8E`****):** 2-byte IO IDs and counts, plus the variable-length NX section.
- **Codec 12 (****`0x0C`****):** GPRS commands (type `0x05`) and responses (type `0x06`), used by the command queue (A13).
- **Any other codec**, including Codec 16: reject cleanly with a logged error and no crash. Codec 16 may be added later as a plugin.

Each AVL record has an 8-byte millisecond UTC timestamp, a priority byte, a GPS element (longitude and latitude as signed 32-bit integers × 10⁷, altitude, angle, satellites, speed in km/h), and the IO element with its event IO ID.

### IO mapping

IO IDs must not be hardcoded in business logic. Ship a seeded, editable FMC920 IO mapping profile (A7). Typical FMC920 IDs are listed below; verify each against the official FMC920 parameter list for the firmware in use.

| IO ID | Meaning | Normalized field |
|---|---|---|
| 239 | Ignition | `ignition` |
| 240 | Movement | `motion` |
| 21 | GSM signal (0–5) | `gsm_signal` |
| 66 | External voltage (mV) | `external_voltage` (×0.001 V) |
| 67 | Battery voltage (mV) | `battery_voltage` (×0.001 V) |
| 16 | Total odometer (m) | `odometer` (×0.001 km) |
| 69 | GNSS status | `gnss_status` |
| 181 / 182 | PDOP / HDOP | `pdop` / `hdop` |
| 200 | Sleep mode | `sleep_mode` |
| 246 | Towing | event: towing |
| 247 | Crash detection | event: crash |
| 249 | Jamming | event: GSM jamming |
| 252 | Unplug | event: power disconnected |
| 253 / 254 | Green driving type / value | events: harsh acceleration, braking, cornering |
| 255 | Over speeding | event: device overspeed |

Unmapped IO values are kept in `raw_metadata.io` as `{id: value}` so they can be mapped later without data loss. When the device does not report engine hours, compute them from ignition time.

### Commands

Codec 12 commands can only be delivered while the device's TCP connection is open. The command queue holds commands as `PENDING` until the device connects, sends them, then waits for a response or times out. Document the FMC920 "Open Link Timeout" setting, which controls how long the device keeps the link open.

### Transport security

Support an optional TLS listener for Teltonika devices whose firmware supports TLS, configured per port with a certificate path. Plain TCP remains the default.

### Fixtures

Use the example hex packets from Teltonika's documentation as unit-test fixtures for Codec 8, Codec 8E, Codec 12 and UDP. Add fixtures for a bad CRC, truncated packets, a wrong record count, an oversized length field, an unknown codec and an unknown IMEI.

## A6. Protocol plugin architecture and second protocol

Every tracker format is a plugin that produces the same `NormalizedLocation`. Nothing after normalization may know which protocol produced a record.

### Decoder interface

```python
class BaseProtocolDecoder(ABC):
    name: str                      # "teltonika", "gt06", "generic_json"
    transports: set[Transport]     # TCP, UDP, HTTP
    supports_commands: bool

    def can_handle(self, first_bytes: bytes) -> bool: ...        # for shared-port auto-detection
    def extract_frame(self, buffer: bytearray) -> Frame | None: ... # handles partial TCP reads
    def identify_device(self, frame: Frame) -> DeviceIdentity | None: ...
    def handshake_response(self, accepted: bool, frame: Frame) -> bytes | None: ...
    def validate(self, frame: Frame) -> None: ...                 # raises ProtocolError (CRC, length)
    def decode(self, frame: Frame, ctx: SessionContext) -> list[RawRecord]: ...
    def acknowledge(self, frame: Frame, accepted: int, ctx: SessionContext) -> bytes | None: ...
    def encode_command(self, command: DeviceCommand) -> bytes: ...  # only if supports_commands
    def decode_command_response(self, frame: Frame) -> CommandResponse | None: ...
```

`RawRecord` holds protocol-native values. The shared `io_mapper` then applies the device model's IO mapping profile (A7) to produce `NormalizedLocation`. Decoders must be pure: no database access, no Django imports, and fully unit-testable with byte fixtures.

### Normalized record

`NormalizedLocation` (pydantic) fields: `device_id`, `imei` / `device_identifier`, `protocol`, `device_time` (UTC), `server_time`, `latitude`, `longitude`, `altitude`, `speed_kmh`, `heading`, `satellites`, `hdop`, `gps_valid`, `ignition`, `motion`, `odometer_km`, `engine_hours`, `external_voltage`, `battery_voltage`, `battery_level`, `gsm_signal`, `event_code`, `events[]` (normalized event types such as `crash` or `harsh_braking`), `sensors{}` (custom mapped values), `priority`, `raw_metadata`, `record_hash`.

### Registry and discovery

Decoders register through a `ProtocolRegistry` and the Python entry-point group `gps_platform.protocols`, so a new protocol is a new package with no edits to the core. On startup, the gateway syncs the list of available protocols and their capabilities to the `device_protocols` table so the UI can offer them.

By default each listening port is bound to one protocol, which is configured in the database and editable in the UI. Optional shared-port auto-detection uses `can_handle()` on the first bytes received.

### Second protocol: GT06

Implement GT06 fully as the proof of extensibility, using its published protocol document.

- **Framing:** start bytes `0x7878` (or `0x7979` for extended length), length, protocol number, content, serial number, CRC-ITU, then the stop bytes `0x0D0A`.
- **Login (****`0x01`****):** the IMEI is encoded as BCD. Reply with a login ACK that echoes the serial number, but only if the device is registered.
- **Location (****`0x12`****, ****`0x22`****):** a date-time of 6 bytes, satellites, latitude and longitude as 4-byte values divided by 1,800,000, 1-byte speed, and a course/status word holding the GPS-valid flag and the hemisphere bits.
- **Heartbeat (****`0x13`****) and alarm packets (****`0x16`****, ****`0x26`****):** decode them and send the ACK each type expects.
- **Timezone quirk:** some GT06 clones send local time instead of UTC. Add a `time_offset_minutes` setting on the device model to handle them.

It must flow into the same database, map, alerts and reports with no change outside `gateway/protocols/gt06/` and a seeded device model.

## A7. Adding new GPS devices through the UI

An administrator must be able to add a tracker through the web UI and have it work within seconds, with no code change, no container restart and no Django Admin. This is a core MVP requirement with its own acceptance tests.

### What the UI must support

| Situation | How it is added | Code change? |
|---|---|---|
| New unit of a supported model (another FMC920) | Add Device wizard | No |
| New model of a supported protocol (for example Teltonika FMB920 or FMC130) | Device Catalog: clone a model and adjust its IO profile and capabilities | No |
| Simple JSON-over-HTTP or delimited-text tracker | Device Catalog: generic configurable protocol with field mapping | No |
| New binary protocol | Decoder plugin package (A6), then enabled and configured in the UI | Plugin only, no core change |

### Device Catalog (Super Administrator)

Three levels: **Manufacturer → Device Model → Device unit**.

A device model holds:

- name, manufacturer and protocol (chosen from the protocols the gateway has registered)
- supported transports and the default listener port
- an identifier type (IMEI with a Luhn check, serial number, or custom regex) and its validation rule
- **capability flags:** ignition, odometer, engine hours, external voltage, battery, GSM signal, commands, harsh-driving events, crash, jamming, fuel or temperature sensors
- the IO mapping profile and `time_offset_minutes`
- the recommended trip detection mode: ignition-based, motion-based, or speed-based (A10)
- verified command templates
- setup instructions written as a Markdown template
- an active/deprecated status

Capability flags drive the UI and processing. For example, battery fields are hidden for models without battery data, and trip detection falls back to motion if ignition is unsupported.

Seed models: Teltonika FMC920 (fully configured), Teltonika FMB920 and FMC130 (cloned profiles marked "unverified" until tested), GT06 generic, and a generic HTTP JSON model.

### IO mapping profile editor

A table editor maps a protocol IO ID or field path to a normalized field or a custom sensor. Each mapping has a data type, scale, offset, unit, and an optional boolean mapping or event mapping, such as "IO 247 value 1 → crash event".

- Profiles are versioned; editing creates a new version and can be rolled back.
- A **live preview** applies the draft profile to the device's last stored raw packet and shows the before and after values, so the admin can see the effect before saving.
- Mapping formulas are declarative (scale and offset only). Never use `eval` or user-supplied code.

### Listener port management

The Super Administrator binds listener ports to protocols and transports in the UI. Docker cannot publish ports at runtime, so Compose pre-publishes a reserved range for UI-assigned listeners. The defaults are:

- **5027:** Teltonika, TCP and UDP
- **5023:** GT06, TCP
- **5055:** generic HTTP
- **5100–5109:** reserved range for UI-assigned listeners

The gateway starts or stops listeners within 10 seconds of a change. The UI warns when a port is outside the published range or blocked by the documented firewall rules.

### Add Device wizard (Organization Admin and Super Admin)

1. Choose the manufacturer and model. The protocol, port and capabilities fill in automatically.
2. Enter the IMEI or identifier. Validate the format and Luhn check digit, and enforce platform-wide uniqueness, because one IMEI can never belong to two organizations. Show a clear error if the IMEI exists elsewhere, without revealing the other tenant.
3. Enter the device name, serial number and firmware version, then select or create a SIM card (number, ICCID, operator, APN, data plan, expiry date).
4. Optionally assign a vehicle and driver.
5. **Setup instructions:** generated from the model's template with this platform's server host, port, protocol and the SIM's APN filled in. These include the configurator steps and SMS configuration text, with copy buttons.
6. **Connection test:** a live panel that tracks progress through "waiting for connection → connected from IP → identifier accepted → first packet decoded → valid GPS fix → IO values mapped". It shows the decoded values, the last raw packet (Super Admin only) and plain-language troubleshooting for each stage that has not been reached, such as "No connection yet: check APN, server address, port and SIM data balance".
7. Finish. The device becomes active, and its status is visible on the device page and live map.

Also provide **bulk import** from CSV (model, IMEI, SIM, vehicle). Each row is validated, a preview is shown, then the import is committed as a single audited operation.

### Hot registration (no restarts)

- Creating, updating, disabling or reassigning a device publishes an event on Redis pub/sub channel `device_registry`. The gateway updates its in-memory registry immediately.
- On a cache miss, the gateway queries the database. Unknown identifiers are held in a negative cache for 60 seconds to resist flooding.
- Disabling a device closes its open connections within 5 seconds and rejects further data.
- Changing a device's model or IO profile takes effect on its next packet.
- If the pub/sub message is lost, a periodic registry refresh every 60 seconds makes the state converge.

### Unregistered device inbox

When an unknown identifier connects, the gateway records the identifier, the detected protocol, the port, the source IP, the first and last time seen, the attempt count, and one raw packet sample (rate-limited). The data is not stored as positions.

The Super Administrator sees an **Unregistered Devices** page with a **Register** button that opens the Add Device wizard pre-filled, including an assignment to an organization. The device's next connection attempt is accepted. Teltonika devices retry automatically, so no visit to the vehicle is needed.

### Generic configurable protocol

For trackers that send JSON over HTTP or delimited text over TCP or UDP, the Super Administrator defines a format in the UI:

- the identifier field, latitude, longitude, timestamp and its format, speed and its unit (km/h, knots or mph), heading, altitude, ignition, battery and extra sensors
- field paths (JSON) or column indexes (delimited), plus the separator and line terminator
- the authentication method for HTTP (per-device token, or the identifier as a parameter)
- an optional ACK string

A **test parse** box accepts a sample message and shows the resulting normalized record or the validation errors. HTTP ingestion requires a per-device token or an allowlisted identifier, and enforces the same size limits and rate limits as other listeners.

### Device lifecycle

The UI supports these operations, each audited:

- activate and disable
- assign and unassign vehicles, with assignment history kept
- replace the tracker on a vehicle, with history continuing on the vehicle
- transfer between organizations (Super Admin only), where history stays with the original organization
- inventory states: in stock, installed, faulty, returned, retired

The device page shows connection history, the last 50 decoded records, raw packets for admins, device events, command history and ingestion lag.

## A8. Multi-tenancy, roles and authentication

### Tenancy

The hierarchy is Platform → Organization → users, vehicles, devices and every other record. A `parent_organization` field is prepared for a future Dealer → Customer level.

- Every tenant-owned model inherits a `TenantModel` base with `organization` (non-null, indexed) and a default manager that **requires** an organization scope. Unscoped access is only possible through an explicit `.unscoped()` call used by platform services.
- DRF viewsets inherit a `TenantViewSet` that scopes querysets from the authenticated user's active organization. Object lookups outside the tenant return **404, never 403**, so resource existence is not leaked.
- Add PostgreSQL Row-Level Security on `positions`, `vehicles`, `devices`, `trips` and `alerts` as defense in depth, keyed by a session variable set per request. Document how platform services bypass it.
- Users may belong to several organizations, with a role per organization, and switch the active organization in the UI.
- Super Administrator access to an organization's data is "break-glass": it requires a stated reason, is time-limited (default 2 hours), is written to the audit log and is visible to that organization's admins.

### Roles

Permissions are stored in the database (`roles`, `permissions`, `role_permissions`) as codenames such as `vehicles.change` or `routes.view_history`. Seed these roles:

| Role | Scope |
|---|---|
| Super Administrator | Whole platform: organizations, device catalog, protocols, listener ports, unregistered devices, health, global settings, platform audit |
| Organization Administrator | Organization profile, users, devices, vehicles, drivers, SIMs, geofences, alert rules, settings, reports, integrations |
| Fleet Manager | Live tracking, vehicles, driver assignment, routes, trips, alerts, reports, maintenance |
| Monitoring Officer | Live map, alerts (acknowledge/resolve), route history, playback |
| Driver (optional portal) | Own assigned vehicle and own trips only |
| Viewer | Read-only tracking and reports, no exports unless granted |
| Auditor | Read-only audit logs and reports |

Route history, exports and share links are separate permissions because location history is sensitive. Organization admins can create custom roles from the permission list. The frontend hides menu items without permission, but the API always enforces permissions independently.

### Authentication

- Email and password login with argon2 hashing and Django password validators (minimum 12 characters, a breached/common password check, not similar to the user's attributes).
- **Tokens:** the access JWT has a 10-minute lifetime and is held in memory only. The refresh token has a 7-day lifetime, rotates on every use, is blacklisted on reuse, and is stored in an httpOnly, Secure, SameSite=Strict cookie scoped to `/api/v1/auth/`. CSRF protection applies to the refresh and logout endpoints.
- **Session visibility:** a session list shows device, IP and last use, with "log out other sessions" and "log out everywhere".
- Logout, password change, forgot/reset password with single-use expiring tokens, and optional email verification.
- **Login protection:** account lockout after 5 failed attempts within 15 minutes, with a 15-minute lock and admin unlock. Login is throttled per IP and per account, and every API is rate-limited per user.
- **WebSocket authentication:** the client requests a one-time ticket (30-second lifetime) from the REST API, then connects with it. JWTs never go in URLs. The server re-checks permissions when the client subscribes.
- **API keys:** per organization with scopes, for machine-to-machine integrations. Keys are hashed at rest, shown once at creation, and can be revoked.
- **MFA (Prepared):** TOTP models and endpoints behind a feature flag. Not enforced by default.

## A9. Data model and position storage

All timestamps are stored in UTC and displayed in the organization's timezone (demo default `Africa/Dar_es_Salaam`). Use foreign keys, check constraints, unique constraints, `created_at`/`updated_at` on every table, and soft deletion (`deleted_at`) for business entities. Location data is never soft-deleted; it is removed only by retention policies.

### Tables

| Area | Tables |
|---|---|
| Identity | `users`, `roles`, `permissions`, `role_permissions`, `organization_users`, `user_sessions`, `api_keys`, `mfa_devices` (prepared) |
| Tenancy and settings | `organizations`, `organization_settings`, `platform_settings` |
| Device catalog | `device_manufacturers`, `device_protocols`, `device_models`, `io_mapping_profiles`, `io_mapping_profile_versions`, `io_mappings`, `generic_protocol_definitions`, `listener_ports`, `command_templates` |
| Devices | `devices`, `sim_cards`, `device_assignments`, `device_connections`, `device_events`, `raw_packets`, `unregistered_device_attempts`, `device_state` |
| Fleet | `vehicles`, `vehicle_groups`, `vehicle_group_members`, `drivers`, `driver_assignments`, `maintenance_schedules`, `maintenance_records` |
| Tracking | `positions` (hypertable), `processing_checkpoints` |
| Analysis | `trips`, `stops`, `idle_events`, `speed_events`, `driving_behavior_events` |
| Geofencing | `geofences`, `geofence_rules`, `geofence_events` |
| Alerts and notifications | `alert_rules`, `alerts`, `notifications`, `notification_preferences`, `notification_channels` |
| Commands | `commands`, `command_responses` |
| Reports and integrations | `report_jobs`, `scheduled_reports`, `webhooks`, `webhook_deliveries`, `share_links`, `data_forwarders` |
| Audit | `audit_logs` |
| Cache | `geocode_cache` |

### Positions (high volume)

For scale: 1,000 vehicles reporting every 30 seconds produce up to about 2.9 million rows per day. Design for this from the first migration.

- `positions` is a TimescaleDB hypertable partitioned on `device_time`, with 1-day chunks. The primary key and unique constraint include the time column: `UNIQUE (device_id, device_time, record_hash)`.
- **Columns:** `id` (bigint identity), `organization_id`, `device_id`, `vehicle_id` (snapshot at ingest), `device_time`, `server_time`, `geom` (`geography(Point, 4326)`), `latitude`, `longitude`, `altitude`, `speed_kmh`, `heading`, `satellites`, `hdop`, `gps_valid`, `is_outlier`, `is_late`, `ignition`, `motion`, `odometer_km`, `engine_hours`, `battery_voltage`, `external_voltage`, `battery_level`, `gsm_signal`, `event_code`, `events` (jsonb), `sensors` (jsonb), `protocol`, `raw_metadata` (jsonb).
- **Indexes:** `(device_id, device_time DESC)`, `(vehicle_id, device_time DESC)`, `(organization_id, device_time DESC)`, GiST on `geom`, and a partial index on `events` where it is not empty.
- **Compression:** compress chunks older than 7 days, segmented by `device_id` and ordered by `device_time DESC`.
- **Retention:** a per-organization retention period (default 365 days) is enforced by a Celery job, plus a platform maximum enforced by a TimescaleDB retention policy.
- **Continuous aggregates:** daily per-vehicle distance, moving time, max speed and point count, used by reports and dashboards.

`device_state` holds one row per device: the latest valid position, last packet time, connection status, computed status (`ONLINE_MOVING`, `ONLINE_IDLE`, `ONLINE_STOPPED`, `OFFLINE`, `NO_GPS`, `UNKNOWN`), the current trip, and the latest IO values. It is mirrored in Redis for the live map. The live map and dashboard never query `positions`.

### Migrations and documentation

All schema changes go through Django migrations. TimescaleDB and RLS setup run as `RunSQL` migrations with a reverse. Document every table, relationship and index in `DATABASE.md`, with a Mermaid ER diagram.

## A10. Processing engine

The processor consumes the `positions` Redis Stream with a consumer group. Records for the same device are always processed in order: shard by device ID across consumers. Each device has a persisted state machine (current trip, open stop, open idle, open overspeed, geofence membership), checkpointed to the database after each batch.

### Detection rules

- **Trip detection modes:**
  - `ignition`: a trip runs from ignition on to ignition off.
  - `motion`: a trip starts when the device moves beyond the movement radius at a speed above the start threshold, and ends after the stop duration.
  - `speed`: speed only, for devices without motion or ignition data.
    The mode comes from the device model and can be overridden per vehicle. Trips shorter than the minimum distance or duration are discarded as noise. Each trip stores the vehicle, device, driver, start and end time, start and end coordinates and address, distance and its source, duration, driving/stopped/idle durations, maximum speed, average moving speed and point count.
- **Stops:** speed at or below the stop threshold for at least the minimum stop duration. Store the start, end, duration, centroid coordinates and address when available.
- **Idling:** ignition on and speed at or below the idle speed for at least the idle duration. This creates an `idle_event` and, if a rule exists, an alert.
- **Overspeed:** per organization, with optional per-vehicle-group thresholds at warning and critical levels. One event per continuous episode, storing start, end, duration, maximum speed, threshold and coordinates. Short spikes below a minimum duration are ignored.
- **Geofences:** circles (center and radius) and polygons stored as PostGIS geography. Membership is checked with `ST_Covers` against geofences cached per organization with a bounding-box pre-filter. Entry and exit require the new state to hold for N consecutive valid points or T seconds (hysteresis), which prevents flapping along boundaries. Rules link geofences to all vehicles, specific vehicles or vehicle groups, with entry/exit/dwell triggers and optional schedules.
- **Driving behavior:** harsh acceleration, braking and cornering, crash, towing, jamming and power disconnect come from device events where the capability exists. These build a per-driver score used in reports.
- **Offline detection:** a Celery Beat task every 60 seconds marks devices with no packet within the offline timeout as `OFFLINE`, and as `NO_GPS` when packets arrive without a valid fix for longer than the GPS-loss timeout.
- **Late data:** late records (A4) trigger a debounced recompute of trips, stops and aggregates for the affected device and day. Recomputed results replace the earlier ones in a single transaction.

### Default thresholds

All thresholds are configurable per organization, with optional per-vehicle overrides.

| Setting | Default |
|---|---|
| Offline timeout | 5 min |
| GPS-loss timeout | 10 min |
| Trip start speed | 5 km/h |
| Minimum trip distance / duration | 0.5 km / 2 min |
| Stop speed threshold | 3 km/h |
| Minimum stop duration | 3 min |
| Idle duration | 5 min |
| Movement radius (drift filter) | 30 m |
| Overspeed warning / critical | 80 / 100 km/h |
| Minimum overspeed duration | 15 s |
| Geofence hysteresis | 2 points or 20 s |
| Long stop alert | 120 min |

## A11. Alerts and notifications

### Alert types

overspeed (warning/critical), geofence entry, geofence exit, geofence dwell, device offline, GPS signal lost, unauthorized movement (motion or ignition outside an allowed schedule or with no assigned driver), ignition on, ignition off, low tracker battery, external power disconnected, tampering/unplug, GSM jamming, towing, crash, harsh driving, long idle, long stop, maintenance due, and SIM expiry.

### Alert records and lifecycle

An alert stores `organization`, `vehicle`, `device`, `driver`, `alert_rule`, `type`, `severity` (info/warning/critical), `message` (rendered in the user's language), `coordinates`, `occurred_at`, `delayed` (from backlog data), `status` (new → acknowledged → resolved), `acknowledged_by/at`, `resolved_by/at` and `notes`. Alerts that clear automatically, such as a device coming back online, are resolved by the system with a note.

### Alert rules

Alert rules are configured by organization admins. Each rule has a type, parameters, scope (all vehicles, specific vehicles or groups), severity, an active schedule, and recipients by role or user.

- **Deduplication:** one open alert per rule, vehicle and type at a time. Repeats update a counter instead of creating new alerts.
- **Cooldown:** a per-rule minimum time between notifications (default 10 minutes).
- **Quiet hours:** per user, applied to non-critical notifications.
- **Delayed alerts:** alerts from backlog data are stored and shown with a "delayed" label, and are never pushed as if they were live.

### Notifications

A `NotificationChannel` provider interface handles delivery, with real implementations for **in-app** (WebSocket push, unread count and a notification center) and **email** (SMTP, sent through Celery with retries). SMS, WhatsApp and push notifications have provider interfaces and settings only, with no vendor hardcoded.

Each user sets preferences per alert type and severity for each channel. Every delivery attempt is logged with its status and error.

## A12. Frontend, live tracking and maps

### General UI

The UI must be an enterprise application: clean, professional, desktop-optimized, tablet-friendly and usable on mobile. Use a restrained palette with consistent status colors (moving green, idle amber, stopped blue/grey, offline grey, warning orange, critical red), always paired with an icon or label so meaning never relies on color alone.

- **Interface patterns:** collapsible sidebar, loading skeletons, empty states with a next action, friendly error states (never raw stack traces), confirmation dialogs for destructive actions, toast notifications, and forms with labels, placeholders and inline validation.
- **Languages:** English and Swahili through react-i18next, with a language switch saved per user. Backend messages and alert texts are translatable too. Dates, numbers and units (km or miles) follow user and organization settings.
- **Installable app:** ship as a PWA with a manifest and service worker caching the app shell (never location data).
- **Accessibility:** keyboard navigation, focus states, ARIA labels on map controls, and WCAG AA contrast.
- **Session expiry:** a silent token refresh, and on failure a redirect to login that returns the user to the page they were on.

### Public landing page

The first page is a public landing page, not a login page. Sections: introduction, live tracking, route monitoring, supported devices (generated from the device catalog's public models), how it works, benefits, privacy and security, about, and a login button. Navigation: Home, Features, Supported Devices, How It Works, About, Login. It never shows real fleet data.

### Navigation (role-filtered)

Dashboard, Live Map, Vehicles, Devices, Drivers, SIM Cards, Trips, Route History, Geofences, Alerts, Reports, Analytics, Maintenance, Notifications, Users, Integrations, Audit Logs, Settings, Profile, Logout. Super Admin also sees Organizations, Device Catalog, Listener Ports, Unregistered Devices and System Health. A global search (Ctrl+K) finds vehicles by registration, devices by IMEI or name, drivers and, for Super Admin, organizations.

### Dashboard

Cards for total vehicles, moving, idle, stopped, offline, online devices, alerts today and trips today. Also show a fleet map, recent alerts, device health (low battery, weak GSM, stale data), today's distance and trip summaries, and a status distribution chart. Everything is scoped to the active organization and the user's permissions and updates over WebSocket.

### Live map

- Shows all authorized vehicles with a heading-rotated icon colored by status, clustering, search, and filters by status, group and device model.
- Follow-vehicle mode, fit all, full-screen workspace, a layer switcher (street/satellite where licensed), and a geofence overlay toggle.
- **Vehicle popup / side panel:** registration, device, IMEI, driver, coordinates, speed, heading, ignition, satellites, last update (relative and absolute), status, battery, external voltage, GSM strength, current trip, plus quick links to history and playback.
- **Live updates:** connect to `/ws/tracking/` and update markers in place with smooth animation between positions. Batch updates every 1 second per client. Clients may subscribe to the current viewport bounds to reduce traffic.
- **Connection status:** show a reconnecting/offline banner with exponential backoff reconnects, then resynchronize from REST after a reconnect.

### Route history and playback

- **Query:** choose a vehicle with a date, or a start and end date-time (a maximum range is configurable, default 31 days).
- **Backend downsampling:** the server returns a simplified polyline sized to the requested zoom level and point budget (default 5,000 points), using `ST_Simplify` or Douglas-Peucker. Full-resolution points load only for the zoomed-in segment.
- **Display:** the polyline with direction arrows, start and end markers, stop markers, speeding segments in a different color, and geofence event markers.
- **Summary:** distance, duration, driving, idle and stopped time, maximum speed, average moving speed and number of stops. A table of stops and events links to map positions.
- **Playback controls:** play, pause, stop, restart, a timeline slider with event ticks, and speeds of 0.5x, 1x, 2x, 4x, 8x and 16x. The panel shows the current timestamp, speed, heading, coordinates and ignition state.

### Maps and geocoding

- **Map adapter:** all map code goes through a `MapAdapter` interface (markers, polylines, polygons, circles, clusters, fit bounds, events) with a Leaflet implementation. A MapLibre implementation must be possible without touching pages.
- **Tile source:** tile URLs and attribution come from backend settings, never hardcoded. Document that the public OpenStreetMap tile servers are not for production fleet use; configure a commercial provider or self-hosted tiles. Provider keys must be domain-restricted, or tiles proxied through Nginx.
- **Geocoding:** reverse geocoding goes through a `GeocodingProvider` interface (Nominatim, Google, Mapbox, others). Results are cached in `geocode_cache` keyed by rounded coordinates (about 11 m precision), requests are rate-limited per provider (Nominatim at most 1 request per second, or a self-hosted instance), and lookups run asynchronously through Celery. When geocoding fails, coordinates are shown instead and the lookup is retried later.

## A13. Reports, analytics, APIs and integrations

### Reports

Daily movement, trips, distance, speed/overspeed, stops, idling, device offline, geofence, alerts, driver (including the behavior score), fleet summary, vehicle utilization, maintenance and device health.

- **Filters:** organization (Super Admin), vehicle or group, driver, date range and status.
- **Display:** on-screen tables with pagination and a chart where useful.
- **Exports:** CSV, Excel and PDF. Exports over 10,000 rows or 31 days run as Celery `report_jobs` with progress shown in the UI and a download link that expires after 24 hours. Every export is written to the audit log.
- **Scheduled reports:** daily, weekly or monthly, emailed to chosen recipients.

### Analytics

Charts for daily distance, fleet utilization, vehicle status distribution, trips per day, alerts over time, overspeed occurrences, idle durations, device uptime and ingestion volume. All of them are built from continuous aggregates, not raw positions.

### REST API

The API is versioned under `/api/v1/`, with OpenAPI at `/api/v1/schema/` and Swagger UI at `/api/docs/` (restricted in production).

- **Endpoint groups:** `auth`, `users`, `organizations`, `roles`, `device-catalog`, `listener-ports`, `unregistered-devices`, `devices`, `sims`, `vehicles`, `vehicle-groups`, `drivers`, `positions`, `routes`, `trips`, `stops`, `geofences`, `alerts`, `alert-rules`, `notifications`, `reports`, `analytics`, `maintenance`, `commands`, `share-links`, `webhooks`, `api-keys`, `audit-logs`, `settings`, `search`, `health`.
- **Conventions:** cursor pagination for positions and large lists, consistent error envelopes, filtering, ordering, and `Idempotency-Key` support on create endpoints.
- **Mobile-ready:** the same API serves future Android, iOS and Flutter apps.

### WebSockets

- `/ws/tracking/` sends position and status updates for the user's active organization, optionally restricted to a viewport or a vehicle list.
- `/ws/alerts/` sends new and updated alerts and notifications.
- `/ws/devices/{id}/diagnostics/` is the live connection-test feed for the Add Device wizard (A7), admins only.
- Channel groups are per organization. Every subscription re-checks permissions, and a user removed from an organization is disconnected.

### Device commands

Commands are built from verified per-model `command_templates`. The command queue is stored in `commands` with the statuses `PENDING`, `SENT`, `ACKNOWLEDGED`, `FAILED` and `TIMEOUT`, and responses are stored in `command_responses`. For the FMC920, ship read-only commands first (`getinfo`, `getver`, `getgps`, `getstatus`). Never ship unverified dangerous commands; output-control commands such as immobilization require an explicit capability flag, a confirmation dialog, a dedicated permission and an audit entry.

### Integrations

- **Share links:** a time-limited, revocable public URL showing the live position of one vehicle (or a trip) on a minimal map, with no other data. Creating a share link requires a permission and is audited.
- **Webhooks:** per organization, for selected events (alert created, trip completed, geofence event, device offline). Payloads are signed with HMAC-SHA256, retried with backoff, and every delivery is logged.
- **Data forwarders (Prepared):** a generic outbound forwarder framework (HTTP JSON, configurable mapping) for regulators, insurers or client systems. Do not invent any regulator's specification; document that it must be configured from the official specification.

### Other modules

- **Maintenance:** schedules by odometer, engine hours or date interval per vehicle, with due-soon and overdue alerts and service records with cost and notes.
- **Settings:** platform and organization levels, covering timezone, units, default map center (Dar es Salaam for the demo) and zoom, offline timeout, all thresholds in A10, data retention, notification settings, map and geocoding providers, and language.
- **Audit log:** login, failed login, logout, user creation, role changes, device/vehicle/driver/SIM changes, catalog and IO profile changes, geofence changes, alert acknowledgement, settings changes, exports, share links, commands, break-glass access and API key creation. Each entry stores the user, action, resource, resource ID, timestamp, IP, user agent and metadata (diffs with secrets redacted). Audit logs are append-only and searchable in the UI.

## A14. Security, privacy and compliance

### Application security

Implement all of the following: JWT security as in A8, RBAC, tenant isolation with RLS, DRF serializer validation on all input, ORM-only queries (any raw SQL parameterized), React's default escaping (no `dangerouslySetInnerHTML` on user data), CSRF on cookie-authenticated endpoints, strict CORS allowlists, rate limiting, safe error responses with a correlation ID and no internals, and secrets only in environment variables.

**Security headers (Nginx):** HSTS, a Content-Security-Policy that allows only the configured tile and geocoding hosts, X-Content-Type-Options, Referrer-Policy, frame-ancestors none, and Permissions-Policy.

### Device-side security

- **Spoofing:** IMEI-only identification can be spoofed, so add these mitigations:
  - accept only registered and active identifiers
  - alert on duplicate simultaneous connections for the same IMEI
  - alert on physically impossible jumps
  - an optional per-device source-IP or carrier allowlist
  - optional TLS listeners (A5)
- Enforce the packet size limits, timeouts, per-IP connection caps and global connection caps from A4. Decoders must survive fuzzing; add property-based tests with Hypothesis that feed random bytes into every decoder.
- Never log full raw packets at INFO level. IMEIs in logs are partially masked outside debug mode.

### Infrastructure security

PostgreSQL and Redis are never published to the host or Internet in production; they are reachable only on the internal Docker network. Redis requires a password. Only ports 80, 443 and the tracker ports are public. Containers run as non-root users with read-only filesystems where possible. Dependencies are scanned in CI with pip-audit, npm audit and Trivy for images.

### Location privacy

Location history is personal data when it can be linked to a driver.

- Separate permissions for route history, exports and share links.
- Audit logging of all exports and share links.
- Configurable retention with automatic deletion.
- No public coordinates except through explicit share links.
- A data export and erasure procedure for a driver's personal data, documented in `SECURITY.md`.

### Compliance notes (Tanzania)

Document in `SECURITY.md` that operators in Tanzania must assess their obligations under the Personal Data Protection Act, 2022 (for example registration with the Personal Data Protection Commission, lawful basis or consent, driver notice, and retention). Document that operators serving commercial or public transport must check LATRA vehicle tracking requirements, and that the data forwarder (A13) is the integration point. Do not present this as legal advice.

## A15. Observability, performance, deployment and backup

### Health and monitoring

- **Health endpoints:** `/health` (liveness) and `/readiness` (database, Redis, migrations applied) on the backend. Equivalent HTTP health ports on the gateway and processor. A Celery ping task.
- **System Health page (Super Admin):** status of every service, active device connections per listener, packets per second, decode errors by protocol, ingestion lag (p50/p95), Redis Stream backlog, Celery queue length and failed tasks, database size and largest chunks, and WebSocket client count.
- **Prometheus metrics** from backend, gateway and processor, with a Grafana dashboard provisioned as code under `monitoring/`. Sentry is enabled when `SENTRY_DSN` is set.
- **Structured logging:** JSON logs through structlog with a correlation ID, covering application errors, gateway startup, device connections and disconnections, protocol errors, database failures, background tasks and security events. Passwords, tokens, API keys and secrets are never logged.

### Performance targets

Verify these with the load test in A16 and report the measured results; do not claim them.

| Metric | Target (4 vCPU / 8 GB VPS) |
|---|---|
| Concurrent device connections | 5,000 |
| Sustained ingestion | 1,000 records/s |
| Packet received → browser marker update | under 2 s at p95 |
| One-day route query | under 1 s |
| 31-day downsampled route | under 3 s |
| Live map with 2,000 vehicles | smooth panning, no full re-render |

### Docker and environments

- `docker-compose.yml` is the base; `docker-compose.dev.yml` adds hot reload and exposes the database locally (dev only); `docker-compose.prod.yml` adds resource limits, `restart: unless-stopped`, log rotation and no exposed internal ports.
- `docker compose up --build` starts the whole platform. An entrypoint waits for the database, runs migrations and runs `collectstatic`. The `make seed` (or `scripts/seed.ps1`) command loads demo data.
- Images use multi-stage builds, pinned base images, non-root users and healthchecks.
- `.env.example` documents every variable with safe placeholders:
  - Django: `DJANGO_SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`
  - Database and Redis: `DATABASE_*`, `REDIS_URL`, `REDIS_PASSWORD`
  - Auth: `JWT_ACCESS_MINUTES`, `JWT_REFRESH_DAYS`
  - Tracker listeners: `TELTONIKA_TCP_PORT`, `TELTONIKA_UDP_PORT`, `GT06_TCP_PORT`, `GENERIC_HTTP_PORT`, `RESERVED_PORT_RANGE`, `GATEWAY_TLS_CERT` / `GATEWAY_TLS_KEY`
  - Email: `EMAIL_*`
  - Platform defaults: `DEFAULT_TIMEZONE`, `MAP_TILE_URL`, `MAP_TILE_ATTRIBUTION`, `GEOCODER_PROVIDER`, `GEOCODER_API_KEY`
  - Observability: `SENTRY_DSN`
  - Demo: `SEED_DEMO_PASSWORD`
    No real secrets are ever committed.

### Nginx

Nginx serves the SPA with history fallback and proxies `/api/`, `/ws/` (with upgrade headers and long timeouts), `/admin/` (IP-restrictable in production), `/static/` and `/media/`. It applies the A14 security headers, gzip/brotli, request size limits and API rate limits. A production HTTPS configuration uses Let's Encrypt via Certbot, with automated renewal and an HTTP→HTTPS redirect. Tracker ports are not proxied through Nginx; they are published directly from the gateway as Layer-4 TCP/UDP.

### Backup and restore

- **Backups:** WAL archiving with pgBackRest or WAL-G for point-in-time recovery, plus a nightly full backup. Backups go to storage outside the server (S3-compatible or another host), never only inside a container volume.
- **Retention:** 7 daily, 4 weekly and 6 monthly backups.
- **Other assets:** back up `.env` and certificates securely and separately.
- **Restore drills:** a script restores into a scratch database and runs row-count checks. The drill is documented and recommended monthly.
- **Documentation:** `BACKUP_RESTORE.md` covers manual backup, restore, point-in-time recovery, volume backup and full server recovery.

### Production deployment (Ubuntu Server 24.04 LTS)

`PRODUCTION_DEPLOYMENT.md` covers these topics:

- server preparation and a non-root deploy user
- Docker installation
- DNS records for the web domain and a tracker domain (for example `gps.example.com`)
- UFW firewall: 22 restricted, 80, 443 and the tracker TCP/UDP ports open, database ports never open
- cloud security groups and NAT/port forwarding when self-hosting behind a router
- SSL certificates
- `.env` hardening
- first start, migrations and creating the Super Admin
- restart policies, log rotation, monitoring and alerting
- upgrades with a pre-upgrade backup and rollback steps

### Scaling path (document only)

Multiple gateway instances behind a Layer-4 load balancer (TCP connections are sticky by nature), with the FMC920 backup server setting as failover. Horizontally scaled stateless API containers, Redis Sentinel or Cluster, PostgreSQL tuning, read replicas for reports, and multi-node TimescaleDB or archival to object storage. Kafka or NATS replace Redis Streams if volume demands it, and Kubernetes comes later.

## A16. Simulator, testing and CI

### Simulator

The simulator must speak real protocols over the network to the gateway. It must never insert into the database directly. Clients exist for Teltonika Codec 8 and 8E over TCP and UDP, GT06 over TCP, and generic JSON over HTTP. Each simulated device uses a configurable IMEI that must be registered (or deliberately unregistered) through the platform.

- **Realistic movement:** routes along real Dar es Salaam roads from bundled GeoJSON tracks, with realistic speed profiles, heading, ignition cycles, stops, idling and GPS noise.
- **Scenarios** (selectable by command-line flag):
  - `normal_day`
  - `overspeed`
  - `geofence_cross`
  - `long_idle`
  - `go_offline`
  - `coverage_gap_backlog` (buffers 2 hours, then uploads)
  - `gps_jump`
  - `invalid_fix`
  - `duplicate_resend` (resends unacknowledged records)
  - `unknown_imei`
  - `malformed_packet`
- **Load mode:** thousands of concurrent virtual devices with a configurable report interval, used for the A15 performance targets.
- **Marking:** simulated devices are tagged `is_simulated` and shown with a badge in the UI. Run with `docker compose --profile sim up simulator` or `python -m simulator --scenario normal_day --devices 3`.

### Automated tests

| Area | Must cover |
|---|---|
| Gateway | IMEI handshake accept/reject, Codec 8, Codec 8E, Codec 12, UDP framing, CRC failure, truncated and oversized packets, partial TCP reads, ACK counts, ACK withheld on database failure, duplicate resend, unknown IMEI → inbox, GT06 login/location/heartbeat, generic JSON and delimited parsing, Hypothesis fuzzing of all decoders |
| Device onboarding | Device created in the UI is accepted within 10 s without restart; disabling drops the connection; inbox → register → accepted; IO profile edit changes the next record's mapping; model clone works; generic protocol test-parse |
| Processing | Trips in each mode, stops, idling, overspeed episodes, geofence hysteresis, drift filter, outlier filter, late-data recompute, offline/no-GPS status, alert dedup and cooldown |
| Backend | Auth flows, lockout, token rotation, RBAC per role, CRUD for every management entity, location and route APIs with downsampling, reports and exports, webhooks signing, share link expiry, audit entries |
| Tenant isolation | An automatic test that enumerates **every** registered API route and WebSocket channel and asserts that a user from organization B can never read or modify organization A's objects (404 or empty) |
| Frontend | Login, token refresh, protected routes, role-based menu, dashboard, live map store updates from WebSocket messages, vehicles/devices CRUD forms, Add Device wizard, route history, playback controls, alerts acknowledgement, i18n switching |
| End-to-end (Playwright) | The acceptance scenarios in A18 against the full Docker stack and the simulator |

Coverage targets: decoders 95%, processing 90%, backend 80%, frontend 70%. Report the actual figures.

### CI (GitHub Actions)

Run on every push and pull request, with no deployment secrets required:

- **Backend and gateway:** ruff, mypy, Pytest (with TimescaleDB/PostGIS and Redis service containers), migration check, `manage.py check --deploy` against a CI settings profile.
- **Frontend:** ESLint, `tsc --noEmit`, Vitest, and the production build.
- **Images and config:** Docker image builds, `docker compose config` validation, pip-audit, npm audit and Trivy.
- **End-to-end:** the Playwright suite, nightly or on a label.

## A17. Documentation and seed data

### Documents in `docs/`

`README.md` (root), `MASTER_SPEC.md`, `PROGRESS.md`, `DECISIONS.md`, `SYSTEM_ARCHITECTURE.md`, `INSTALLATION.md`, `DEVELOPMENT.md`, `WINDOWS_DEVELOPMENT.md`, `PRODUCTION_DEPLOYMENT.md`, `DEVICE_INTEGRATION.md`, `ADDING_DEVICES_VIA_UI.md`, `TELTONIKA_FMC920.md`, `GT06.md`, `API.md`, `DATABASE.md`, `SECURITY.md`, `TESTING.md`, `MONITORING.md`, `TROUBLESHOOTING.md`, `USER_GUIDE.md`, `ADMIN_GUIDE.md`, `BACKUP_RESTORE.md`, `SCALING.md`.

### Key content requirements

- **README quick start**, in both bash and PowerShell:
  1. Clone the repository and `cd gps-tracking-platform`.
  2. Copy `.env.example` to `.env` and set the required values.
  3. Run `docker compose up --build`.
  4. Run migrations (automatic) and the seed command.
  5. Open the browser and log in.
  6. Start the simulator and watch the vehicles move.
    Then explain how to switch from the simulator to a real FMC920.
- **`WINDOWS_DEVELOPMENT.md`****:** Windows 11 with Docker Desktop and the WSL2 backend, Git (with line-ending settings: `core.autocrlf=input`, `.gitattributes` forcing LF for shell scripts), VS Code, PowerShell equivalents of every `make` target (`scripts/*.ps1`), port conflicts, and common errors such as WSL memory limits, file permission issues and CRLF entrypoint failures.
- **`TELTONIKA_FMC920.md`****:** what an FMC920 needs (a SIM with an active data plan, PIN disabled or configured, the APN for the local carrier), configuration with Teltonika Configurator and by SMS, the server domain or IP, port and protocol (TCP or UDP), codec selection (Codec 8 or 8E), records and data-acquisition settings, the Open Link Timeout, the firewall, registering the IMEI in the UI, using the connection test panel, reading device logs, verifying received AVL packets in raw packet view, and a no-data troubleshooting tree. Mark all parameter IDs and SMS syntax as "verify against the official Teltonika documentation for your firmware".
- **`ADDING_DEVICES_VIA_UI.md`****:** step-by-step with screenshots or descriptions for each A7 case, plus how to write a decoder plugin for a new binary protocol.
- **Network requirements:** a public IP or DNS name, opening TCP/UDP ports, NAT/port forwarding, cloud VPS security groups, and why the database must never be exposed.
- **Mermaid diagrams:** system architecture, device communication sequence (handshake → AVL → ACK), data flow, database ERD, deployment architecture, authentication and token refresh, live tracking flow, trip detection state machine, alert workflow, multi-tenant isolation, and device onboarding through the UI.

### Seed data (development only)

- **Organizations:** Demo Logistics Ltd (timezone Africa/Dar_es_Salaam, km, English and Swahili) and a second organization, Coastal Transport Ltd, used to demonstrate isolation.
- **Users:** `superadmin@example.com` (Super Administrator), `admin@demo.local` (Organization Administrator), `fleet@demo.local` (Fleet Manager), `monitor@demo.local` (Monitoring Officer), `viewer@demo.local` (Viewer), `auditor@demo.local` (Auditor), `driver@demo.local` (Driver), and `admin@coastal.local` for the second organization. Passwords come from `SEED_DEMO_PASSWORD` in `.env` or are randomly generated and printed once by the seed command. The seed command refuses to run when `DEBUG=False`.
- **Fleet:** vehicles T 123 ABC, T 456 XYZ and T 789 DEF with drivers, one vehicle group, three FMC920 devices with SIM records, and one GT06 and one generic HTTP device in the second organization.
- **Geofences and rules:** a depot circle and a port-area polygon in Dar es Salaam, plus overspeed and geofence alert rules.
- **History:** 7 days of simulated history generated **through the gateway** with the simulator's backfill mode, so trips, stops and events are produced by the real pipeline. All simulated data is flagged.

## A18. Acceptance tests, completion report and prohibited actions

The platform is complete only when every scenario below passes as an automated Playwright and Pytest suite against the full Docker stack. The simulator sends real protocol packets over TCP/UDP in place of physical devices. Physical FMC920 confirmation is done by the human using Part D.

### Scenario 1: Core tracking flow (Teltonika)

1. The Super Admin logs in and creates an organization and its admin user.
2. The organization admin adds an FMC920 through the Add Device wizard using an IMEI, creates a vehicle and assigns the tracker.
3. The simulator connects as that IMEI to the public Teltonika port. The gateway accepts the IMEI (`0x01`), decodes AVL data, stores it in PostGIS, and ACKs the correct count.
4. The connection test panel shows each stage completed. The vehicle appears online, and the browser receives WebSocket updates and moves the marker without a page reload.
5. Route History shows the path. Playback runs at all speeds.
6. A trip and stops are detected. The overspeed and geofence scenarios produce events and alerts, and an alert appears on the dashboard and is acknowledged.
7. A trip report is generated and exported to CSV, Excel and PDF, and the export is in the audit log.
8. A user from the second organization cannot see the vehicle, its positions, trips, alerts or WebSocket events, and receives 404 when requesting them directly.

### Scenario 2: Adding devices through the UI

1. **New unit:** register a new FMC920 IMEI in the UI while the gateway is running. A simulator connecting with it is accepted within 10 seconds, with no restart.
2. **Unknown device:** an unknown IMEI connects, is rejected and appears in Unregistered Devices. The admin registers it from the inbox, and the next attempt is accepted.
3. **New model:** clone the FMC920 model as "FMB920" in the Device Catalog, edit one IO mapping, and register a device of that model. The next record reflects the edited mapping.
4. **Generic tracker:** define a generic JSON-over-HTTP format in the UI, use test-parse with a sample, register a device, and POST data with `curl`. The vehicle appears on the live map.
5. **Disable:** disabling a device in the UI closes its connection within 5 seconds and rejects further data.
6. **Bulk import:** a 20-row CSV import shows validation errors for bad rows, then imports the valid ones.

### Scenario 3: Extensibility (GT06)

A GT06 device registered through the UI sends login, location and heartbeat packets. Its positions flow through the same `NormalizedLocation`, positions table, live map, trips, alerts and reports. A code diff shows no changes outside the GT06 plugin and seed data.

### Scenario 4: Data integrity and resilience

- **Database outage:** with the database stopped, the gateway sends no ACK. After a restart, the simulator's resend produces no data loss and no duplicates.
- **Coverage backlog:** a 2-hour backlog upload never moves the live marker backwards, marks the records late, recomputes trips correctly and labels any alerts as delayed.
- **Bad data:** GPS jumps and invalid fixes do not appear on routes or in distance totals.
- **Redis outage:** after Redis restarts, the processor resumes from its checkpoint with no missed events.
- **Malformed input:** malformed and oversized packets are rejected without affecting other connections.

### Completion report

At the end of the final phase, deliver a structured report with these sections:

- implemented modules
- files created
- database tables
- API endpoints
- WebSocket endpoints
- tracker ports
- supported protocols and device models
- user roles
- test results with counts and coverage
- load test results against the A15 targets
- Docker services
- default development users (no passwords in the report)
- how to start
- how to connect an FMC920
- how to add a device and a device model through the UI
- how to add a new protocol plugin
- known limitations
- security notes
- a production checklist

### Prohibited actions

Do not:

- build only a static UI
- fake device communication while claiming physical tracker support
- store only the latest location
- overwrite or edit location history
- hardcode an IMEI, an IO ID in business logic, or FMC920-specific logic outside its plugin and seed data
- parse protocols inside Django views
- make Django Admin the user-facing platform
- expose another tenant's data
- expose PostgreSQL or Redis publicly
- commit secrets
- assume HTTP POST is how the FMC920 sends AVL data
- ACK before persistence
- require a restart to add a device
- use `eval` or user-supplied code for mappings
- implement unverified dangerous device commands
- claim completion while required modules contain placeholders or failing tests
