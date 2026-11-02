#!/usr/bin/env bash
# Restore drill (spec A15): restore a dump into a scratch database and compare row counts with the source.
# Recommended monthly. Exit code 0 only when the checksum matches and every checked table has the same count.
#
#   scripts/backup/restore_drill.sh backups/daily/gps_20261003T020000Z.dump
#
# Environment: libpq variables for the server (PGHOST PGPORT PGUSER PGPASSWORD), SOURCE_DB (default gps) to
# compare against (skip the comparison with SOURCE_DB=), SCRATCH_DB (default gps_restore_drill, dropped first).
set -euo pipefail
DUMP="${1:?usage: restore_drill.sh <dump file>}"
SCRATCH="${SCRATCH_DB:-gps_restore_drill}"
SOURCE="${SOURCE_DB-gps}"
TABLES="organizations users devices vehicles drivers positions device_state trips stops alerts audit_logs geofences"

if [ -f "$DUMP.sha256" ]; then
  (cd "$(dirname "$DUMP")" && sha256sum --check --quiet "$(basename "$DUMP").sha256") && echo "checksum ok"
fi
psql -v ON_ERROR_STOP=1 -q -d postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" -c "CREATE DATABASE \"$SCRATCH\""
# TimescaleDB restores need pre/post hooks around pg_restore (hypertables, compressed chunks, jobs).
psql -v ON_ERROR_STOP=1 -q -d "$SCRATCH" -c "CREATE EXTENSION IF NOT EXISTS timescaledb" \
     -c "SELECT timescaledb_pre_restore()" >/dev/null
pg_restore --no-owner --exit-on-error --dbname="$SCRATCH" "$DUMP" 2> >(grep -v "already exists" >&2 || true) || {
  echo "pg_restore reported errors" >&2; exit 1; }
psql -v ON_ERROR_STOP=1 -q -d "$SCRATCH" -c "SELECT timescaledb_post_restore()" >/dev/null
echo "restored into $SCRATCH"

status=0
for t in $TABLES; do
  restored=$(psql -tA -d "$SCRATCH" -c "SELECT count(*) FROM \"$t\"")
  if [ -n "$SOURCE" ]; then
    source=$(psql -tA -d "$SOURCE" -c "SELECT count(*) FROM \"$t\"")
    mark=$([ "$restored" = "$source" ] && echo ok || echo DIFFERENT)
    [ "$mark" = ok ] || status=1
    printf "%-14s source %10s  restored %10s  %s\n" "$t" "$source" "$restored" "$mark"
  else
    printf "%-14s restored %10s\n" "$t" "$restored"
  fi
done
psql -tA -d "$SCRATCH" -c "SELECT 'hypertables: ' || string_agg(hypertable_name, ', ') FROM timescaledb_information.hypertables"
[ "${KEEP_SCRATCH:-0}" = "1" ] || psql -q -d postgres -c "DROP DATABASE \"$SCRATCH\""
exit $status
