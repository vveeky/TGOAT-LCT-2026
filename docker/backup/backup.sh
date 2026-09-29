#!/usr/bin/env bash
set -euo pipefail
# Run with an authenticated PostgreSQL client and pg_dump >= server version.
: "${DATABASE_URL:?Set DATABASE_URL to authorized PostgreSQL URL}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
file="$BACKUP_DIR/tgoat-$(date -u +%Y%m%dT%H%M%SZ).dump"
tmp="${file}.partial"
trap 'rm -f "$tmp"' EXIT
pg_dump --format=custom --no-owner --dbname="$DATABASE_URL" --file="$tmp"
pg_restore --list "$tmp" >/dev/null
chmod 600 "$tmp"
mv "$tmp" "$file"
sha256sum "$file" >"$file.sha256"
find "$BACKUP_DIR" -maxdepth 1 -name 'tgoat-*.dump' -type f -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name 'tgoat-*.dump.sha256' -type f -mtime "+$RETENTION_DAYS" -delete
trap - EXIT
printf 'Backup verified: %s\n' "$file"
