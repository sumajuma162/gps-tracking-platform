# System architecture

This describes the platform as built. The requirements are in `MASTER_SPEC.md`; why things are the way they are is
in `DECISIONS.md`.

## Services

| Service | Technology | Role | Ports |
| --- | --- | --- | --- |
| `nginx` | Nginx 1.27 | TLS, security headers, rate limits, routes `/api`, `/ws`, `/admin`, `/static`, the web app | 80, 443 (public) |
| `frontend` | React 18 + TypeScript, built to static files | Web app (English and Kiswahili) | internal |
| `backend` | Django 5.2 + DRF + Channels on Uvicorn (ASGI) | REST API, WebSockets, admin, `/health`, `/readiness`, `/metrics` | 8000 internal |
| `celery-worker`, `celery-beat` | Celery 5.6 | Reports, emails, webhooks, maintenance checks, retention, cleanups | none |
| `gateway` | Python asyncio | Tracker connections, protocol plugins, ACK after persistence | 5027 tcp/udp (Teltonika), 5023 tcp (GT06), 5055 http (generic), 5100-5109 reserved; 8081 internal |
| `processor` | Python asyncio | Live status, trips, stops, idling, overspeed, geofences, alerts, webhook events | 8082 internal |
| `postgres` | PostgreSQL 16 + PostGIS 3 + TimescaleDB 2.17 | Source of truth; Row-Level Security per organization | internal only |
| `redis` | Redis 7 (AOF) | Positions stream, pub/sub, Channels layer, Celery broker | internal only |
| `prometheus`, `grafana` | optional profile `monitoring` | Metrics and dashboards | Grafana on 127.0.0.1:3000 |
| `simulator` | optional profile `sim` | Simulated trackers speaking the real protocols | none |

```mermaid
flowchart LR
  subgraph Internet
    T1[Teltonika FMC920] -- TCP/UDP 5027 --> GW
    T2[GT06 tracker] -- TCP 5023 --> GW
    T3[HTTP / text tracker] -- HTTP 5055, TCP/UDP 5100-5109 --> GW
    B[Browser] -- HTTPS 443 --> NG
  end
  subgraph Docker internal network
    NG[nginx] --> FE[frontend]
    NG -- /api /admin --> BE[backend\nDjango + DRF]
    NG -- /ws --> BE
    GW[gateway] -- "INSERT (then ACK)" --> PG[(PostgreSQL\nPostGIS + TimescaleDB)]
    GW -- XADD positions --> RD[(Redis)]
    RD -- consumer group --> PR[processor]
    PR -- trips, alerts, state --> PG
    PR -- Channels group_send --> RD
    RD -- live updates --> BE
    BE --> PG
    BE -- tasks --> RD
    RD --> CW[celery-worker]
    CB[celery-beat] --> RD
    CW --> PG
    BE -- device_registry pub/sub --> RD
    RD -- registry changes --> GW
  end
```

## Device communication (Teltonika)

```mermaid
sequenceDiagram
  participant D as FMC920
  participant G as gateway
  participant P as PostgreSQL
  participant R as Redis
  D->>G: TCP connect, IMEI (2-byte length + ASCII)
  G->>G: registry lookup (cached; invalidated by pub/sub)
  alt registered and active
    G-->>D: 0x01
  else unknown
    G->>P: unregistered-device inbox
    G-->>D: 0x00, close
  end
  loop every send period
    D->>G: AVL packet (Codec 8 / 8E, CRC-16)
    G->>G: decode, validate, normalize, IO mapping, jump filter
    G->>P: one atomic statement: positions (ON CONFLICT DO NOTHING), live state, last seen
    alt committed
      G-->>D: record count (4 bytes)
      G->>R: XADD positions
    else database error
      G--xD: no ACK, close (tracker keeps and resends)
    end
  end
```

GT06 is the same shape: login packet (BCD IMEI) → login ACK; location packets (no ACK in the protocol); heartbeat
and alarm packets ACKed after storage. Generic HTTP trackers get their format's ACK string as the HTTP response
body after storage, or HTTP 503 when storage fails.

## Data flow

```mermaid
flowchart TD
  A[Raw bytes] --> B[Protocol plugin\nframe, CRC, decode]
  B --> C[RawRecord]
  C --> D[Validation\ncoordinates, time window]
  D --> E[NormalizedLocation\nIO mapping profile per device model]
  E --> F[Annotator\nlate, outlier jump filter]
  F --> G[(positions hypertable)]
  F --> H[(device_state)]
  G --> I[Redis stream 'positions']
  I --> J[processor]
  J --> K[(trips, stops, idle, speed, geofence events)]
  J --> L[(alerts, notifications, webhook_events)]
  J --> M[WebSocket groups\nlive map, notifications]
  G --> N[positions_hourly\ncontinuous aggregate]
  N --> O[Analytics]
  K --> P[Reports]
```

## Live tracking

```mermaid
sequenceDiagram
  participant D as Tracker
  participant G as gateway
  participant R as Redis
  participant P as processor
  participant B as backend (Channels)
  participant W as Browser
  W->>B: POST /api/v1/auth/ws-ticket/ (30 s, single use)
  W->>B: WebSocket /ws/tracking/?ticket=...
  B->>B: join group org.{id} (needs tracking.view_live)
  D->>G: packet
  G->>R: XADD positions (after commit)
  R->>P: XREADGROUP
  P->>P: status (moving, idle, stopped, offline, no GPS), analysis
  P->>R: group_send org.{id}
  R->>B: Channels layer
  B->>W: position update (marker moves; measured about 1 s end to end)
```

## Trip detection

```mermaid
stateDiagram-v2
  [*] --> Stopped
  Stopped --> Moving: ignition on / motion / speed > trip start (per model or vehicle mode)
  Moving --> Idling: ignition on, speed <= stop speed
  Idling --> Moving: speed > trip start
  Idling --> Stopped: ignition off
  Moving --> Stopped: ignition off, or stop threshold held (speed mode)
  Stopped --> [*]
  note right of Moving: distance from odometer when the model has one,\notherwise PostGIS distance between valid points,\nwith the drift radius filter
  note right of Stopped: a trip shorter than the minimum distance\nor duration is dropped
```

Thresholds come from the organization's settings over platform defaults (`apps/settings_app/schema.py`). Late
records (older than what the engine has seen) trigger a recompute of the affected window; results from it are
marked delayed. If records were stored while the stream was lost, the processor reads them from the database
before continuing.

## Alerts

```mermaid
flowchart LR
  S[Signal: overspeed, geofence, offline, crash, ...] --> R{Matching active rules\nscope, schedule, params}
  R -- no --> X[ignored]
  R -- yes --> D{Open alert for\nrule + vehicle + type?}
  D -- yes --> U[occurrences + 1]
  D -- no --> N[new alert]
  U --> C{cooldown passed?}
  N --> C
  C -- no --> Q[stop]
  C -- yes --> P[recipients: roles + users]
  P --> F{per-user channel preference\nand severity}
  F --> Z{quiet hours?\ncritical bypasses}
  Z --> IA[in-app + WebSocket]
  Z --> EM[email queue, retried]
  N --> WH[webhook outbox 'alert.created']
```

Maintenance and SIM-expiry alerts are raised by Celery with the same semantics (`apps/alerts/system.py`).

## Multi-tenant isolation

```mermaid
flowchart TD
  Req[API request] --> Auth[JWT or API key]
  Auth --> Tenant[resolve_tenant:\nX-Organization-Id must be one of the user's memberships]
  Tenant --> Perm[permission check per action]
  Perm --> GUC["SET LOCAL app.current_org = id"]
  GUC --> RLS[PostgreSQL Row-Level Security\non every tenant table]
  RLS --> DB[(rows of that organization only)]
  Tenant -. "API key: its own organization only,\nscopes narrowed to the creator's permissions" .-> Perm
```

Web requests run as the restricted database role `gps_app`, so even a missing filter in code can't read another
tenant's rows. `positions` is reachable only through the `tenant_positions` view; the analytics aggregate only
through `tenant_positions_hourly`. An automated test walks every API route as a user of another organization
(`apps/organizations/tests/test_tenant_isolation.py`).

## Authentication and token refresh

```mermaid
sequenceDiagram
  participant W as Browser
  participant B as backend
  W->>B: POST /auth/login/ (email, password)
  B-->>W: access token (10 min, in memory) + refresh cookie (httpOnly, Secure, SameSite=Strict, path /api/v1/auth/, 7 days)
  W->>B: API calls with Authorization: Bearer access
  B-->>W: 401 when the access token expires
  W->>B: POST /auth/refresh/ (cookie)
  B->>B: rotate: old refresh token revoked; reuse of a revoked one revokes the whole session
  B-->>W: new access token + new refresh cookie
  W->>B: POST /auth/logout/ (session revoked)
```

Failed logins are throttled and lock the account for 15 minutes after 5 failures in 15 minutes. Users see and revoke
their signed-in devices.

## Device onboarding through the UI

```mermaid
flowchart LR
  A[Add tracker wizard] --> B[Pick model\nfrom catalog]
  B --> C[IMEI / identifier\nLuhn check, unique]
  C --> D[Assign vehicle]
  D --> E[Setup instructions\nserver, port, APN filled in]
  E --> F[Connection test\nregistered, connected, first data, GPS fix, readings understood]
  C -. registry pub/sub .-> G[gateway accepts it within seconds]
  U[Unregistered devices inbox] --> C
```

New models with a known protocol are added in the catalog (with an IO mapping editor); simple trackers with their
own JSON or text format use **Tracker formats** (no code); a new binary protocol needs a decoder plugin
(`DEVICE_INTEGRATION.md`).

## Deployment

```mermaid
flowchart TB
  subgraph Host["VPS (Ubuntu 24.04, Docker)"]
    direction TB
    NG[nginx :80 :443] --- FE[frontend]
    NG --- BE[backend]
    GW[gateway :5027 :5023 :5055 :5100-5109]
    BE --- PG[(postgres)]
    BE --- RD[(redis)]
    GW --- PG
    GW --- RD
    PR[processor] --- PG
    PR --- RD
    CW[celery worker/beat] --- PG
    CW --- RD
    PM[prometheus] -.-> BE
    PM -.-> GW
    PM -.-> PR
    GF[grafana 127.0.0.1:3000] --- PM
  end
  Users -- 443 --> NG
  Trackers -- tracker ports --> GW
  PG -- WAL archive, nightly dumps --> S3[(object storage)]
```

Only 80, 443 and the tracker ports are published. PostgreSQL and Redis are never published.
