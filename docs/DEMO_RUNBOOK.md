# Backend demo runbook

## Population-wide historical replay (recommended demo)

This is an accelerated **historical replay**, not live monitoring. The
digest-pinned manifest selects an already prepared source window and current
reference/model. Reset performs the bounded warmup immediately at the replay
start; future source rows become visible only as virtual time reaches them.
Canonical inference remains on the frozen object × 6h schedule.

Start the replay API in terminal 1 from the repository root:

```bash
TGOAT_RUNTIME_MODE=replay \
TGOAT_REPLAY_MANIFEST="$PWD/ml/data/demo/replay/replay_manifest.json" \
TGOAT_REPLAY_SPEED=1800 \
TGOAT_REPLAY_AUTO_START=false \
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in terminal 2:

```bash
cd frontend
VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173
```

Log in with a seeded dispatcher account from `docs/API_CONTRACT.md`. The
dashboard identifies the mode as `DEMO · историческое воспроизведение` and
shows virtual time, speed, progress and state. Start/Pause/Resume/Reset and
600×/1800×/3600× speed controls are available only when the API reports replay
mode. A reset creates a fresh isolated demo session and returns to the warmed,
not-yet-running start point. It does not delete reference or unrelated
production-style records. A full bounded walkthrough and measured load result
are in `REPLAY_LOAD_SMOKE.md`.

### User-facing scenario walkthroughs

Open `http://127.0.0.1:5173/scenarios` after login. The smoke/fire, flooding,
and КД АВ/access cards are navigation aids to registered channel/object
records, not incident simulators and not separate probability estimates. They
label the missing fire/flood ground truth, weather/camera/ODS integrations and
human verification explicitly. Channel 120578 is the organizer-confirmed КД АВ
example; its eight dictionary states are shown as reference labels only. The
channel's registered `тревожное` history remains authoritative and is not
rewritten using the current state dictionary. An in-scope technician or
dispatcher may leave a review; only dispatchers may record outcomes. These
controls require a corresponding operational prediction to be `available`;
the UI and API reject writes for degraded/no-score predictions.

To run the ordinary live-clock application, omit replay variables or set
`TGOAT_RUNTIME_MODE=normal`; replay mutations are then explicitly unavailable.
Normal mode still requires a current, complete canonical feature-state source
and the usual operational worker. It does not substitute replay data.

The single-object `runtime_window.json` instructions below are retained as a
legacy bounded provider/bootstrap check, not the recommended user demo.

The repository does not check in generated feature-state SQLite databases.
Fresh startup reports `degraded`/`no_prediction` until state is prepared; it
does not inject fake scores. A bounded, digest-pinned source fixture and
reproducible bootstrap command are checked in.

For the historical object-5343 demo, first create a fresh state database:

```bash
.venv/bin/python -m scripts.bootstrap_demo_feature_state \
  --output /tmp/tgoat-demo-feature-state.sqlite
```

Then use that path for `TGOAT_FEATURE_STATE_DB` and configure persistent
prediction/recommendation/cycle paths as described in `BACKEND_RUNBOOK.md`.
The fixture snapshot is June 30, 2026; because it is stale relative to the
current date, it proves bounded model serving but is not a live forecast. No
historical full rebuild or model training occurs.

The safe backend-only smoke scenario is fixture object 5343 through:

1. `GET /api/v1/operational/dashboard`;
2. `GET /api/v1/operational/objects/5343`;
3. `GET /api/v1/objects/5343/risk?snapshot_ts=2026-06-30T20%3A59%3A59Z`;
4. history/cards/recommendations reads.

The explicit snapshot is the manifest's canonical `2026-06-30T23:59:59+03:00`
in UTC. A bare `GET .../risk` intentionally asks for the current canonical
snapshot and will fail closed as degraded because this fixture is historical;
it must not be described as a failed bounded-fixture inference.

Without feature state, expected states are explicit `no_cycle`,
`no_prediction`, and `degraded` with null score/flag. With valid bounded state,
the demo cycle produces an available ML prediction and, for a positive
prediction, a card/recommendation/one in-web notification. The object detail UI
allows an in-scope technician or dispatcher to save a review; only an
authorized dispatcher may add an outcome. Both link to that prediction. Exact
probabilities are fixture/model-dependent.
