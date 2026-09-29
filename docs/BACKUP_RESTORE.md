# Backup and restore

The optional `backup` compose profile performs PostgreSQL `pg_dump` in custom format, validates the archive listing, stores a SHA-256 sidecar and applies retention.

Start automatic backups:

```bash
cd docker
docker compose --env-file .env -f compose.yml --profile backups up -d --build backup
```

Restore testing must target a disposable database. The restore script verifies checksum, restores the archive and checks key schema objects before reporting measured duration.

The TЗ requires recovery within 4 hours. The project supplies the mechanism and measurement path; a real deployment restore measurement is required before claiming the RTO as accepted. Copying a live PostgreSQL data directory is not used as the backup mechanism.
