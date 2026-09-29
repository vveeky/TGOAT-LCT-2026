# Replay load smoke

## Refreshed final-tree result after SQLite write serialization — 2026-09-25

After a rare shared-memory SQLite table-lock failure was reproduced in a
20-request test, cross-repository operational forecast writes were serialized
and the bounded load smoke was repeated against that final write path. This is
the authoritative current-tree measurement. The historical 2026-09-23 run
below is retained for context, not as a controlled before/after benchmark.

| Measure | Result |
| --- | ---: |
| Host / runtime | Apple M1 Pro, macOS 27.2, arm64, 10 logical CPUs, 16 GiB RAM; Python 3.12.3; one Uvicorn worker; replay mode; one isolated SQLite replay-session DB |
| Concurrent clients / duration | 20 / 180.07 s |
| Requests / throughput | 66,179 / 367.52 requests/s |
| p50 / p95 / p99 | 5.40 / 311.89 / 398.41 ms |
| Maximum latency | 521.77 ms |
| HTTP results | 66,022 × 200; 157 × 201; 0 errors (0%) |
| SQLite locked/busy errors | 0 |
| Server CPU | 129.9% peak sampled by `ps` (macOS may report >100% across cores) |
| Server RSS | 340.89 MiB peak sampled by `ps` |
| Journal at end | 78 rows / 78 unique identities / 0 duplicates |
| Replay state | ready (the smoke does not start replay) |

This supports only the measured local single-worker replay setup. It is not a
production capacity, PostgreSQL durability, scheduled backup, or
infrastructure recovery claim. The smoke used an isolated replay session; its
157 dispatcher review responses resulted in 78 unique journal identities.

Measured 2026-09-23 on the local development host. This is a reproducible
single-process replay-runtime load smoke, not a production capacity guarantee.

## Environment and setup

- Host: Apple M1 Pro, macOS 27.2, arm64, 10 logical CPUs, 16 GiB RAM.
- Python 3.12.3; one Uvicorn worker; replay runtime; SQLite, one isolated
  replay-session database.
- 20 concurrent authenticated client loops, using the seeded central
  dispatcher identity; mixed dashboard/object/detail/history/cards/
  recommendations/journal/notifications reads plus low-frequency dispatcher
  review writes.
- Duration: 180 seconds, with the smoke script's post-run paginated journal
  integrity scan.

## Result after SQLite synchronization fix

| Measure | Result |
| --- | ---: |
| Requests | 65,623 |
| Throughput | 364.43 requests/s |
| p50 | 5.40 ms |
| p95 | 312.49 ms |
| p99 | 408.22 ms |
| Maximum | 552.96 ms |
| HTTP errors | 0 / 65,623 (0%) |
| SQLite locked/busy errors | 0 |
| Server CPU | 130.9% peak sampled by `ps` (macOS may report >100% across cores) |
| Server RSS | 340.72 MiB peak sampled by `ps` |
| Journal at end | 78 rows / 78 unique identities / 0 duplicates |

During the first 20-client attempt, unsynchronized reads on shared SQLite
repository connections produced HTTP 500s (no SQLite lock/busy errors). Reads
and write/read transactions were then protected by each repository's existing
reentrant lock. Targeted replay/API/workflow tests passed (71 passed), and the
full-duration load smoke was repeated with the zero-error result above. This
local result demonstrates only this exact host, one-worker, SQLite setup; it
does not establish production-scale capacity or durability.

## Reproduce

Start the replay server as described in `DEMO_RUNBOOK.md`, find the actual
Uvicorn Python process PID (not the terminal/shell PID), then run:

```bash
.venv/bin/python scripts/run_replay_load_smoke.py \
  --server-pid <uvicorn-python-pid> \
  --users 20 \
  --duration-seconds 180 \
  --base-url http://127.0.0.1:8000/api/v1
```

The smoke does not start, pause or reset replay during the measurement, so it
does not mutate the replay cursor. Dispatcher review writes are isolated
append-only records in the smoke session and are cleared when that replay
session is reset.
