"""Persistent dispatcher decisions and outcomes for operational ML forecasts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from backend.app.config import as_utc, utc_now
from backend.app.services.sqlite_support import connect_sqlite


class OperationalPredictionNotFound(LookupError):
    pass


class OperationalActionIdempotencyConflict(ValueError):
    pass


class OperationalPredictionNotActionable(ValueError):
    pass


def _iso(value: datetime) -> str:
    return as_utc(value).isoformat()


def _idempotency_key(explicit: str | None, payload: dict) -> str:
    if explicit:
        return explicit
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SQLiteOperationalActionRepository:
    """Append-only review/outcome store co-located with prediction history."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection = connect_sqlite(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS operational_prediction_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_identity TEXT NOT NULL REFERENCES prediction_history(identity),
                object_id INTEGER NOT NULL,
                snapshot_ts TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason_code TEXT,
                comment TEXT,
                created_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                UNIQUE(prediction_identity, actor_user_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_operational_reviews_identity
                ON operational_prediction_reviews(prediction_identity, created_at);
            CREATE TABLE IF NOT EXISTS operational_prediction_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_identity TEXT NOT NULL REFERENCES prediction_history(identity),
                object_id INTEGER NOT NULL,
                snapshot_ts TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                actual_outcome TEXT NOT NULL,
                reason_code TEXT,
                comment TEXT,
                created_at TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                UNIQUE(prediction_identity, actor_user_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_operational_outcomes_identity
                ON operational_prediction_outcomes(prediction_identity, created_at);
        """)
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def prediction(self, identity: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT identity, object_id, snapshot_ts, model_version, reference_hash, prediction_status "
                "FROM prediction_history WHERE identity=?", (identity,),
            ).fetchone()
            return dict(row) if row else None

    def _validate_prediction(self, identity: str, object_id: int) -> dict:
        prediction = self.prediction(identity)
        if prediction is None:
            raise OperationalPredictionNotFound(identity)
        if int(prediction["object_id"]) != int(object_id):
            raise OperationalPredictionNotFound(identity)
        return prediction

    def _insert(self, *, table: str, identity: str, object_id: int,
                actor_user_id: str, actor_role: str, payload: dict,
                idempotency_key: str | None) -> dict:
        with self._lock:
            prediction = self._validate_prediction(identity, object_id)
            if prediction["prediction_status"] != "available":
                raise OperationalPredictionNotActionable(
                    "dispatcher actions require an available operational prediction"
                )
            key = _idempotency_key(idempotency_key, {
                "identity": identity, "actor_user_id": actor_user_id, **payload,
            })
            conn = self._connection
            prior = conn.execute(
                f"SELECT * FROM {table} WHERE prediction_identity=? AND actor_user_id=? AND idempotency_key=?",
                (identity, actor_user_id, key),
            ).fetchone()
            if prior:
                prior_payload = {name: prior[name] for name in payload}
                if prior_payload != payload:
                    raise OperationalActionIdempotencyConflict("idempotency key was already used with different content")
                return dict(prior)
            now = _iso(utc_now())
            values = {
                "prediction_identity": identity,
                "object_id": int(prediction["object_id"]),
                "snapshot_ts": prediction["snapshot_ts"],
                "actor_user_id": actor_user_id,
                "actor_role": actor_role,
                **payload,
                "created_at": now,
                "idempotency_key": key,
            }
            columns = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            try:
                with conn:
                    cursor = conn.execute(
                        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                        tuple(values.values()),
                    )
                row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (cursor.lastrowid,)).fetchone()
                return dict(row)
            except sqlite3.IntegrityError:
                prior = conn.execute(
                    f"SELECT * FROM {table} WHERE prediction_identity=? AND actor_user_id=? AND idempotency_key=?",
                    (identity, actor_user_id, key),
                ).fetchone()
                if prior and {name: prior[name] for name in payload} == payload:
                    return dict(prior)
                raise OperationalActionIdempotencyConflict("idempotency key conflicts with an existing action")

    def add_review(self, identity: str, object_id: int, *, actor_user_id: str,
                   actor_role: str, decision: str, reason_code: str | None,
                   comment: str | None, idempotency_key: str | None = None) -> dict:
        return self._insert(
            table="operational_prediction_reviews", identity=identity, object_id=object_id,
            actor_user_id=actor_user_id, actor_role=actor_role,
            payload={"decision": decision, "reason_code": reason_code, "comment": comment},
            idempotency_key=idempotency_key,
        )

    def add_outcome(self, identity: str, object_id: int, *, actor_user_id: str,
                    actor_role: str, actual_outcome: str, reason_code: str | None,
                    comment: str | None, idempotency_key: str | None = None) -> dict:
        return self._insert(
            table="operational_prediction_outcomes", identity=identity, object_id=object_id,
            actor_user_id=actor_user_id, actor_role=actor_role,
            payload={"actual_outcome": actual_outcome, "reason_code": reason_code, "comment": comment},
            idempotency_key=idempotency_key,
        )

    def _list(self, table: str, identity: str) -> list[dict]:
        with self._lock:
            if self.prediction(identity) is None:
                raise OperationalPredictionNotFound(identity)
            rows = self._connection.execute(
                f"SELECT * FROM {table} WHERE prediction_identity=? ORDER BY id", (identity,),
            ).fetchall()
            return [dict(row) for row in rows]

    def reviews(self, identity: str) -> list[dict]:
        return self._list("operational_prediction_reviews", identity)

    def outcomes(self, identity: str) -> list[dict]:
        return self._list("operational_prediction_outcomes", identity)

    def summaries(self, identities: list[str]) -> dict[str, dict]:
        summaries = {
            identity: {"review_count": 0, "latest_review_decision": None,
                       "outcome_count": 0, "latest_actual_outcome": None}
            for identity in identities
        }
        if not identities:
            return summaries
        placeholders = ",".join("?" for _ in identities)
        with self._lock:
            reviews = self._connection.execute(
                "SELECT prediction_identity, decision, created_at, id "
                f"FROM operational_prediction_reviews WHERE prediction_identity IN ({placeholders}) "
                "ORDER BY created_at DESC, id DESC", identities,
            ).fetchall()
            seen_reviews = set()
            for row in reviews:
                identity = row["prediction_identity"]
                summaries[identity]["review_count"] += 1
                if identity not in seen_reviews:
                    summaries[identity]["latest_review_decision"] = row["decision"]
                    seen_reviews.add(identity)
            outcomes = self._connection.execute(
                "SELECT prediction_identity, actual_outcome, created_at, id "
                f"FROM operational_prediction_outcomes WHERE prediction_identity IN ({placeholders}) "
                "ORDER BY created_at DESC, id DESC", identities,
            ).fetchall()
            seen_outcomes = set()
            for row in outcomes:
                identity = row["prediction_identity"]
                summaries[identity]["outcome_count"] += 1
                if identity not in seen_outcomes:
                    summaries[identity]["latest_actual_outcome"] = row["actual_outcome"]
                    seen_outcomes.add(identity)
            return summaries
