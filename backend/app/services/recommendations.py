"""Deterministic operator decision-support rules, separate from ML output."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.app.config import as_utc, utc_now
from backend.app.services.sqlite_support import connect_sqlite


RECOMMENDATION_RULE_VERSION = "rules_v1"


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    object_id: int
    snapshot_ts: datetime
    card_id: str | None
    status: str
    code: str | None
    action: str | None
    basis: tuple[str, ...]
    rule_version: str
    generated_at: datetime
    model_version: str | None
    reference_hash: str | None


class SQLiteRecommendationRepository:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._connection = connect_sqlite(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("""CREATE TABLE IF NOT EXISTS recommendations (
                recommendation_id TEXT PRIMARY KEY, object_id INTEGER NOT NULL,
                snapshot_ts TEXT NOT NULL, card_id TEXT, status TEXT NOT NULL,
                code TEXT, action TEXT, basis_json TEXT NOT NULL, rule_version TEXT NOT NULL,
                generated_at TEXT NOT NULL, model_version TEXT, reference_hash TEXT,
                UNIQUE(object_id, snapshot_ts, card_id, rule_version)
            )""")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def save(self, recommendation: Recommendation) -> Recommendation:
        with self._lock, self._connection:
            self._connection.execute("""INSERT OR IGNORE INTO recommendations
                (recommendation_id, object_id, snapshot_ts, card_id, status, code, action,
                 basis_json, rule_version, generated_at, model_version, reference_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                recommendation.recommendation_id, recommendation.object_id,
                recommendation.snapshot_ts.isoformat(), recommendation.card_id,
                recommendation.status, recommendation.code, recommendation.action,
                ",".join(recommendation.basis), recommendation.rule_version,
                recommendation.generated_at.isoformat(), recommendation.model_version,
                recommendation.reference_hash))
            row = self._connection.execute(
                "SELECT * FROM recommendations WHERE recommendation_id=?", (recommendation.recommendation_id,)).fetchone()
            return self._from_row(row)

    def list_for_object(self, object_id: int) -> list[Recommendation]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM recommendations WHERE object_id=? ORDER BY snapshot_ts DESC", (object_id,)).fetchall()
            return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Recommendation:
        return Recommendation(
            recommendation_id=row["recommendation_id"], object_id=row["object_id"],
            snapshot_ts=datetime.fromisoformat(row["snapshot_ts"]), card_id=row["card_id"],
            status=row["status"], code=row["code"], action=row["action"],
            basis=tuple(filter(None, row["basis_json"].split(","))),
            rule_version=row["rule_version"], generated_at=datetime.fromisoformat(row["generated_at"]),
            model_version=row["model_version"], reference_hash=row["reference_hash"],
        )


class RecommendationService:
    """Rules use only current operational result/card facts; never targets/future events."""

    def __init__(self, repository: SQLiteRecommendationRepository,
                 rule_version: str = RECOMMENDATION_RULE_VERSION):
        self.repository = repository
        self.rule_version = rule_version

    def generate(self, record, card=None) -> Recommendation:
        p = record.prediction
        card_id = card.card_id if card else None
        identity = f"{record.identity}|card:{card_id or 'none'}|rules:{self.rule_version}"
        if p.prediction_status == "degraded":
            status, code, action, basis = "degraded", "VERIFY_DATA_AVAILABILITY", \
                "Проверить доступность телеметрии и повторить прогноз после восстановления данных.", ("ml_degraded",)
        elif p.risk_flag is not True:
            status, code, action, basis = "not_required", None, None, ("ml_negative",)
        elif card and card.first_positive_snapshot_ts == card.last_positive_snapshot_ts:
            status, code, action, basis = "available", "CHECK_OBJECT", \
                "Проверить объект и подтвердить отклонение перед созданием ремонтной заявки.", ("ml_positive", "new_card")
        else:
            status, code, action, basis = "available", "PRIORITIZE_OPERATOR_REVIEW", \
                "Приоритизировать проверку продолжающегося риска и последних тревожных событий.", ("ml_positive", "persistent_card")
        return self.repository.save(Recommendation(
            recommendation_id=identity, object_id=p.object_id, snapshot_ts=as_utc(p.snapshot_ts),
            card_id=card_id, status=status, code=code, action=action, basis=basis,
            rule_version=self.rule_version, generated_at=utc_now(),
            model_version=p.model_version, reference_hash=p.reference_hash))
