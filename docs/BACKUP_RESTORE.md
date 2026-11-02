# Backup and restore

Two layers:

1. **Nightly logical dumps** (`scripts/backup/pg_backup.sh`): `pg_dump -Fc` with a SHA-256, kept 7 daily, 4 weekly
   and 6 monthly, copied off the server with rclone when `BACKUP_REMOTE` is set.
2. **Point-in-time recovery** with pgBackRest WAL archiving to S3-compatible storage
   (`docker-compose.backup.yml`, `docker/pgbackrest/pgbackrest.conf`): weekly full, daily differential, WAL every
   5 minutes at most, 4 full backups retained.

Also back up `.env` (in a password manager or secret store, not next to the dumps) and the `media` volume (exports
are temporary, uploaded files are not).

## Nightly dump

```bash
# /etc/cron.d/gps-backup on the host
15 2 * * * deploy cd /opt/gps && docker compose exec -T postgres bash -c \
  'PGUSER=$POSTGRES_USER PGDATABASE=$POSTGRES_DB BACKUP_DIR=/home/postgres/backups /scripts/pg_backup.sh' \
  >> /var/log/gps-backup.log 2>&1
```

(Mount `scripts/backup` at `/scripts` and a backup directory into the postgres container, or run the script from the
host with `PGHOST`/`PGPASSWORD` set and `pg_dump` 16 installed.)

## Restore drill (monthly)

```bash
PGHOST=... PGUSER=gps PGPASSWORD=... scripts/backup/restore_drill.sh backups/daily/gps_<time>.dump
```

It checks the checksum, restores into a scratch database with TimescaleDB's pre/post-restore hooks, compares row
counts of the key tables with the live database and lists the restored hypertables; exit code 0 only when everything
matches. **Verified on 2026-10-04** against a database with 24 h of simulated history put through the gateway
(8,640 positions): checksum ok, 12 of 12 tables identical, both hypertables restored.

## Full restore (disaster)

1. Stop writers: `docker compose stop gateway processor celery-worker celery-beat backend`.
2. Restore the dump into a fresh database:
   ```bash
   psql -d postgres -c "DROP DATABASE gps" -c "CREATE DATABASE gps OWNER gps"
   psql -d gps -c "CREATE EXTENSION timescaledb" -c "SELECT timescaledb_pre_restore()"
   pg_restore --no-owner -d gps backups/daily/gps_<time>.dump
   psql -d gps -c "SELECT timescaledb_post_restore()"
   ```
3. Start everything: `docker compose up -d`. Trackers resend what they buffered while the gateway was down; records
   already in the restored database are deduplicated.

## Point-in-time recovery

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.backup.yml up -d
docker compose exec -u postgres postgres pgbackrest --stanza=gps stanza-create
docker compose exec -u postgres postgres pgbackrest --stanza=gps --type=full backup      # weekly (cron)
docker compose exec -u postgres postgres pgbackrest --stanza=gps --type=diff backup      # daily (cron)
# restore to a moment: stop postgres, then
pgbackrest --stanza=gps --delta --type=time "--target=2026-10-04 09:30:00+03" restore
```

**Not verified in the build environment** (no Docker or object storage there). Run a point-in-time restore on staging
before relying on it.

## Targets

Recovery point: at most 5 minutes with WAL archiving (24 h with dumps only). Recovery time: about 30 minutes for a
50 GB database on SSD (measure yours in the drill).
