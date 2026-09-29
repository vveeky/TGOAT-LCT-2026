# Backend runbook

## Start API

For the population-wide historical demo, use the replay commands in
`DEMO_RUNBOOK.md`. In replay mode the clock used for prediction freshness is
virtual; current-time freshness is not used. The selected manifest is
digest-checked against the source, reference snapshot and frozen artifact.
Warmup is limited to source timestamps at or before `replay_start`; later rows
are revealed incrementally. Each 6h virtual boundary invokes the existing
operational cycle for the runtime-derived population. Replay storage is
isolated per session and reset discards only that session. Do not point replay
at a normal deployment's persistent production databases.

For ordinary serving, keep `TGOAT_RUNTIME_MODE=normal` (the default): the
clock is wall time, freshness is measured against the current canonical
endpoint, and a current complete feature-state source plus the normal worker
are required. Historical replay is never treated as current state. Replay
control mutations are disabled in normal mode; in replay mode they require an
authorized district or central dispatcher.

From the repository root:

```bash
export TGOAT_RUNTIME_MODE=normal
export TGOAT_OPERATIONAL_STORAGE=postgres
export TGOAT_OPERATIONAL_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DB'
export DEMO_AUTH_TOKEN_SECRET='replace-with-random-secret'
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

The lifecycle creates one frozen ML service and the operational read model.
Without a configured feature-state DB, `/api/v1/objects/{id}/risk` returns an
explicit `degraded` response and operational read endpoints show
`no_prediction`; no mock score is substituted.

## API exchange formats

API responses are JSON by default. For a bounded XML read-side exchange of the
same authenticated, role-scoped operational snapshot, request
`GET /api/v1/operational/dashboard` with `Accept: application/xml` (or
`text/xml`). The XML root is `operational_dashboard`; response fields retain
their API names, lists are wrapped by the field name and contain `<item>`
elements, booleans are `true`/`false`, and nulls use `nil="true"`. The response
includes `Vary: Accept`. This is a generic demo representation, not a
partner-specific XML schema or a live external-source integration.

## Bounded demo state

For the checked-in object-5343 historical fixture, prepare a new SQLite state
outside tracked source directories (the command refuses to overwrite it):

```bash
.venv/bin/python -m scripts.bootstrap_demo_feature_state \
  --output /tmp/tgoat-demo-feature-state.sqlite
```

This validates the digest-pinned source and current references, then calls the
existing hourly feature hydrator. It reads only the bounded fixture; it does
not rebuild Phase 1 or annual data. The manifest's canonical snapshot is
2026-06-30 23:59 Europe/Moscow, so this demonstrates historical runtime
availability and must not be represented as a live current forecast.
To query that exact bounded snapshot, pass `snapshot_ts=2026-06-30T20:59:59Z`
to `GET /api/v1/objects/5343/risk` (or use operational prediction history
after running the bounded cycle). A request without `snapshot_ts` evaluates
current freshness and is expected to be degraded for this historical fixture.

## Persistence configuration

- `TGOAT_OPERATIONAL_STORAGE=postgres` and
  `TGOAT_OPERATIONAL_DATABASE_URL`: required for normal operational mode.
  PostgreSQL stores prediction/card history, workflow, idempotency receipts,
  notifications and audit through the shared Unit of Work. Startup fails
  closed if the database is unavailable; normal mode does not fall back to
  process memory or SQLite.
- `TGOAT_FEATURE_STATE_DB`: separate feature-serving SQLite input. If absent
  or unavailable, the feature provider is degraded and inference does not
  issue a normal prediction. This database is not the operational workflow
  store.
- Historical Replay remains a separate resettable SQLite demo-session; use
  the commands in `DEMO_RUNBOOK.md`. It does not replace normal PostgreSQL
  persistence or audit retention.

## Worker

Run one dedicated worker process:

```bash
TGOAT_RUNTIME_MODE=normal \
TGOAT_OPERATIONAL_STORAGE=postgres \
TGOAT_OPERATIONAL_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DB' \
TGOAT_FEATURE_STATE_DB=/path/to/feature-state.sqlite \
.venv/bin/python -m backend.operational_worker
```

Canonical endpoints are 05:59:59, 11:59:59, 17:59:59 and 23:59:59 in
Europe/Moscow. Existing installations catch up every missed endpoint oldest
first. Bootstrap runs only the latest eligible endpoint and never backfills
historical years. A cycle is `completed` only after all expected 78 objects
have terminal available/degraded results. Reference/provider/model failure is
recorded as failed or unavailable; it is never a negative prediction.

Cycle identity and prediction/card identities make safe retries idempotent.
Cycle state is stored with the other normal operational records in PostgreSQL
and is visible through the operational dashboard. The Compose deployment and
worker supervision instructions are in `docs/DEPLOYMENT.md`.

To write one bounded operational demo cycle after bootstrap, set the required
PostgreSQL connection and `TGOAT_FEATURE_STATE_DB`, then run:

```bash
.venv/bin/python -m scripts.run_operational_demo_cycle
```

The command selects the manifest object unless `TGOAT_OPERATIONAL_OBJECT_IDS`
is explicitly set. The API process must use the same persistent DB paths to
read the resulting prediction, decision/outcome and notification records.
