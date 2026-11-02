# API

REST over HTTPS at `/api/v1/`, JSON, documented live with OpenAPI 3 at `/api/docs/` (sign-in required when
`DEBUG=0`) and `/api/v1/schema/`. The schema is generated from the code and validated in CI with no warnings.

## Authentication

- **Users:** `POST /api/v1/auth/login/` → `{"access": ...}` and an httpOnly refresh cookie. Send
  `Authorization: Bearer <access>`; refresh with `POST /api/v1/auth/refresh/`. Access tokens last 10 minutes.
- **Organization:** send `X-Organization-Id: <id>` on organization routes when the user belongs to several.
- **API keys** (Integrations page, `integrations.manage`): `Authorization: Api-Key gpk_...` or `X-Api-Key`.
  Organization routes only, limited to the key's scopes and to what its creator still holds. Never accepted on
  account, platform or WebSocket-ticket routes.
- **WebSockets:** `POST /api/v1/auth/ws-ticket/` (single use, 30 s), then connect to `/ws/tracking/`,
  `/ws/notifications/` or `/ws/devices/<id>/diagnostics/` with `?ticket=`.

## Conventions

- Pagination: `?page=` and `?page_size=` (max 200); responses `{count, next, previous, results}`.
- Search and ordering on list routes: `?search=`, `?ordering=`.
- Errors: `{"error": {"code": "...", "message": "...", "details": {...}}}` with the HTTP status; validation errors
  list fields in `details`.
- Times are ISO 8601 with time zone; distances in km, speeds in km/h (the UI converts units).
- Rate limits: 1200 requests/min per user; login 10/min, refresh 30/min, password endpoints 5/min (configurable).

## Main resources

| Area | Routes |
| --- | --- |
| Account | `auth/login`, `refresh`, `logout`, `me`, `password/*`, `sessions/*`, `ws-ticket` |
| Organization admin | `organizations`, `users`, `roles`, `audit-logs` (`?q=&action=&user=&resource_type=&resource_id=&ip=&since=&until=`, `facets/`) |
| Fleet | `vehicles`, `vehicle-groups`, `drivers`, `sims`, `devices` (`assign`, `unassign`, `enable`, `disable`, `setup-instructions`, `rotate-token`, `import`) |
| Device catalog (Super Admin) | `device-catalog/manufacturers`, `protocols`, `models` (`clone`), `io-profiles`, `listener-ports`, `generic-formats` (`test-parse`) |
| Tracking | `tracking/live`, `dashboard/summary`, `tracking/history` (`export?type=csv|gpx|kml`), `settings/map` |
| Analysis | `trips`, `stops`, `geofences`, `geofence-events` |
| Alerts | `alert-rules`, `alerts` (`acknowledge`, `resolve`), `notifications`, `notification-preferences`, `commands` |
| Reports | `reports/` (catalog), `reports/<name>/` (14 reports, `?format=csv|xlsx|pdf`), `report-jobs`, `report-schedules` |
| Driver portal | `my-vehicle` (own assigned vehicle, live state, trips of 7 days) |
| Other | `share-links`, `search`, `analytics`, `maintenance/schedules`, `maintenance/records`, `webhooks` (`test`, `deliveries`, `events`), `api-keys` (`revoke`), `settings/organization`, `settings/platform` |
| Platform | `platform/*` (unregistered devices, audit, system health) |

## Webhooks

`POST` to your URL with JSON `{"id", "event", "organization_id", "occurred_at", "data"}` for `alert.created`,
`trip.completed`, `geofence.event` and `device.offline`. Verify the signature:

```python
import hashlib, hmac, time

def verify(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > tolerance:
        return False                                   # replayed or very late
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

Answer 2xx within 10 s. 5xx, 408, 429 and timeouts are retried after 1 min, 5 min, 30 min, 2 h and 6 h; other 4xx are
final. Use `X-GPS-Delivery` to ignore repeats.

## Tracker ingestion over HTTP (generic formats)

`POST http://<host>:5055/<format slug>` with the tracker's JSON (or GET with parameters), authenticated with the
device token (`Authorization: Bearer`, `X-Device-Token` or `token=`) unless the format allows registered identifiers.
`200` with the format's ACK means stored; `401` unknown device or token; `400` names the field that didn't parse;
`413` too large; `429` rate limited; `503` storage unavailable (retry).
