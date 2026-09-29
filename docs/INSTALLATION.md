# Installation

## Supported runtime

Hackathon deployment target: Linux server, Python 3.12+, Node.js for frontend build, PostgreSQL 12+ (the provided compose file uses PostgreSQL 16), and Docker Compose for the reproducible stack.

## Python-only development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Normal operational runtime is fail-closed PostgreSQL. Configure at minimum:

```bash
export TGOAT_RUNTIME_MODE=normal
export TGOAT_OPERATIONAL_STORAGE=postgres
export TGOAT_OPERATIONAL_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DB'
export DEMO_AUTH_TOKEN_SECRET='replace-with-random-secret'
```

Start API:

```bash
.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

Historical Replay is a separate isolated mode and retains its SQLite session storage. `sqlite-test` is explicit test-only storage; normal operational startup must not silently fall back to SQLite or RAM.

## Reproducible Docker deployment

See `docs/DEPLOYMENT.md` and `docker/.env.example`.
