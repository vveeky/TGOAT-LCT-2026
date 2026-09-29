# Current verification status

Latest source-branch stabilization, 2026-09-29:

- Backend full suite: **378 passed**; focused PostgreSQL regression: **6 passed**.
- Frontend: **80/80 Vitest** and **52/52 Node tests**; lint and production build passed.
- `git diff --check`: passed.
- Browser E2E passed for Signal → Request → assignment → technician result → dispatcher acceptance → close/resolution.
- Historical Replay and simulation smoke passed.
- Local 20-client read-heavy smoke, local TLS, test LDAP, and backup/restore checks were exercised. These are bounded local results, not a production capacity guarantee or acceptance against Customer infrastructure.

Normal operations use PostgreSQL for durable workflow and append-only audit; Historical Replay uses an isolated resettable SQLite demo-session. The frozen ML output is generic 24-hour proxy alarm/anomaly risk, not a validated probability of a physical incident. Live Customer SMVU and production LDAP/AD connections have not been accepted.
