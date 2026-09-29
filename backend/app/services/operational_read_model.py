"""Coherent read projection for persisted operational ML state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from backend.app.config import utc_now
from backend.app.services.operational_cycle import OperationalCycleResult, SQLiteOperationalCycleRepository
from backend.app.services.prediction_history import ForecastCard, OperationalForecastService, PredictionHistoryRecord
from backend.app.services.ml_features import latest_canonical_snapshot


@dataclass(frozen=True)
class OperationalRiskState:
    object_id: int
    prediction_identity: str | None
    status: str
    snapshot_ts: datetime | None
    generated_at: datetime | None
    risk_probability: float | None
    risk_flag: bool | None
    threshold: float | None
    model_version: str | None
    feature_schema_version: str | None
    risk_score_kind: str
    degraded_reason: str | None
    card: ForecastCard | None
    recommendation: object | None


@dataclass(frozen=True)
class OperationalDashboard:
    cycle: OperationalCycleResult | None
    cycle_status: str
    stale: bool
    snapshot_ts: datetime | None
    object_count: int
    available_count: int
    positive_count: int
    negative_count: int
    degraded_count: int
    no_prediction_count: int
    items: tuple[OperationalRiskState, ...]


class OperationalRiskReadService:
    def __init__(self, forecast_service: OperationalForecastService,
                 cycle_repository: SQLiteOperationalCycleRepository,
                 object_source, clock: Callable[[], datetime] = utc_now,
                 latest_snapshot_provider: Callable[[], datetime | None] | None = None,
                 replay_mode: bool = False):
        self.forecast_service = forecast_service
        self.cycle_repository = cycle_repository
        self.object_source = object_source
        self.clock = clock
        self.latest_snapshot_provider = latest_snapshot_provider
        self.replay_mode = replay_mode

    def object_state(self, object_id: int, snapshot_ts: datetime | None = None) -> OperationalRiskState:
        history = self.forecast_service.history(object_id, limit=1000)
        record = next((item for item in history if snapshot_ts is None or item.prediction.snapshot_ts == snapshot_ts), None)
        cards = self.forecast_service.cards(object_id)
        active = next((card for card in cards if card.status in {"active", "uncertain"}), None)
        recommendation = None
        if record:
            recommendation = next((item for item in self.forecast_service.recommendations(object_id)
                                  if item.snapshot_ts == record.prediction.snapshot_ts), None)
            p = record.prediction
            return OperationalRiskState(
                object_id, record.identity, p.prediction_status, p.snapshot_ts, p.generated_at, p.risk_probability,
                p.risk_flag, p.threshold, p.model_version, p.feature_schema_version,
                p.risk_score_kind, p.degraded_reason, active, recommendation,
            )
        return OperationalRiskState(object_id, None, "no_prediction", None, None, None, None, None,
                                    None, None, "ml", "no_operational_prediction", active, recommendation)

    def dashboard(self, object_ids: Iterable[int]) -> OperationalDashboard:
        ids = tuple(sorted(set(object_ids)))
        latest = (self.latest_snapshot_provider() if self.latest_snapshot_provider
                  else self.cycle_repository.latest_completed_snapshot())
        cycle = self.cycle_repository.latest()
        expected = latest if self.replay_mode else latest_canonical_snapshot(now=self.clock())
        stale = latest is not None and (
            latest < expected
            or (cycle is not None and cycle.status != "completed" and cycle.snapshot_ts >= latest)
        )
        cycle_status = ("completed" if self.replay_mode and latest is not None else
                        "no_cycle" if cycle is None else ("stale" if stale else cycle.status))
        items = tuple(self.object_state(object_id, latest) for object_id in ids)
        available = sum(item.status == "available" for item in items)
        positive = sum(item.status == "available" and item.risk_flag is True for item in items)
        negative = sum(item.status == "available" and item.risk_flag is False for item in items)
        degraded = sum(item.status == "degraded" for item in items)
        no_prediction = sum(item.status == "no_prediction" for item in items)
        return OperationalDashboard(cycle, cycle_status, stale, latest, len(ids), available,
                                    positive, negative, degraded, no_prediction, items)
