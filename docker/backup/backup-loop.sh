#!/usr/bin/env sh
set -eu
: "${DATABASE_URL:?Set DATABASE_URL}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
while true; do
  /backup/backup.sh || echo "backup failed at $(date -u +%FT%TZ)" >&2
  sleep "$INTERVAL"
done
