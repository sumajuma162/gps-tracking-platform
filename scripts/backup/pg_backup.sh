#!/usr/bin/env bash
# Nightly logical backup of the platform database (spec A15 "Backup and restore").
#
# Writes a compressed custom-format dump (pg_dump -Fc) with a SHA-256, keeps 7 daily, 4 weekly (Sunday) and
# 6 monthly (1st) copies locally, and copies each new dump off the server when BACKUP_REMOTE is set (an rclone
# remote such as "s3:my-bucket/gps"). Point-in-time recovery uses pgBackRest WAL archiving on top of this
# (docker/pgbackrest/, docs/BACKUP_RESTORE.md).
#
# Environment: PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE (libpq), BACKUP_DIR (default ./backups),
# BACKUP_REMOTE (optional). Run inside the postgres container or anywhere pg_dump can reach the database.
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DB="${PGDATABASE:-gps}"
NOW="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly" "$BACKUP_DIR/monthly"
FILE="$BACKUP_DIR/daily/${DB}_${NOW}.dump"

pg_dump --format=custom --compress=6 --no-owner --dbname="$DB" --file="$FILE.partial"
mv "$FILE.partial" "$FILE"
sha256sum "$FILE" > "$FILE.sha256"
echo "backup written: $FILE ($(du -h "$FILE" | cut -f1))"

if [ "$(date -u +%u)" = "7" ]; then cp "$FILE" "$FILE.sha256" "$BACKUP_DIR/weekly/"; fi
if [ "$(date -u +%d)" = "01" ]; then cp "$FILE" "$FILE.sha256" "$BACKUP_DIR/monthly/"; fi

prune() {  # keep the newest N dumps in a folder
  local dir="$1" keep="$2"
  ls -1t "$dir"/*.dump 2>/dev/null | tail -n +"$((keep + 1))" | while read -r old; do rm -f "$old" "$old.sha256"; done
}
prune "$BACKUP_DIR/daily" 7
prune "$BACKUP_DIR/weekly" 4
prune "$BACKUP_DIR/monthly" 6

if [ -n "${BACKUP_REMOTE:-}" ]; then
  rclone copy "$BACKUP_DIR" "$BACKUP_REMOTE" --include "*.dump" --include "*.sha256"
  echo "copied to $BACKUP_REMOTE"
fi
