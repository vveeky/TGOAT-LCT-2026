"""PostgreSQL implementation of the frozen prediction/card repository contract.

This is intentionally explicit SQL rather than a SQLite dialect-rewrite layer.
The identity and episode rules mirror SQLitePredictionHistoryRepository; the
object lock row serializes first-card creation and chronological updates.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

from psycopg.rows import dict_row

from backend.app.config import as_utc, utc_now
from backend.app.services.prediction_history import (
    MERGE_GAP,
    ForecastCard,
    OperationalPrediction,
    OutOfOrderPredictionError,
    PredictionHistoryError,
    PredictionHistoryRecord,
    PredictionIdentityConflictError,
    ProvenanceConflictError,
    _iso,
    _parse_dt,
)


class PostgreSQLPredictionHistoryRepository:
    """Operational prediction/card storage on a shared PostgreSQL pool."""

    atomic_projections = True

    def __init__(self, database):
        self.database = database
        self._local = threading.local()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """One connection/transaction for prediction rows, cards and memberships."""
        if getattr(self._local, "connection", None) is not None:
            raise PredictionHistoryError("prediction batch cannot be nested")
        with self.database.unit_of_work() as uow:
            connection = uow.connection
            try:
                self._local.connection = connection
                yield
            finally:
                self._local.connection = None

    @contextmanager
    def _connection(self):
        current = getattr(self._local, "connection", None)
        if current is not None:
            yield current
        else:
            with self.database.connection() as connection:
                yield connection

    @staticmethod
    def identity(prediction: OperationalPrediction) -> str:
        return f"{prediction.object_id}|{_iso(prediction.snapshot_ts)}|{prediction.model_version or ''}"

    @staticmethod
    def _one(connection, sql: str, params=()):
        return connection.cursor(row_factory=dict_row).execute(sql, params).fetchone()

    @staticmethod
    def _all(connection, sql: str, params=()):
        return connection.cursor(row_factory=dict_row).execute(sql, params).fetchall()

    def _row_prediction(self, row) -> PredictionHistoryRecord:
        prediction = OperationalPrediction(
            object_id=row["object_id"], snapshot_ts=_parse_dt(row["snapshot_ts"]),
            generated_at=_parse_dt(row["generated_at"]), risk_probability=row["risk_probability"],
            risk_flag=row["risk_flag"], threshold=row["threshold"],
            prediction_status=row["prediction_status"], degraded_reason=row["degraded_reason"],
            risk_score_kind=row["risk_score_kind"], model_version=row["model_version"],
            feature_schema_version=row["feature_schema_version"],
            reference_snapshot_id=row["reference_snapshot_id"], reference_hash=row["reference_hash"],
            source_freshness_ts=_parse_dt(row["source_freshness_ts"]) if row["source_freshness_ts"] else None,
        )
        return PredictionHistoryRecord(row["identity"], prediction, _parse_dt(row["created_at"]))

    @staticmethod
    def _row_card(row) -> ForecastCard:
        return ForecastCard(
            card_id=row["card_id"], object_id=row["object_id"],
            first_positive_snapshot_ts=_parse_dt(row["first_positive_snapshot_ts"]),
            last_positive_snapshot_ts=_parse_dt(row["last_positive_snapshot_ts"]),
            max_probability=row["max_probability"], threshold=row["threshold"],
            model_version=row["model_version"], feature_schema_version=row["feature_schema_version"],
            reference_snapshot_id=row["reference_snapshot_id"], reference_hash=row["reference_hash"],
            status=row["status"], created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def history(self, object_id: int, limit: int = 100):
        with self._connection() as connection:
            rows = self._all(connection, "SELECT * FROM prediction_history WHERE object_id=%s ORDER BY snapshot_ts DESC LIMIT %s",
                             (object_id, limit))
        return [self._row_prediction(row) for row in rows]

    def get(self, identity: str):
        with self._connection() as connection:
            row = self._one(connection, "SELECT * FROM prediction_history WHERE identity=%s", (identity,))
        return self._row_prediction(row) if row else None

    def global_history(self, *, object_ids: list[int] | None, limit: int, offset: int):
        where, params = "", []
        if object_ids is not None:
            if not object_ids:
                return [], 0
            where = " WHERE object_id = ANY(%s)"
            params.append(object_ids)
        with self._connection() as connection:
            total = self._one(connection, "SELECT count(*) AS n FROM prediction_history" + where, params)["n"]
            rows = self._all(connection,
                "SELECT * FROM prediction_history" + where +
                " ORDER BY snapshot_ts DESC, object_id, identity LIMIT %s OFFSET %s",
                [*params, limit, offset])
        return [self._row_prediction(row) for row in rows], total

    def cards(self, object_id: int, *, status: str | None = None):
        sql = "SELECT * FROM forecast_cards WHERE object_id=%s"
        params: list[Any] = [object_id]
        if status:
            sql += " AND status=%s"
            params.append(status)
        sql += " ORDER BY first_positive_snapshot_ts DESC"
        with self._connection() as connection:
            rows = self._all(connection, sql, params)
        return [self._row_card(row) for row in rows]

    def process_operational(self, prediction: OperationalPrediction):
        if prediction.prediction_status not in {"available", "degraded"}:
            raise PredictionHistoryError("operational status must be available or degraded")
        if prediction.prediction_status == "available" and prediction.risk_flag is None:
            raise PredictionHistoryError("available prediction requires risk_flag")
        identity = self.identity(prediction)
        if getattr(self._local, "connection", None) is None:
            with self.batch():
                return self._process_in_transaction(prediction, identity)
        return self._process_in_transaction(prediction, identity)

    def _process_in_transaction(self, p: OperationalPrediction, identity: str):
        connection = self._local.connection
        connection.execute(
            "INSERT INTO object_workflow_locks(object_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (p.object_id,),
        )
        connection.execute("SELECT object_id FROM object_workflow_locks WHERE object_id=%s FOR UPDATE", (p.object_id,)).fetchone()
        existing = self._one(connection, "SELECT * FROM prediction_history WHERE identity=%s", (identity,))
        if existing:
            if not self._same_prediction(existing, p):
                raise PredictionIdentityConflictError("prediction identity already has incompatible content")
            member = self._one(connection, """SELECT c.* FROM forecast_cards c
                JOIN forecast_card_predictions m ON m.card_id=c.card_id WHERE m.prediction_identity=%s""", (identity,))
            return self._row_prediction(existing), self._row_card(member) if member else None, False
        degraded = self._one(connection, """SELECT * FROM prediction_history WHERE object_id=%s
            AND snapshot_ts=%s AND prediction_status='degraded' FOR UPDATE""", (p.object_id, p.snapshot_ts))
        latest = self._one(connection, "SELECT snapshot_ts FROM prediction_history WHERE object_id=%s ORDER BY snapshot_ts DESC LIMIT 1 FOR UPDATE",
                            (p.object_id,))
        if latest and p.snapshot_ts < _parse_dt(latest["snapshot_ts"]):
            raise OutOfOrderPredictionError("older operational snapshot must be replayed explicitly")
        now = utc_now()
        if degraded and p.prediction_status == "available":
            connection.execute("""UPDATE prediction_history SET identity=%s, generated_at=%s,
                risk_probability=%s,risk_flag=%s,threshold=%s,prediction_status=%s,degraded_reason=%s,
                risk_score_kind=%s,model_version=%s,feature_schema_version=%s,reference_snapshot_id=%s,
                reference_hash=%s,source_freshness_ts=%s WHERE identity=%s""",
                (identity, p.generated_at, p.risk_probability, p.risk_flag, p.threshold, p.prediction_status,
                 p.degraded_reason, p.risk_score_kind, p.model_version, p.feature_schema_version,
                 p.reference_snapshot_id, p.reference_hash, p.source_freshness_ts, degraded["identity"]))
        else:
            connection.execute("""INSERT INTO prediction_history
                (identity,object_id,snapshot_ts,generated_at,risk_probability,risk_flag,threshold,
                 prediction_status,degraded_reason,risk_score_kind,model_version,feature_schema_version,
                 reference_snapshot_id,reference_hash,source_freshness_ts,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (identity,p.object_id,p.snapshot_ts,p.generated_at,p.risk_probability,p.risk_flag,p.threshold,
                 p.prediction_status,p.degraded_reason,p.risk_score_kind,p.model_version,p.feature_schema_version,
                 p.reference_snapshot_id,p.reference_hash,p.source_freshness_ts,now))
        card = self._advance_card(connection, p, now)
        if card:
            connection.execute("INSERT INTO forecast_card_predictions(prediction_identity,card_id) VALUES (%s,%s)",
                               (identity, card.card_id))
        self._finalize_watermark(connection, p, now)
        row = self._one(connection, "SELECT * FROM prediction_history WHERE identity=%s", (identity,))
        return self._row_prediction(row), card, True

    @staticmethod
    def _same_prediction(row, p):
        return all((row[key] == expected) for key, expected in (
            ("object_id", p.object_id), ("snapshot_ts", p.snapshot_ts),
            ("risk_probability", p.risk_probability), ("risk_flag", p.risk_flag),
            ("threshold", p.threshold), ("prediction_status", p.prediction_status),
            ("degraded_reason", p.degraded_reason), ("risk_score_kind", p.risk_score_kind),
            ("model_version", p.model_version), ("feature_schema_version", p.feature_schema_version),
            ("reference_snapshot_id", p.reference_snapshot_id), ("reference_hash", p.reference_hash),
        ))

    def _advance_card(self, connection, p, now):
        if p.prediction_status != "available" or p.risk_flag is not True:
            return None
        if p.risk_probability is None:
            raise PredictionHistoryError("positive prediction requires risk_probability")
        row = self._one(connection, """SELECT * FROM forecast_cards WHERE object_id=%s
            AND status IN ('active','uncertain') ORDER BY last_positive_snapshot_ts DESC LIMIT 1 FOR UPDATE""", (p.object_id,))
        if row and p.snapshot_ts - _parse_dt(row["last_positive_snapshot_ts"]) <= MERGE_GAP:
            if (row["model_version"],row["feature_schema_version"],row["reference_hash"]) != (
                p.model_version,p.feature_schema_version,p.reference_hash):
                raise ProvenanceConflictError("positive prediction provenance is incompatible with active card")
            connection.execute("""UPDATE forecast_cards SET status='active',last_positive_snapshot_ts=%s,
                max_probability=%s,threshold=%s,updated_at=%s WHERE card_id=%s""",
                (p.snapshot_ts,max(row["max_probability"],p.risk_probability),p.threshold,now,row["card_id"]))
            return self._row_card(self._one(connection,"SELECT * FROM forecast_cards WHERE card_id=%s",(row["card_id"],)))
        if row:
            connection.execute("UPDATE forecast_cards SET status='closed',updated_at=%s WHERE card_id=%s", (now,row["card_id"]))
        card_id = f"object:{p.object_id}:{_iso(p.snapshot_ts)}"
        connection.execute("""INSERT INTO forecast_cards
            (card_id,object_id,first_positive_snapshot_ts,last_positive_snapshot_ts,max_probability,threshold,
             model_version,feature_schema_version,reference_snapshot_id,reference_hash,status,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s)""",
            (card_id,p.object_id,p.snapshot_ts,p.snapshot_ts,p.risk_probability,p.threshold,p.model_version,
             p.feature_schema_version,p.reference_snapshot_id,p.reference_hash,now,now))
        return self._row_card(self._one(connection,"SELECT * FROM forecast_cards WHERE card_id=%s",(card_id,)))

    def _finalize_watermark(self, connection, p, now):
        rows = self._all(connection,"""SELECT * FROM forecast_cards WHERE object_id=%s
            AND status IN ('active','uncertain') AND last_positive_snapshot_ts <= %s FOR UPDATE""",
            (p.object_id,p.snapshot_ts-MERGE_GAP))
        for row in rows:
            status = "uncertain" if p.prediction_status == "degraded" else "closed"
            connection.execute("UPDATE forecast_cards SET status=%s,updated_at=%s WHERE card_id=%s",
                               (status,now,row["card_id"]))
