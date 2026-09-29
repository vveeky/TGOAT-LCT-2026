"""Persistent operational ML prediction history and forecast-card grouping.

The operational cycle is deliberately separate from GET/replay handlers.  A
read-only API may inspect this repository, but only ``process_operational``
can append history or advance cards.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.config import as_utc, utc_now
from backend.app.services.sqlite_support import connect_sqlite


MERGE_GAP = timedelta(hours=12)


class PredictionHistoryError(RuntimeError):
    pass


class OutOfOrderPredictionError(PredictionHistoryError):
    pass


class ProvenanceConflictError(PredictionHistoryError):
    pass


class PredictionIdentityConflictError(PredictionHistoryError):
    pass


@dataclass(frozen=True)
class OperationalPrediction:
    object_id: int
    snapshot_ts: datetime
    generated_at: datetime
    risk_probability: float | None
    risk_flag: bool | None
    threshold: float | None
    prediction_status: str
    degraded_reason: str | None
    risk_score_kind: str
    model_version: str | None
    feature_schema_version: str | None
    reference_snapshot_id: str | None
    reference_hash: str | None
    source_freshness_ts: datetime | None = None

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> "OperationalPrediction":
        return cls(
            object_id=int(result["object_id"]),
            snapshot_ts=as_utc(_parse_dt(result["snapshot_ts"])),
            generated_at=as_utc(_parse_dt(result["generated_at"])),
            risk_probability=result.get("risk_probability"),
            risk_flag=result.get("risk_flag"),
            threshold=result.get("threshold"),
            prediction_status=str(result["status"]),
            degraded_reason=result.get("degraded_reason"),
            risk_score_kind=str(result.get("risk_score_kind", "ml")),
            model_version=result.get("model_version"),
            feature_schema_version=result.get("feature_schema_version"),
            reference_snapshot_id=result.get("reference_snapshot_id"),
            reference_hash=result.get("reference_hash"),
            source_freshness_ts=(as_utc(_parse_dt(result["source_freshness_ts"]))
                                 if result.get("source_freshness_ts") else None),
        )


@dataclass(frozen=True)
class PredictionHistoryRecord:
    identity: str
    prediction: OperationalPrediction
    created_at: datetime


@dataclass(frozen=True)
class ForecastCard:
    card_id: str
    object_id: int
    first_positive_snapshot_ts: datetime
    last_positive_snapshot_ts: datetime
    max_probability: float
    threshold: float | None
    model_version: str | None
    feature_schema_version: str | None
    reference_snapshot_id: str | None
    reference_hash: str | None
    status: str
    created_at: datetime
    updated_at: datetime


def _parse_dt(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return as_utc(value).isoformat()


class SQLitePredictionHistoryRepository:
    """SQLite MVP repository; production deployment should use PostgreSQL."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection = connect_sqlite(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._batch_depth = 0
        self._create_schema()

    @contextmanager
    def batch(self):
        """Atomic prediction/card batch used for one replay scoring boundary."""
        with self._lock:
            if self._batch_depth or self._connection.in_transaction:
                raise PredictionHistoryError("prediction batch cannot be nested")
            self._connection.execute("BEGIN IMMEDIATE")
            self._batch_depth = 1
            try:
                yield
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            finally:
                self._batch_depth = 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS prediction_history (
                    identity TEXT PRIMARY KEY,
                    object_id INTEGER NOT NULL,
                    snapshot_ts TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    risk_probability REAL,
                    risk_flag INTEGER,
                    threshold REAL,
                    prediction_status TEXT NOT NULL,
                    degraded_reason TEXT,
                    risk_score_kind TEXT NOT NULL,
                    model_version TEXT,
                    feature_schema_version TEXT,
                    reference_snapshot_id TEXT,
                    reference_hash TEXT,
                    source_freshness_ts TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(object_id, snapshot_ts, model_version)
                );
                CREATE INDEX IF NOT EXISTS idx_prediction_object_time
                    ON prediction_history(object_id, snapshot_ts);
                CREATE TABLE IF NOT EXISTS forecast_cards (
                    card_id TEXT PRIMARY KEY,
                    object_id INTEGER NOT NULL,
                    first_positive_snapshot_ts TEXT NOT NULL,
                    last_positive_snapshot_ts TEXT NOT NULL,
                    max_probability REAL NOT NULL,
                    threshold REAL,
                    model_version TEXT,
                    feature_schema_version TEXT,
                    reference_snapshot_id TEXT,
                    reference_hash TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cards_object_status
                    ON forecast_cards(object_id, status, last_positive_snapshot_ts);
                CREATE TABLE IF NOT EXISTS forecast_card_predictions (
                    prediction_identity TEXT PRIMARY KEY REFERENCES prediction_history(identity),
                    card_id TEXT NOT NULL REFERENCES forecast_cards(card_id)
                );
            """)
            # Upgrade existing history without inference or recommendation heuristics.
            # Only an unambiguous positive/provenance-compatible episode is linked.
            self._connection.execute("""INSERT OR IGNORE INTO forecast_card_predictions
                SELECT p.identity, MIN(c.card_id) FROM prediction_history p
                JOIN forecast_cards c ON c.object_id=p.object_id
                  AND p.snapshot_ts BETWEEN c.first_positive_snapshot_ts AND c.last_positive_snapshot_ts
                  AND p.model_version IS c.model_version
                  AND p.feature_schema_version IS c.feature_schema_version
                  AND p.reference_hash IS c.reference_hash
                WHERE p.prediction_status='available' AND p.risk_flag=1
                GROUP BY p.identity HAVING COUNT(*)=1""")

    @staticmethod
    def identity(prediction: OperationalPrediction) -> str:
        return f"{prediction.object_id}|{_iso(prediction.snapshot_ts)}|{prediction.model_version or ''}"

    def _row_prediction(self, row: sqlite3.Row) -> PredictionHistoryRecord:
        p = OperationalPrediction(
            object_id=row["object_id"], snapshot_ts=_parse_dt(row["snapshot_ts"]),
            generated_at=_parse_dt(row["generated_at"]), risk_probability=row["risk_probability"],
            risk_flag=None if row["risk_flag"] is None else bool(row["risk_flag"]),
            threshold=row["threshold"], prediction_status=row["prediction_status"],
            degraded_reason=row["degraded_reason"], risk_score_kind=row["risk_score_kind"],
            model_version=row["model_version"], feature_schema_version=row["feature_schema_version"],
            reference_snapshot_id=row["reference_snapshot_id"], reference_hash=row["reference_hash"],
            source_freshness_ts=_parse_dt(row["source_freshness_ts"]) if row["source_freshness_ts"] else None,
        )
        return PredictionHistoryRecord(row["identity"], p, _parse_dt(row["created_at"]))

    def _row_card(self, row: sqlite3.Row) -> ForecastCard:
        return ForecastCard(
            card_id=row["card_id"], object_id=row["object_id"],
            first_positive_snapshot_ts=_parse_dt(row["first_positive_snapshot_ts"]),
            last_positive_snapshot_ts=_parse_dt(row["last_positive_snapshot_ts"]),
            max_probability=row["max_probability"], threshold=row["threshold"],
            model_version=row["model_version"], feature_schema_version=row["feature_schema_version"],
            reference_snapshot_id=row["reference_snapshot_id"], reference_hash=row["reference_hash"],
            status=row["status"], created_at=_parse_dt(row["created_at"]), updated_at=_parse_dt(row["updated_at"]),
        )

    def history(self, object_id: int, limit: int = 100) -> list[PredictionHistoryRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM prediction_history WHERE object_id=? ORDER BY snapshot_ts DESC LIMIT ?",
                (object_id, limit)).fetchall()
            return [self._row_prediction(row) for row in rows]

    def get(self, identity: str) -> PredictionHistoryRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM prediction_history WHERE identity=?", (identity,),
            ).fetchone()
            return self._row_prediction(row) if row else None

    def global_history(self, *, object_ids: list[int] | None, limit: int, offset: int):
        where = ""
        params: list[Any] = []
        if object_ids is not None:
            if not object_ids:
                return [], 0
            where = f" WHERE object_id IN ({','.join('?' for _ in object_ids)})"
            params.extend(object_ids)
        with self._lock:
            total = self._connection.execute(
                f"SELECT COUNT(*) FROM prediction_history{where}", params,
            ).fetchone()[0]
            rows = self._connection.execute(
                f"SELECT * FROM prediction_history{where} "
                "ORDER BY snapshot_ts DESC, object_id, identity LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return [self._row_prediction(row) for row in rows], total

    def cards(self, object_id: int, *, status: str | None = None) -> list[ForecastCard]:
        with self._lock:
            if status:
                rows = self._connection.execute(
                    "SELECT * FROM forecast_cards WHERE object_id=? AND status=? ORDER BY first_positive_snapshot_ts DESC",
                    (object_id, status)).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM forecast_cards WHERE object_id=? ORDER BY first_positive_snapshot_ts DESC",
                    (object_id,)).fetchall()
            return [self._row_card(row) for row in rows]

    def process_operational(self, prediction: OperationalPrediction) -> tuple[PredictionHistoryRecord, ForecastCard | None, bool]:
        """Atomically persist one operational result and update card state.

        Returns ``(history_record, card_or_none, inserted)``. Duplicate identity
        is a no-op. Operational input is monotonic per object; replay belongs to
        a separate read-only path and cannot mutate these tables.
        """
        if prediction.prediction_status not in {"available", "degraded"}:
            raise PredictionHistoryError("operational status must be available or degraded")
        if prediction.prediction_status == "available" and prediction.risk_flag is None:
            raise PredictionHistoryError("available prediction requires risk_flag")
        with self._lock:
            conn = self._connection
            identity = self.identity(prediction)
            existing = conn.execute("SELECT * FROM prediction_history WHERE identity=?", (identity,)).fetchone()
            if existing:
                if not self._same_prediction(existing, prediction):
                    raise PredictionIdentityConflictError("prediction identity already has incompatible content")
                record = self._row_prediction(existing)
                linked = conn.execute("""SELECT c.* FROM forecast_cards c
                    JOIN forecast_card_predictions m ON m.card_id=c.card_id
                    WHERE m.prediction_identity=?""", (identity,)).fetchone()
                return record, self._row_card(linked) if linked else None, False
            # A degraded result cannot know the eventual model version. Permit
            # exactly one transactional upgrade of the same object/snapshot to
            # an available result, while keeping available rows immutable.
            degraded = conn.execute(
                "SELECT * FROM prediction_history WHERE object_id=? AND snapshot_ts=? AND prediction_status='degraded'",
                (prediction.object_id, _iso(prediction.snapshot_ts))).fetchone()
            if degraded and prediction.prediction_status == "available":
                identity = self.identity(prediction)
            latest = conn.execute(
                "SELECT snapshot_ts FROM prediction_history WHERE object_id=? ORDER BY snapshot_ts DESC LIMIT 1",
                (prediction.object_id,)).fetchone()
            if latest and prediction.snapshot_ts < _parse_dt(latest["snapshot_ts"]):
                raise OutOfOrderPredictionError("older operational snapshot must be replayed explicitly")
            now = utc_now()
            managed_batch = bool(self._batch_depth)
            if not managed_batch:
                conn.execute("BEGIN IMMEDIATE")
            try:
                if degraded and prediction.prediction_status == "available":
                    conn.execute("""UPDATE prediction_history SET identity=?, generated_at=?,
                        risk_probability=?, risk_flag=?, threshold=?, prediction_status=?, degraded_reason=?,
                        risk_score_kind=?, model_version=?, feature_schema_version=?, reference_snapshot_id=?,
                        reference_hash=?, source_freshness_ts=? WHERE identity=?""", (
                        identity, _iso(prediction.generated_at), prediction.risk_probability,
                        int(prediction.risk_flag), prediction.threshold, prediction.prediction_status,
                        prediction.degraded_reason, prediction.risk_score_kind, prediction.model_version,
                        prediction.feature_schema_version, prediction.reference_snapshot_id,
                        prediction.reference_hash, _iso(prediction.source_freshness_ts)
                        if prediction.source_freshness_ts else None, degraded["identity"]))
                else:
                    conn.execute("""INSERT INTO prediction_history
                    (identity, object_id, snapshot_ts, generated_at, risk_probability, risk_flag,
                     threshold, prediction_status, degraded_reason, risk_score_kind, model_version,
                     feature_schema_version, reference_snapshot_id, reference_hash, source_freshness_ts, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    identity, prediction.object_id, _iso(prediction.snapshot_ts), _iso(prediction.generated_at),
                    prediction.risk_probability, None if prediction.risk_flag is None else int(prediction.risk_flag),
                    prediction.threshold, prediction.prediction_status, prediction.degraded_reason,
                    prediction.risk_score_kind, prediction.model_version, prediction.feature_schema_version,
                    prediction.reference_snapshot_id, prediction.reference_hash,
                    _iso(prediction.source_freshness_ts) if prediction.source_freshness_ts else None, _iso(now)))
                card = self._advance_card(conn, prediction, now)
                if card:
                    conn.execute("INSERT INTO forecast_card_predictions VALUES (?,?)",
                                 (identity, card.card_id))
                self._finalize_watermark(conn, prediction, now)
                if not managed_batch:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
            row = conn.execute("SELECT * FROM prediction_history WHERE identity=?", (identity,)).fetchone()
            return self._row_prediction(row), card, True

    @staticmethod
    def _same_prediction(row: sqlite3.Row, p: OperationalPrediction) -> bool:
        return (
            row["object_id"] == p.object_id and row["snapshot_ts"] == _iso(p.snapshot_ts)
            and row["risk_probability"] == p.risk_probability
            and (None if row["risk_flag"] is None else bool(row["risk_flag"])) == p.risk_flag
            and row["threshold"] == p.threshold and row["prediction_status"] == p.prediction_status
            and row["degraded_reason"] == p.degraded_reason and row["risk_score_kind"] == p.risk_score_kind
            and row["model_version"] == p.model_version
            and row["feature_schema_version"] == p.feature_schema_version
            and row["reference_snapshot_id"] == p.reference_snapshot_id
            and row["reference_hash"] == p.reference_hash
        )

    def _advance_card(self, conn: sqlite3.Connection, p: OperationalPrediction, now: datetime) -> ForecastCard | None:
        if p.prediction_status != "available" or p.risk_flag is not True:
            return None
        if p.risk_probability is None:
            raise PredictionHistoryError("positive prediction requires risk_probability")
        row = conn.execute(
            "SELECT * FROM forecast_cards WHERE object_id=? AND status IN ('active','uncertain') ORDER BY last_positive_snapshot_ts DESC LIMIT 1",
            (p.object_id,)).fetchone()
        if row and p.snapshot_ts - _parse_dt(row["last_positive_snapshot_ts"]) <= MERGE_GAP:
            provenance = (row["model_version"], row["feature_schema_version"], row["reference_hash"])
            incoming = (p.model_version, p.feature_schema_version, p.reference_hash)
            if provenance != incoming:
                raise ProvenanceConflictError("positive prediction provenance is incompatible with active card")
            conn.execute("""UPDATE forecast_cards SET status='active', last_positive_snapshot_ts=?, max_probability=?,
                threshold=?, updated_at=? WHERE card_id=?""", (
                _iso(p.snapshot_ts), max(row["max_probability"], p.risk_probability), p.threshold, _iso(now), row["card_id"]))
            return self._row_card(conn.execute("SELECT * FROM forecast_cards WHERE card_id=?", (row["card_id"],)).fetchone())
        if row:
            conn.execute("UPDATE forecast_cards SET status='closed', updated_at=? WHERE card_id=?", (_iso(now), row["card_id"]))
        card_id = f"object:{p.object_id}:{_iso(p.snapshot_ts)}"
        conn.execute("""INSERT INTO forecast_cards
            (card_id, object_id, first_positive_snapshot_ts, last_positive_snapshot_ts, max_probability,
             threshold, model_version, feature_schema_version, reference_snapshot_id, reference_hash,
             status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            card_id, p.object_id, _iso(p.snapshot_ts), _iso(p.snapshot_ts), p.risk_probability,
            p.threshold, p.model_version, p.feature_schema_version, p.reference_snapshot_id,
            p.reference_hash, "active", _iso(now), _iso(now)))
        return self._row_card(conn.execute("SELECT * FROM forecast_cards WHERE card_id=?", (card_id,)).fetchone())

    def _finalize_watermark(self, conn: sqlite3.Connection, p: OperationalPrediction, now: datetime) -> None:
        """Finalize a card once the canonical slot at last-positive+12h is processed.

        A degraded boundary is uncertain rather than falsely closed. A later
        available positive at the same canonical identity can still retry and
        update the card transactionally.
        """
        boundary = _iso(p.snapshot_ts - MERGE_GAP)
        rows = conn.execute(
            """SELECT * FROM forecast_cards WHERE object_id=? AND status IN ('active','uncertain')
               AND last_positive_snapshot_ts <= ?""",
            (p.object_id, boundary)).fetchall()
        for row in rows:
            status = "uncertain" if p.prediction_status == "degraded" else "closed"
            conn.execute("UPDATE forecast_cards SET status=?, updated_at=? WHERE card_id=?",
                         (status, _iso(now), row["card_id"]))

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Small deterministic diagnostic representation for restart tests."""
        return {
            "history": [dict(row) for row in self._connection.execute("SELECT * FROM prediction_history ORDER BY identity")],
            "cards": [dict(row) for row in self._connection.execute("SELECT * FROM forecast_cards ORDER BY card_id")],
        }


class OperationalForecastService:
    """Application boundary for write-side inference and read-only card views."""

    def __init__(self, repository: SQLitePredictionHistoryRepository, recommendation_service=None,
                 notification_repository=None):
        self.repository = repository
        self.recommendation_service = recommendation_service
        self.notification_repository = notification_repository
        self._record_lock = threading.RLock()

    def record(self, result: dict[str, Any]) -> tuple[PredictionHistoryRecord, ForecastCard | None, bool]:
        if getattr(self.repository, "atomic_projections", False):
            # PostgreSQL normal mode binds authoritative history/card writes,
            # deterministic recommendation, and notification in one shared UoW.
            # Unlike the recoverable SQLite projections, a PG write failure must
            # roll the entire canonical prediction command back.
            with self.repository.batch():
                record, card, inserted = self.repository.process_operational(
                    OperationalPrediction.from_result(result)
                )
                if self.recommendation_service is not None:
                    self.recommendation_service.generate(record, card)
                if self.notification_repository is not None:
                    self.notification_repository.create_for_prediction(record, card)
                return record, card, inserted
        # These SQLite repositories use separate connections to a shared local
        # database. Serialize the cross-repository write so concurrent requests
        # cannot overlap history/card, recommendation, and notification writes.
        with self._record_lock:
            record, card, inserted = self.repository.process_operational(
                OperationalPrediction.from_result(result)
            )
            if self.recommendation_service is not None:
                try:
                    self.recommendation_service.generate(record, card)
                except Exception:
                    # Prediction/card state is authoritative. Recommendation is a
                    # deterministic, explicitly recoverable projection; callers
                    # can rebuild it without re-running ML or changing cards.
                    pass
            if self.notification_repository is not None:
                # Unique operational prediction identity makes this safe on cycle retries.
                self.notification_repository.create_for_prediction(record, card)
            return record, card, inserted

    def record_batch(self, results: list[dict[str, Any]]) -> list[tuple[PredictionHistoryRecord, ForecastCard | None, bool]]:
        """Atomically write all prediction/card state for one replay boundary."""
        if getattr(self.repository, "atomic_projections", False):
            with self.repository.batch():
                written = [self.repository.process_operational(OperationalPrediction.from_result(result))
                           for result in results]
                for record, card, _ in written:
                    if self.recommendation_service is not None:
                        self.recommendation_service.generate(record, card)
                    if self.notification_repository is not None:
                        self.notification_repository.create_for_prediction(record, card)
                return written
        with self._record_lock, self.repository.batch():
            written = [self.repository.process_operational(OperationalPrediction.from_result(result))
                       for result in results]
        # Derived projections are recoverable/idempotent and intentionally run
        # only after the authoritative prediction/card transaction commits.
        for record, card, _ in written:
            if self.recommendation_service is not None:
                try:
                    self.recommendation_service.generate(record, card)
                except Exception:
                    pass
            if self.notification_repository is not None:
                self.notification_repository.create_for_prediction(record, card)
        return written

    def history(self, object_id: int, limit: int = 100) -> list[PredictionHistoryRecord]:
        return self.repository.history(object_id, limit)

    def get(self, identity: str) -> PredictionHistoryRecord | None:
        return self.repository.get(identity)

    def global_history(self, *, object_ids: list[int] | None, limit: int, offset: int):
        return self.repository.global_history(object_ids=object_ids, limit=limit, offset=offset)

    def cards(self, object_id: int, *, status: str | None = None) -> list[ForecastCard]:
        return self.repository.cards(object_id, status=status)

    def recommendations(self, object_id: int):
        if self.recommendation_service is None:
            return []
        return self.recommendation_service.repository.list_for_object(object_id)

    def rebuild_recommendation(self, record: PredictionHistoryRecord, card: ForecastCard | None = None):
        if self.recommendation_service is None:
            return None
        return self.recommendation_service.generate(record, card)
