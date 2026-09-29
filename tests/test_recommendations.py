from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from backend.app.services.prediction_history import OperationalForecastService, SQLitePredictionHistoryRepository
from backend.app.services.recommendations import (
    RECOMMENDATION_RULE_VERSION,
    RecommendationService,
    SQLiteRecommendationRepository,
)


def result(status="available", flag=True, probability=.8, object_id=1):
    return {
        "object_id": object_id, "snapshot_ts": "2024-01-01T02:59:59+00:00",
        "generated_at": "2024-01-01T03:00:00+00:00", "risk_probability": probability if status == "available" else None,
        "risk_flag": flag if status == "available" else None, "threshold": .3987734643 if status == "available" else None,
        "status": status, "risk_score_kind": "ml", "degraded_reason": "provider_unavailable" if status == "degraded" else None,
        "model_version": "model-v1" if status == "available" else None,
        "feature_schema_version": "schema-v1" if status == "available" else None,
        "reference_snapshot_id": "ref-v1" if status == "available" else None,
        "reference_hash": "hash-v1" if status == "available" else None,
    }


def make_service(tmp_path):
    rec = RecommendationService(SQLiteRecommendationRepository(tmp_path / "recommendations.db"))
    return OperationalForecastService(SQLitePredictionHistoryRepository(tmp_path / "history.db"), rec)


def test_positive_recommendation_is_deterministic_and_versioned(tmp_path):
    service = make_service(tmp_path)
    record, card, inserted = service.record(result())
    recommendation = service.recommendations(1)[0]
    assert inserted and card is not None
    assert recommendation.status == "available"
    assert recommendation.code == "CHECK_OBJECT"
    assert recommendation.rule_version == RECOMMENDATION_RULE_VERSION
    service.record(result())
    assert len(service.recommendations(1)) == 1


def test_negative_is_not_required_and_degraded_is_not_a_valid_action(tmp_path):
    service = make_service(tmp_path)
    service.record(result(flag=False, probability=.1))
    negative = service.recommendations(1)[0]
    assert negative.status == "not_required" and negative.action is None
    service.record({**result(status="degraded"), "snapshot_ts": "2024-01-01T08:59:59+00:00"})
    degraded = service.recommendations(1)[0]
    assert degraded.status == "degraded" and degraded.code == "VERIFY_DATA_AVAILABILITY"


def test_persistent_card_gets_review_recommendation_without_new_card(tmp_path):
    service = make_service(tmp_path)
    service.record(result())
    service.record({**result(probability=.9), "snapshot_ts": "2024-01-01T08:59:59+00:00"})
    items = service.recommendations(1)
    assert len(items) == 2 and items[0].code == "PRIORITIZE_OPERATOR_REVIEW"


def test_recommendation_history_survives_repository_restart(tmp_path):
    service = make_service(tmp_path)
    service.record(result())
    expected = service.recommendations(1)[0]
    restored = RecommendationService(SQLiteRecommendationRepository(tmp_path / "recommendations.db"))
    actual = restored.repository.list_for_object(1)[0]
    assert actual.recommendation_id == expected.recommendation_id
    assert actual.action == expected.action and actual.generated_at == expected.generated_at


def test_repository_primary_key_is_concurrency_safe_without_card(tmp_path):
    service = make_service(tmp_path)
    record, _, _ = service.record(result(flag=False, probability=.1))
    recommendation = service.recommendations(1)[0]
    repo = service.recommendation_service.repository
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: repo.save(recommendation), range(8)))
    assert all(row.recommendation_id == recommendation.recommendation_id for row in rows)
    assert len(repo.list_for_object(1)) == 1


def test_recommendation_failure_is_recoverable_without_rerunning_ml(tmp_path):
    class FailingRecommendationService:
        def generate(self, record, card):
            raise RuntimeError("recommendation storage unavailable")

    from backend.app.services.recommendations import RecommendationService
    history = OperationalForecastService(SQLitePredictionHistoryRepository(tmp_path / "history.db"), FailingRecommendationService())
    record, card, _ = history.record(result())
    good = RecommendationService(SQLiteRecommendationRepository(tmp_path / "recommendations.db"))
    rebuilt = good.generate(record, card)
    assert rebuilt.status == "available"
    assert len(good.repository.list_for_object(1)) == 1
