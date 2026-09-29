from copy import deepcopy
from datetime import timedelta

from backend.app.config import utc_now
from backend.app.services.repositories import (
    DuckDBEventHistoryRepository, InMemoryMaintenanceRepository,
    InMemoryNotificationRepository, InMemoryPredictionOutcomeRepository,
    InMemoryPredictionRepository, InMemoryPredictionReviewRepository, InMemoryAuditRepository,
)

_PREDICTION_TEMPLATES = [
    {"id": 1042, "scope_type": "channel", "incident_type": "proxy_event",
     "object_id": 28, "channel_id": 2866, "system_ref": None,
     "score_kind": "mock", "model_version": "mock-v1", "risk_score": 0.82,
     "risk_level": "high", "forecast_from_hours": 24, "forecast_to_hours": 48,
     "factors": ["Рост частоты тревог", "Изменение интервалов между событиями"],
     "recommendations": [{"prediction_id": 1042, "code": "inspect-channel",
                           "title": "Проверить канал",
                           "description": "Проверить датчик и линию связи по регламенту.",
                           "source": "mock"}],
     "status": "active", "dispatcher_result": None},
    {"id": 1043, "scope_type": "object", "incident_type": "anomaly",
     "object_id": 28, "channel_id": None, "system_ref": None,
     "score_kind": "mock", "model_version": "mock-v1", "risk_score": 0.61,
     "risk_level": "medium", "forecast_from_hours": 24, "forecast_to_hours": 72,
     "factors": ["Аномальная активность нескольких каналов объекта"],
     "status": "active", "dispatcher_result": None},
    {"id": 1044, "scope_type": "engineering_system",
     "incident_type": "engineering_system_failure", "object_id": 28,
     "channel_id": None, "system_ref": "mock:object-28:security-system",
     "score_kind": "mock", "model_version": "mock-v1", "risk_score": 0.58,
     "risk_level": "medium", "forecast_from_hours": 24, "forecast_to_hours": 48,
     "factors": ["Согласованное изменение активности связанных каналов"],
     "status": "active", "dispatcher_result": None},
]


def _demo_clock():
    return utc_now()


def _demo_predictions() -> list[dict]:
    now = _demo_clock()
    predictions = deepcopy(_PREDICTION_TEMPLATES)
    for index, item in enumerate(predictions):
        item["created_at"] = (now - timedelta(minutes=index + 1)).isoformat()
    return predictions

def _demo_maintenance_requests() -> list[dict]:
    timestamp = _demo_clock().isoformat()
    return [{
        "id": 501, "object_id": 28, "channel_id": 2866, "prediction_id": 1042,
        "reason": "Высокий демонстрационный score", "priority": "high", "status": "draft",
        "source": "local", "external_request_id": None, "sync_status": "not_synced",
        "external_status": None, "created_at": timestamp, "updated_at": timestamp,
    }]


def _demo_notifications() -> list[dict]:
    timestamp = (_demo_clock() - timedelta(seconds=30)).isoformat()
    return [{
        "id": 701, "type": "risk", "severity": "critical", "title": "Высокий уровень риска",
        "message": "Канал #2866 требует внимания (демонстрационный score)",
        "prediction_id": 1042, "object_id": 28, "channel_id": 2866,
        "created_at": timestamp,
    }]

prediction_repository = InMemoryPredictionRepository(
    _demo_predictions, {28: {}}, {2866: {"object_id": 28}}, clock=_demo_clock
)
prediction_review_repository = InMemoryPredictionReviewRepository()
prediction_outcome_repository = InMemoryPredictionOutcomeRepository()
maintenance_repository = InMemoryMaintenanceRepository(_demo_maintenance_requests)
notification_repository = InMemoryNotificationRepository(_demo_notifications)
audit_repository = InMemoryAuditRepository()
event_history_repository = DuckDBEventHistoryRepository()


def reset_mock_storage() -> None:
    prediction_repository.reset()
    prediction_review_repository.reset()
    prediction_outcome_repository.reset()
    maintenance_repository.reset()
    notification_repository.reset()
    audit_repository.reset()
