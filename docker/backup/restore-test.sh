#!/usr/bin/env bash
set -euo pipefail
: "${RESTORE_TEST_DATABASE_URL:?Must reference a disposable EMPTY PostgreSQL database}"
: "${BACKUP_FILE:?Absolute path to verified backup archive required}"
if [[ ! -f "$BACKUP_FILE" ]]; then echo 'Backup missing' >&2; exit 2; fi
sha256sum --check "$BACKUP_FILE.sha256"
start=$(date +%s)
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$RESTORE_TEST_DATABASE_URL" "$BACKUP_FILE"
psql "$RESTORE_TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -Atqc 'SELECT count(*) FROM schema_migrations;'
psql "$RESTORE_TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -Atqc 'SELECT count(*) FROM audit_events;'
psql "$RESTORE_TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -Atqc 'SELECT count(*) FROM maintenance_requests;'
echo "Restore elapsed seconds: $(($(date +%s)-start))"
