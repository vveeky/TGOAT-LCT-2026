# Current limitations

These are remaining boundaries of the demo/runtime, not statements that the
operational ML integration is absent. See `BACKEND_RUNBOOK.md`
for current behavior and `DEMO_RUNBOOK.md` for the bounded fixture path.

## Data and model

- There is no confirmed physical-failure ground truth, verified repair outcome
  history, or official incident target. The frozen model predicts the accepted
  proxy target; it does not prove physical failure.
- The older single-object bounded fixture ends 2026-06-30; the recommended
  population-wide replay is a selected historical window, 2026-05-05 through
  2026-05-07. Neither is current live telemetry. Replay labels and controls
  make that distinction explicit; normal runtime rejects stale feature state
  rather than silently presenting it as current.
- Real coordinates/topology are not provided. The map deterministically places
  actual object IDs/statuses at synthetic demo coordinates; displayed
  probabilities belong to those objects, not to the synthetic locations,
  district membership or sensor/channel cells.
- `Europe/Moscow` remains the configured interpretation for naive event
  timestamps. Event timestamps represent registration time; network delay and
  physical occurrence time are not reconstructed.
- Whole calendar days with no records are ambiguous, not organizer-confirmed
  technical data loss. The modeling policy is conservative and explicitly
  documented in the Phase 1 report.

## Demo infrastructure

- Normal operational persistence uses PostgreSQL. Predictions, workflow,
  notifications and audit are durable operational records; critical domain
  mutations and their audit records share one Unit of Work/transaction.
  Normal mode fails closed when PostgreSQL is unavailable and does not
  silently fall back to RAM or SQLite.
- Historical Replay is a separate resettable SQLite demo-session. Its state
  is intentionally scoped to the replay session and is not production audit
  retention.
- A local 20-client read-heavy smoke, along with local TLS, test-LDAP and
  backup/restore checks, has been exercised. These are bounded local results,
  not a production capacity guarantee, customer-host acceptance, HA/SLO
  evidence, or a measured production RTO. A separate PostgreSQL regression
  covers workflow concurrency and idempotency.
- Demo roles and server-side object-scope checks are implemented. Local test
  LDAP group mapping was exercised, but the Customer's production LDAP/AD
  endpoint, credentials and role mapping have not been accepted. No live
  Customer SMVU or external maintenance/helpdesk connection is claimed.
- Legacy/mock APIs and compatibility stores remain in selected prototype
  routes. Their process-local demo data is not the source of normal
  operational predictions, workflow, notifications or audit.
- Actionable operational high-risk forecasts create persistent in-web
  notifications; the frontend exposes the list, unread state and mark-read
  action with legacy mock items explicitly labeled. SMS, push and external
  delivery are intentionally not part of the demo.
- The operational UI shows the latest available backend projection. It does
  not imply a live scheduled cycle unless the worker is running and its cycle
  status is current.

## Legacy compatibility

- Legacy dashboard, review, maintenance and notification mock APIs remain for
  existing prototype screens/consumers. Their `mock` scores are not frozen ML
  probabilities and are not the source for the operational dashboard/map risk.
- Local draft records and mock external integrations are not evidence of a
  completed physical inspection or repair.
