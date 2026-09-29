"""Application-lifecycle construction for the frozen ML service."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from backend.app.services.ml_features import (
    FeatureProviderError,
    SQLiteFeatureStateStore,
    StateFeatureProvider,
)
from backend.app.services.risk_inference import RiskInferenceService
from backend.app.services.prediction_history import OperationalForecastService, SQLitePredictionHistoryRepository
from backend.app.services.operational_cycle import OperationalForecastCycleService, SQLiteOperationalCycleRepository
from backend.app.services.reference_data import get_objects_data, get_reference_meta
from backend.app.services.recommendations import RecommendationService, SQLiteRecommendationRepository
from backend.app.services.operational_actions import SQLiteOperationalActionRepository
from backend.app.services.operational_notifications import SQLiteOperationalNotificationRepository
from backend.app.services.operational_read_model import OperationalRiskReadService


def _history_database_path() -> str:
    configured = os.environ.get("TGOAT_PREDICTION_HISTORY_DB")
    if configured:
        return configured
    # Named shared-memory URI lets independent repositories share the
    # ephemeral database while retaining separate SQLite connections.
    return f"file:tgoat_operational_{uuid4().hex}?mode=memory&cache=shared"


class UnavailableFeatureProvider:
    def __init__(self, reason: str = "provider state is unavailable"):
        self.reason = reason

    def snapshot(self, object_id, snapshot_ts):
        raise FeatureProviderError(self.reason)

    def metadata(self):
        return {"reference_snapshot_id": None, "reference_hash": None, "available_to": None}


def build_risk_inference_service() -> RiskInferenceService:
    state_path = os.environ.get("TGOAT_FEATURE_STATE_DB")
    provider = UnavailableFeatureProvider()
    if state_path and Path(state_path).is_file():
        try:
            reference = get_reference_meta()
            state = SQLiteFeatureStateStore(state_path)
            provider = StateFeatureProvider(
                state,
                expected_reference_snapshot_id=f"reference-v{reference['schema_version']}",
                expected_reference_hash=reference["source_sha256"],
                require_complete_coverage=True,
            )
        except FeatureProviderError as error:
            provider = UnavailableFeatureProvider(str(error))
        except Exception:
            # A corrupt or incompatible persisted DB must degrade, never prevent
            # API startup or be mistaken for a complete source state.
            provider = UnavailableFeatureProvider()
    return RiskInferenceService(provider)


def build_operational_forecast_service() -> OperationalForecastService:
    history_path = _history_database_path()
    recommendation_path = os.environ.get("TGOAT_RECOMMENDATION_DB", history_path)
    repository = SQLitePredictionHistoryRepository(history_path)
    notifications = SQLiteOperationalNotificationRepository(history_path)
    return OperationalForecastService(
        repository,
        RecommendationService(SQLiteRecommendationRepository(recommendation_path)),
        notifications,
    )


def build_operational_cycle_service() -> OperationalForecastCycleService:
    history_path = _history_database_path()
    cycle_path = os.environ.get("TGOAT_OPERATIONAL_CYCLE_DB", history_path)
    recommendation_path = os.environ.get("TGOAT_RECOMMENDATION_DB", history_path)
    history = OperationalForecastService(
        SQLitePredictionHistoryRepository(history_path),
        RecommendationService(SQLiteRecommendationRepository(recommendation_path)),
        SQLiteOperationalNotificationRepository(history_path),
    )
    reference_object_ids = sorted(
        int(item["object_id"]) for item in get_objects_data() if item["has_channels"]
    )
    selected_ids = os.environ.get("TGOAT_OPERATIONAL_OBJECT_IDS")
    if selected_ids:
        try:
            object_ids = sorted({int(value.strip()) for value in selected_ids.split(",") if value.strip()})
        except ValueError as error:
            raise RuntimeError("TGOAT_OPERATIONAL_OBJECT_IDS must be comma-separated integers") from error
        unknown = sorted(set(object_ids) - set(reference_object_ids))
        if not object_ids or unknown:
            raise RuntimeError(f"TGOAT_OPERATIONAL_OBJECT_IDS contains unknown/empty ids: {unknown}")
    else:
        object_ids = reference_object_ids
    return OperationalForecastCycleService(
        build_risk_inference_service(), history,
        SQLiteOperationalCycleRepository(cycle_path),
        object_source=lambda: object_ids,
        expected_object_count=len(object_ids) if selected_ids else 78,
    )


def build_postgres_operational_runtime():
    """Construct the normal operational contour on one required PostgreSQL DB."""
    from backend.app.services.postgres_storage import PostgreSQLDatabase
    from backend.app.services.postgres_prediction_history import PostgreSQLPredictionHistoryRepository
    from backend.app.services.postgres_projections import (
        PostgreSQLRecommendationRepository,
        PostgreSQLOperationalNotificationRepository,
        PostgreSQLOperationalCycleRepository,
    )
    from backend.app.services.postgres_actions import PostgreSQLOperationalActionRepository
    from backend.app.services.postgres_dispatcher_workflow import PostgreSQLDispatcherWorkflow

    database = PostgreSQLDatabase(os.environ.get("TGOAT_OPERATIONAL_DATABASE_URL", ""))
    database.open()
    try:
        database.migrate()
        history = PostgreSQLPredictionHistoryRepository(database)
        notifications = PostgreSQLOperationalNotificationRepository(database)
        recommendations = RecommendationService(PostgreSQLRecommendationRepository(database))
        forecast = OperationalForecastService(history, recommendations, notifications)
        reference_object_ids = sorted(
            int(item["object_id"]) for item in get_objects_data() if item["has_channels"]
        )
        selected_ids = os.environ.get("TGOAT_OPERATIONAL_OBJECT_IDS")
        if selected_ids:
            try:
                object_ids = sorted({int(value.strip()) for value in selected_ids.split(",") if value.strip()})
            except ValueError as error:
                raise RuntimeError("TGOAT_OPERATIONAL_OBJECT_IDS must be comma-separated integers") from error
            unknown = sorted(set(object_ids) - set(reference_object_ids))
            if not object_ids or unknown:
                raise RuntimeError(f"TGOAT_OPERATIONAL_OBJECT_IDS contains unknown/empty ids: {unknown}")
        else:
            object_ids = reference_object_ids
        cycle_repository = PostgreSQLOperationalCycleRepository(database)
        cycle = OperationalForecastCycleService(
            build_risk_inference_service(), forecast, cycle_repository,
            object_source=lambda: object_ids,
            expected_object_count=len(object_ids) if selected_ids else 78,
        )
        if os.environ.get("TGOAT_SIMULATED_LIVE_MODE", "0") == "1":
            from backend.app.services.live_status import current_simulated_watermark
            def committed_simulated_time():
                progress = current_simulated_watermark(database)
                return (datetime.fromisoformat(progress["processed_at"])
                        if progress is not None else None)
            read_model = OperationalRiskReadService(
                forecast, cycle_repository, lambda: object_ids,
                latest_snapshot_provider=committed_simulated_time,
                replay_mode=True,
            )
        else:
            read_model = OperationalRiskReadService(
                forecast, cycle_repository, lambda: object_ids,
            )
        return {
            "database": database,
            "cycle": cycle,
            "workflow": PostgreSQLDispatcherWorkflow(database, history),
            "actions": PostgreSQLOperationalActionRepository(database),
            "notifications": notifications,
            "read_model": read_model,
        }
    except Exception:
        database.close()
        raise
