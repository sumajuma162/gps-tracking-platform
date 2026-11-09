# Security

## Tenancy

- Every organization-owned row carries `organization_id`. Web requests run as the restricted role `gps_app` under
  PostgreSQL Row-Level Security keyed on `app.current_org` (set per transaction), on top of application filters.
- `X-Organization-Id` must be one of the user's active memberships; otherwise 404 (other tenants' objects look
  missing, never "forbidden").
- An automated test walks every API route as a member of another organization and checks nothing leaks or changes.

## Accounts and sessions

- Passwords hashed with Argon2 (PBKDF2 accepted for upgrade); Django password validators.
- Login throttled (10/min per IP by default); 5 failures in 15 minutes lock the account for 15 minutes.
- Access tokens 10 minutes, in memory only. Refresh tokens 7 days in an httpOnly, Secure, SameSite=Strict cookie
  scoped to `/api/v1/auth/`, rotated on each use; reusing an old one revokes the session and is audited.
- Users see and revoke their signed-in devices; password reset links expire in 24 h and end other sessions.
- WebSockets use single-use 30-second tickets, never tokens in URLs.

## Authorization

Roles (Super Admin, Organization Admin, Fleet Manager, Monitoring Officer, Viewer, Auditor, Driver, and custom
roles) grant permission codenames checked on every action. Exports, commands and output control have their own
permissions; output-control commands also need hardware verification and confirmation.

## API keys and webhooks

- API keys: only SHA-256 hashes stored; shown once; scopes limited to the creator's permissions and narrowed again
  on every request; never valid on account, platform or WebSocket-ticket routes; expiry and revocation; audited.
- Webhooks: HMAC-SHA256 signatures with timestamps; https to public addresses only (no loopback, private, link-local
  or cloud-metadata addresses; redirects not followed) against server-side request forgery.
- HTTP trackers: per-device tokens, hashed; or identifiers accepted only for registered devices.

## Network

- Only 80/443 and tracker ports are published. PostgreSQL and Redis live on the internal Docker network; Redis
  requires a password. `/metrics` isn't routed by Nginx and can require a bearer token.
- Nginx: TLS 1.2+, HSTS, a Content-Security-Policy, X-Frame-Options DENY, X-Content-Type-Options, Referrer-Policy,
  request rate limits on `/api/`, body size limits.
- Gateway: packet size limits, handshake and idle timeouts, per-IP and global connection limits; malformed input
  closes only that connection and is counted.

## Containers

Non-root users, read-only root filesystems where possible, `no-new-privileges`, resource limits (production
override). Secrets only in `.env` (never committed; `.env.example` has placeholders).

## Audit

Append-only audit log of sign-ins, user and role changes, data changes, exports, commands, API keys, webhooks and
settings (old and new values), with IP and user agent. Secrets are redacted.

## Personal data

Location history is personal data about drivers. Organizations set their own retention (`retention_days`); share
links expire; exports are permission-controlled and audited; webhook and forwarder payloads include only listed
fields. Sentry is configured not to send personal data.

## Dependency and image scanning

CI runs `pip-audit` on every Python requirements file and `npm audit --audit-level=high`, and scans the built
images with Trivy (fails on fixable HIGH/CRITICAL).

Known and accepted (2026-11-10): `npm audit --audit-level=high` passes; 9 moderate advisory instances remain in
React Router 6 and the nested `minimatch` dependency used by TypeScript ESLint. The app doesn't use server-side
rendering; user-supplied redirect targets go through `safeNext`, which rejects backslashes, whitespace, control
characters, schemes and `//`. Vitest was upgraded to 5.1.0; Vite and `follow-redirects` were updated to patched
releases. Plan: upgrade to React Router 7 and resolve the remaining transitive `minimatch` advisory in a separate
dependency-maintenance change.

## Reporting a vulnerability

Email the platform operator's security contact (set it in your deployment's README); don't open a public issue.
