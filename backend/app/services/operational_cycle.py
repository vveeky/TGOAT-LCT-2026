"""Dedicated write-side runner for one canonical six-hour ML cycle."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from backend.app.config import EVENT_SOURCE_TIMEZONE, as_utc, utc_now
from backend.app.services.ml_features import is_canonical_snapshot, latest_canonical_snapshot
from backend.app.services.prediction_history import OperationalForecastService
from backend.app.services.sqlite_support import connect_sqlite


class OperationalCycleError(RuntimeError):
    pass


class ReferenceContractError(OperationalCycleError):
    pass


class CycleAlreadyRunning(OperationalCycleError):
    pass


@dataclass(frozen=True)
class OperationalCycleResult:
    cycle_id: str
    snapshot_ts: datetime
    status: str
    object_count: int
    available_count: int
    degraded_count: int
    positive_count: int
    started_at: datetime
    finished_at: datetime | None
    error: str | None = None
    run_mode: str = "normal"


class SQLiteOperationalCycleRepository:
    """Persisted cycle lease/status; prediction/card writes remain separate but idempotent."""

    def __init__(self, path: str | Path = ":memory:", lease_seconds: int = 1800):
        self.path = str(path)
        self.lease_seconds = lease_seconds
        self._lock = threading.RLock()
        self._connection = connect_sqlite(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("""CREATE TABLE IF NOT EXISTS operational_cycles (
                cycle_id TEXT PRIMARY KEY, snapshot_ts TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL, object_count INTEGER NOT NULL DEFAULT 0,
                available_count INTEGER NOT NULL DEFAULT 0, degraded_count INTEGER NOT NULL DEFAULT 0,
                positive_count INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL,
                finished_at TEXT, error TEXT, run_mode TEXT NOT NULL DEFAULT 'normal'
            )""")
            columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(operational_cycles)")}
            if "run_mode" not in columns:
                self._connection.execute("ALTER TABLE operational_cycles ADD COLUMN run_mode TEXT NOT NULL DEFAULT 'normal'")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def begin(self, snapshot_ts: datetime, object_count: int, run_mode: str = "normal") -> OperationalCycleResult:
        snapshot_ts = as_utc(snapshot_ts)
        cycle_id = f"canonical:{snapshot_ts.isoformat()}"
        now = utc_now()
        with self._lock:
            row = self._connection.execute("SELECT * FROM operational_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
            if row and row["status"] == "completed":
                return self._row(row)
            if row and row["status"] == "running":
                age = now - datetime.fromisoformat(row["started_at"])
                if age < timedelta(seconds=self.lease_seconds):
                    raise CycleAlreadyRunning(cycle_id)
                self._connection.execute("UPDATE operational_cycles SET status='running', started_at=?, error=NULL, run_mode=? WHERE cycle_id=?",
                                         (now.isoformat(), run_mode, cycle_id))
            else:
                self._connection.execute("""INSERT OR REPLACE INTO operational_cycles
                    (cycle_id, snapshot_ts, status, object_count, started_at, run_mode)
                    VALUES (?,?,?,?,?,?)""", (cycle_id, snapshot_ts.isoformat(), "running", object_count, now.isoformat(), run_mode))
            self._connection.commit()
            row = self._connection.execute("SELECT * FROM operational_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
            return self._row(row)

    def latest_completed_snapshot(self) -> datetime | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT snapshot_ts FROM operational_cycles WHERE status='completed' ORDER BY snapshot_ts DESC LIMIT 1"
            ).fetchone()
            return datetime.fromisoformat(row["snapshot_ts"]) if row else None

    def latest(self) -> OperationalCycleResult | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM operational_cycles ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return self._row(row) if row else None

    def record_unavailable(self, snapshot_ts: datetime, *, run_mode: str, error: str) -> OperationalCycleResult:
        snapshot_ts = as_utc(snapshot_ts)
        cycle_id = f"canonical:{snapshot_ts.isoformat()}"
        now = utc_now().isoformat()
        with self._lock, self._connection:
            self._connection.execute("""INSERT OR REPLACE INTO operational_cycles
                (cycle_id, snapshot_ts, status, object_count, started_at, finished_at, error, run_mode)
                VALUES (?,?,?,?,?,?,?,?)""", (cycle_id, snapshot_ts.isoformat(), "unavailable", 0, now, now, error[:2000], run_mode))
            return self._row(self._connection.execute("SELECT * FROM operational_cycles WHERE cycle_id=?", (cycle_id,)).fetchone())

    def complete(self, cycle_id: str, *, available: int, degraded: int, positive: int) -> OperationalCycleResult:
        now = utc_now()
        with self._lock, self._connection:
            self._connection.execute("""UPDATE operational_cycles SET status='completed', available_count=?,
                degraded_count=?, positive_count=?, finished_at=?, error=NULL WHERE cycle_id=?""",
                                     (available, degraded, positive, now.isoformat(), cycle_id))
            return self._row(self._connection.execute("SELECT * FROM operational_cycles WHERE cycle_id=?", (cycle_id,)).fetchone())

    def fail(self, cycle_id: str, error: str) -> OperationalCycleResult:
        now = utc_now()
        with self._lock, self._connection:
            self._connection.execute("UPDATE operational_cycles SET status='failed', finished_at=?, error=? WHERE cycle_id=?",
                                     (now.isoformat(), error[:2000], cycle_id))
            return self._row(self._connection.execute("SELECT * FROM operational_cycles WHERE cycle_id=?", (cycle_id,)).fetchone())

    def get(self, snapshot_ts: datetime) -> OperationalCycleResult | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM operational_cycles WHERE snapshot_ts=?", (as_utc(snapshot_ts).isoformat(),)).fetchone()
            return self._row(row) if row else None

    @staticmethod
    def _row(row: sqlite3.Row) -> OperationalCycleResult:
        return OperationalCycleResult(
            cycle_id=row["cycle_id"], snapshot_ts=datetime.fromisoformat(row["snapshot_ts"]),
            status=row["status"], object_count=row["object_count"], available_count=row["available_count"],
            degraded_count=row["degraded_count"], positive_count=row["positive_count"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
                error=row["error"], run_mode=row["run_mode"],
        )


def next_canonical_snapshot(snapshot_ts: datetime) -> datetime:
    local = as_utc(snapshot_ts).astimezone(EVENT_SOURCE_TIMEZONE)
    return as_utc(local + timedelta(hours=6))


def due_canonical_snapshots(last_completed: datetime, latest: datetime) -> list[datetime]:
    current = next_canonical_snapshot(last_completed)
    latest = as_utc(latest)
    result = []
    while current <= latest:
        result.append(current)
        current = next_canonical_snapshot(current)
    return result


class OperationalForecastCycleService:
    """Execute exactly one canonical cycle; scheduler/HTTP are separate concerns."""

    def __init__(self, inference_service, forecast_service: OperationalForecastService,
                 cycle_repository: SQLiteOperationalCycleRepository,
                 object_source: Callable[[], Iterable[int]], expected_object_count: int = 78):
        self.inference_service = inference_service
        self.forecast_service = forecast_service
        self.cycle_repository = cycle_repository
        self.object_source = object_source
        self.expected_object_count = expected_object_count

    def run_once(self, *, now: datetime | None = None, snapshot_ts: datetime | None = None,
                 run_mode: str = "normal") -> OperationalCycleResult:
        selected = as_utc(snapshot_ts) if snapshot_ts is not None else latest_canonical_snapshot(now)
        if not is_canonical_snapshot(selected):
            raise OperationalCycleError("cycle snapshot must be canonical")
        object_ids = sorted(set(int(value) for value in self.object_source()))
        if len(object_ids) != self.expected_object_count:
            raise ReferenceContractError(
                f"reference object set has {len(object_ids)} objects, expected {self.expected_object_count}")
        self._validate_provider_reference()
        cycle = self.cycle_repository.begin(selected, len(object_ids), run_mode)
        if cycle.status == "completed":
            return cycle
        available = degraded = positive = 0
        try:
            results = self.inference_service.batch_predict([(object_id, selected) for object_id in object_ids])
            if len(results) != len(object_ids):
                raise OperationalCycleError("inference batch cardinality mismatch")
            for result in results:
                record, _, _ = self.forecast_service.record(result)
                if record.prediction.prediction_status == "available":
                    available += 1
                    positive += int(record.prediction.risk_flag is True)
                else:
                    degraded += 1
            return self.cycle_repository.complete(cycle.cycle_id, available=available,
                                                  degraded=degraded, positive=positive)
        except Exception as error:
            self.cycle_repository.fail(cycle.cycle_id, str(error))
            raise

    def run_due(self, *, now: datetime | None = None) -> list[OperationalCycleResult]:
        """Run bootstrap or every missed canonical snapshot, oldest first."""
        latest = latest_canonical_snapshot(now)
        last = self.cycle_repository.latest_completed_snapshot()
        if last is None:
            try:
                return [self.run_once(snapshot_ts=latest, run_mode="bootstrap")]
            except ReferenceContractError as error:
                return [self.cycle_repository.record_unavailable(latest, run_mode="bootstrap", error=str(error))]
        due = due_canonical_snapshots(last, latest)
        results = []
        for snapshot in due:
            try:
                results.append(self.run_once(snapshot_ts=snapshot, run_mode="catch_up" if len(due) > 1 else "normal"))
            except ReferenceContractError as error:
                results.append(self.cycle_repository.record_unavailable(snapshot, run_mode="catch_up", error=str(error)))
                break
        return results

    def _validate_provider_reference(self) -> None:
        provider = getattr(self.inference_service, "provider", None)
        metadata = provider.metadata() if provider is not None and hasattr(provider, "metadata") else {}
        if not metadata.get("reference_snapshot_id") or not metadata.get("reference_hash"):
            raise ReferenceContractError("accepted reference identity/hash unavailable; cycle fails closed")


class OperationalForecastWorker:
    """Thin runner loop intended for a dedicated process, never FastAPI lifespan."""

    def __init__(self, cycle_service: OperationalForecastCycleService, sleep_seconds: int = 30):
        self.cycle_service = cycle_service
        self.sleep_seconds = sleep_seconds

    def run_once(self, now: datetime | None = None) -> OperationalCycleResult:
        return self.cycle_service.run_once(now=now)

    def run_forever(self, *, stop_event=None, clock: Callable[[], datetime] = utc_now) -> None:
        """Poll the canonical clock from a dedicated process.

        The persisted cycle repository makes this safe against restarts and a
        second worker. FastAPI never calls this loop.
        """
        while stop_event is None or not stop_event.is_set():
            now = clock()
            try:
                self.cycle_service.run_due(now=now)
            except CycleAlreadyRunning:
                pass
            except Exception:
                # The cycle repository records the failure; the next poll can
                # retry the same canonical snapshot after the lease policy.
                pass
            time.sleep(self.sleep_seconds)
