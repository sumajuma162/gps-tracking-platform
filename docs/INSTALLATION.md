# Installation

For a development machine see `DEVELOPMENT.md` (or `WINDOWS_DEVELOPMENT.md`); for a server see
`PRODUCTION_DEPLOYMENT.md`. This page covers what both share.

## Requirements

| | Minimum | Recommended (5,000 trackers) |
| --- | --- | --- |
| CPU / RAM | 2 vCPU, 4 GB | 4 vCPU, 8 GB |
| Disk | 20 GB SSD | 100 GB SSD + object storage for backups |
| Software | Docker Engine 24+ with Compose v2 (Linux), or Docker Desktop with WSL2 (Windows 11) | |
| Network | for real trackers: a public IP or DNS name and the tracker ports open (below) | |

## Steps

```bash
git clone <repository> gps-tracking-platform && cd gps-tracking-platform
cp .env.example .env
```

Edit `.env`. Required:

| Variable | What to set |
| --- | --- |
| `DJANGO_SECRET_KEY` | 50+ random characters (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `DATABASE_PASSWORD`, `REDIS_PASSWORD` | strong random values |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `FRONTEND_URL` | your hostname (`localhost` for development) |
| `TRACKER_SERVER_HOST` | the hostname or IP trackers connect to (shown in setup instructions) |
| `DEBUG` | `1` only for development (needed by `seed_demo`), `0` in production |

Then:

```bash
docker compose up --build -d
docker compose ps                 # every service "healthy" after about a minute
curl http://localhost/readiness   # {"status": "ready", ...}: database, migrations, Redis
```

Migrations run automatically when the backend starts. Then either:

- **Development / demo:** `docker compose exec backend python manage.py seed_demo` (refuses with `DEBUG=0`), then
  generate a week of history through the real gateway:
  `docker compose --profile sim run --rm simulator --scenario normal_day --backfill-hours 168`.
- **Production:** `docker compose exec backend python manage.py create_superadmin you@company.co.tz` (asks for a
  password), sign in, create the organization and its administrator.

Open `http://localhost` (or your domain) and sign in.

## Ports

| Port | Who connects | Open it to |
| --- | --- | --- |
| 80, 443 | browsers | everyone (443 with TLS in production) |
| 5027 TCP and UDP | Teltonika trackers | trackers (the Internet, or the operator's APN range) |
| 5023 TCP | GT06 trackers | trackers |
| 5055 TCP | HTTP trackers (generic JSON formats) | trackers |
| 5100-5109 TCP/UDP | listener ports assigned in the UI | trackers, only the ones you use |
| 5432, 6379, 8000, 8081, 8082 | internal only | **nobody**: never publish PostgreSQL or Redis |

Behind NAT, forward the tracker ports and 443 to the server. On a cloud VPS, allow them in the security group.
A mobile tracker needs a SIM with a data plan; its APN must allow outbound connections to your server.

## Upgrading

```bash
git pull
docker compose build
docker compose up -d          # migrations run on backend start
```

Take a backup first (`BACKUP_RESTORE.md`). Trackers reconnect on their own; records they hold are resent.
