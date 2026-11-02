# Production deployment

A single VPS with Docker Compose. Read `SECURITY.md` and `BACKUP_RESTORE.md` before going live.

## 1. Server

- Ubuntu 24.04 LTS, 4 vCPU / 8 GB for about 5,000 trackers (see `SCALING.md`), SSD.
- A DNS name (`gps.example.co.tz`) pointing at the server, and optionally a second name for trackers.
- Firewall (ufw or the cloud security group): allow 22 (SSH, from your IPs), 80, 443, 5027 TCP/UDP, 5023, 5055 and
  only the 5100-5109 ports you assign. Nothing else. PostgreSQL and Redis are never published.
- Docker Engine and the Compose plugin from Docker's repository; a non-root deploy user in the `docker` group.
- Unattended security upgrades on.

## 2. Configuration

```bash
git clone <repository> /opt/gps && cd /opt/gps
cp .env.example .env && chmod 600 .env
```

Set at least: `DEBUG=0`, `DJANGO_SECRET_KEY`, `DATABASE_PASSWORD`, `REDIS_PASSWORD`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS=https://gps.example.co.tz`, `FRONTEND_URL`, `TRACKER_SERVER_HOST`, email (`EMAIL_HOST`, ...),
`MAP_TILE_URL` (a tile provider you're licensed to use; the public OpenStreetMap servers are not for production),
`METRICS_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, optionally `SENTRY_DSN`.

## 3. TLS

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d gps.example.co.tz      # before starting nginx, or use the webroot below
```

`nginx/prod/default.conf` expects certificates in `/etc/letsencrypt/live/<domain>/`; edit the `server_name` and
paths. Renewal: `certbot renew --webroot -w /var/www/certbot` from cron, then `docker compose exec nginx nginx -s reload`.
The production config redirects HTTP to HTTPS and sends HSTS, CSP, X-Frame-Options, Referrer-Policy and
X-Content-Type-Options.

## 4. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose exec backend python manage.py create_superadmin you@company.co.tz
docker compose exec backend python manage.py check --deploy
```

The production override runs Django with production settings, read-only root filesystems, `no-new-privileges`,
resource limits and log rotation (20 MB × 5 per service).

With point-in-time recovery (recommended), add `-f docker-compose.backup.yml` and create the pgBackRest stanza
(`BACKUP_RESTORE.md`).

## 5. Monitoring

```bash
echo -n "$METRICS_TOKEN" > monitoring/prometheus/metrics_token
docker compose --profile monitoring up -d prometheus grafana
ssh -L 3000:127.0.0.1:3000 deploy@server        # then open http://localhost:3000
```

See `MONITORING.md` for alert routing.

## 6. After go-live

- Schedule backups and run a restore drill (`BACKUP_RESTORE.md`).
- Confirm trackers connect (Super Admin → System health; **Unregistered devices** for typos).
- Check `docker compose ps` shows every service healthy, and `https://<domain>/readiness`.

## Updating

```bash
cd /opt/gps && git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Migrations run when the backend starts. Trackers reconnect and resend what they buffered while the gateway restarted.

## Network notes

Trackers need a stable address: a static public IP or a DNS name. Behind NAT, forward the tracker ports. Some
operators' APNs block uncommon ports; test with a phone on the same SIM if a tracker never connects. The database
must never be reachable from the Internet: Redis and PostgreSQL hold every organization's data.
