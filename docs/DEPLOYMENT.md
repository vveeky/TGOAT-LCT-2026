# Deployment

The reproducible deployment is defined by `docker/compose.yml`.

Core services:

- PostgreSQL operational database;
- FastAPI backend;
- nginx HTTPS frontend/reverse proxy;
- optional simulated telemetry feed;
- optional city-weather feed;
- optional test LDAP directory;
- optional PostgreSQL backup loop.

Quick start:

```bash
cp docker/.env.example docker/.env
# Fill strong PostgreSQL/auth secrets and TGOAT_CERT_DIR.
cd docker
docker compose --env-file .env -f compose.yml up -d --build postgres backend web
```

The public interface is HTTPS. nginx permits TLS 1.2/1.3 and redirects HTTP to HTTPS. Private TLS keys are intentionally not stored in the project.

For operational procedures see `docs/BACKEND_RUNBOOK.md`; for backup/restore see `docs/BACKUP_RESTORE.md`; for the measured replay load smoke see `docs/REPLAY_LOAD_SMOKE.md`.

No protected customer endpoint is embedded. Simulator/test adapters prove the integration boundary without claiming connection to real customer systems.
