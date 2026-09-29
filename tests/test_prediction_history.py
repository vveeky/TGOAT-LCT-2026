from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.prediction_history import (
    OperationalPrediction,
    OutOfOrderPredictionError,
    PredictionIdentityConflictError,
    ProvenanceConflictError,
    SQLitePredictionHistoryRepository,
)
from backend.app.services.operational_actions import (
    OperationalPredictionNotActionable,
    SQLiteOperationalActionRepository,
)


BASE = datetime(2024, 1, 1, 5, 59, 59, tzinfo=timezone.utc)


def prediction(ts, *, object_id=1, positive=True, status="available", probability=.5,
               model="risk_baseline_v1_phase1_1_hgb", schema="risk_baseline_v1_phase2",
               reference="ref-hash"):
    return OperationalPrediction(
        object_id=object_id, snapshot_ts=ts, generated_at=ts + timedelta(minutes=1),
        risk_probability=probability if status == "available" else None,
        risk_flag=positive if status == "available" else None,
        threshold=.3987734643 if status == "available" else None,
        prediction_status=status, degraded_reason="provider_unavailable" if status == "degraded" else None,
        risk_score_kind="ml", model_version=model if status == "available" else None,
        feature_schema_version=schema if status == "available" else None,
        reference_snapshot_id="snapshot" if status == "available" else None,
        reference_hash=reference if status == "available" else None,
    )


def process(repo, p):
    return repo.process_operational(p)


def test_positive_6h_12h_merge_and_max_score():
    repo = SQLitePredictionHistoryRepository()
    _, first, inserted = process(repo, prediction(BASE, probability=.4))
    _, second, _ = process(repo, prediction(BASE + timedelta(hours=6), probability=.9))
    _, third, _ = process(repo, prediction(BASE + timedelta(hours=12), probability=.6))
    assert inserted and first.card_id == second.card_id == third.card_id
    assert third.max_probability == .9
    assert len(repo.cards(1)) == 1


def test_positive_negative_positive_at_12h_is_same_card_and_negative_is_persisted():
    repo = SQLitePredictionHistoryRepository()
    _, first, _ = process(repo, prediction(BASE, probability=.5))
    process(repo, prediction(BASE + timedelta(hours=6), positive=False, probability=.1))
    _, third, _ = process(repo, prediction(BASE + timedelta(hours=12), probability=.7))
    assert third.card_id == first.card_id
    assert len(repo.history(1)) == 3
    assert repo.history(1)[1].prediction.risk_flag is False


def test_watermark_closes_card_after_processed_negative_merge_window():
    repo = SQLitePredictionHistoryRepository()
    _, first, _ = process(repo, prediction(BASE, probability=.5))
    process(repo, prediction(BASE + timedelta(hours=6), positive=False, probability=.1))
    process(repo, prediction(BASE + timedelta(hours=12), positive=False, probability=.1))
    assert first.card_id == repo.cards(1)[0].card_id
    assert repo.cards(1)[0].status == "closed"


def test_degraded_boundary_is_uncertain_then_available_retry_reopens_same_card():
    repo = SQLitePredictionHistoryRepository()
    _, first, _ = process(repo, prediction(BASE, probability=.5))
    process(repo, prediction(BASE + timedelta(hours=6), positive=False, probability=.1))
    process(repo, prediction(BASE + timedelta(hours=12), status="degraded"))
    assert repo.cards(1)[0].status == "uncertain"
    _, retried, inserted = process(repo, prediction(BASE + timedelta(hours=12), probability=.8))
    assert inserted and retried.card_id == first.card_id
    assert repo.cards(1)[0].status == "active"


def test_degraded_snapshot_can_upgrade_to_available_once():
    repo = SQLitePredictionHistoryRepository()
    degraded = prediction(BASE, status="degraded")
    process(repo, degraded)
    _, card, inserted = process(repo, prediction(BASE, probability=.8))
    assert inserted and card is not None
    assert len(repo.history(1)) == 1
    assert repo.history(1)[0].prediction.prediction_status == "available"


def test_degraded_prediction_cannot_be_reviewed_before_available_retry(tmp_path):
    path = tmp_path / "operational.sqlite"
    history = SQLitePredictionHistoryRepository(path)
    actions = SQLiteOperationalActionRepository(path)
    degraded, _, _ = process(history, prediction(BASE, status="degraded"))

    with pytest.raises(OperationalPredictionNotActionable):
        actions.add_review(
            degraded.identity, 1, actor_user_id="dispatcher", actor_role="central_dispatcher",
            decision="monitoring", reason_code=None, comment=None,
        )

    available, _, inserted = process(history, prediction(BASE, probability=.8))
    assert inserted and available.identity != degraded.identity
    assert actions.prediction(degraded.identity) is None
    assert actions.prediction(available.identity)["prediction_status"] == "available"
    review = actions.add_review(
        available.identity, 1, actor_user_id="dispatcher", actor_role="central_dispatcher",
        decision="monitoring", reason_code=None, comment=None,
    )
    assert review["prediction_identity"] == available.identity
    actions.close()
    history.close()


def test_gap_over_12h_closes_previous_and_creates_new_card():
    repo = SQLitePredictionHistoryRepository()
    _, first, _ = process(repo, prediction(BASE))
    _, second, _ = process(repo, prediction(BASE + timedelta(hours=18)))
    assert first.card_id != second.card_id
    assert [c.status for c in repo.cards(1)] == ["active", "closed"]


def test_degraded_is_unknown_and_does_not_break_merge_or_close_card():
    repo = SQLitePredictionHistoryRepository()
    _, first, _ = process(repo, prediction(BASE))
    _, no_card, inserted = process(repo, prediction(BASE + timedelta(hours=6), status="degraded"))
    _, second, _ = process(repo, prediction(BASE + timedelta(hours=12), probability=.8))
    assert inserted and no_card is None and second.card_id == first.card_id
    assert repo.history(1)[1].prediction.prediction_status == "degraded"
    assert repo.history(1)[1].prediction.risk_flag is None


def test_long_positive_sequence_remains_one_card():
    repo = SQLitePredictionHistoryRepository()
    cards = [process(repo, prediction(BASE + timedelta(hours=6 * i), probability=i / 100))[1]
             for i in range(30)]
    assert len(repo.cards(1)) == 1
    assert cards[-1].max_probability == .29
    assert cards[-1].last_positive_snapshot_ts == BASE + timedelta(hours=174)


def test_duplicate_is_idempotent_and_concurrent_duplicate_has_one_row():
    repo = SQLitePredictionHistoryRepository()
    p = prediction(BASE)
    first = process(repo, p)
    second = process(repo, p)
    assert first[0].identity == second[0].identity and second[2] is False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: process(repo, p), range(8)))
    assert all(item[2] is False for item in results)
    assert len(repo.history(1)) == 1 and len(repo.cards(1)) == 1


def test_duplicate_identity_with_new_generation_time_replays_existing_prediction():
    repo = SQLitePredictionHistoryRepository()
    original = prediction(BASE)
    process(repo, original)
    retried = prediction(BASE)
    retried = OperationalPrediction(**{**retried.__dict__, "generated_at": retried.generated_at + timedelta(seconds=5)})
    existing, card, inserted = process(repo, retried)
    assert inserted is False
    assert existing.prediction.generated_at == original.generated_at
    assert card is not None
    assert len(repo.history(1)) == 1


def test_duplicate_identity_with_incompatible_content_is_rejected():
    repo = SQLitePredictionHistoryRepository()
    process(repo, prediction(BASE, probability=.5))
    with pytest.raises(PredictionIdentityConflictError):
        process(repo, prediction(BASE, probability=.9))


def test_restart_equivalence(tmp_path):
    path = tmp_path / "history.sqlite"
    repo = SQLitePredictionHistoryRepository(path)
    process(repo, prediction(BASE, probability=.4))
    process(repo, prediction(BASE + timedelta(hours=6), probability=.8))
    expected = repo.snapshot()
    repo.close()
    restored = SQLitePredictionHistoryRepository(path)
    process(restored, prediction(BASE + timedelta(hours=12), probability=.6))
    assert restored.cards(1)[0].max_probability == .8
    assert len(restored.history(1)) == 3
    assert expected["cards"][0]["card_id"] == restored.cards(1)[0].card_id


def test_out_of_order_is_rejected_for_operational_path():
    repo = SQLitePredictionHistoryRepository()
    process(repo, prediction(BASE + timedelta(hours=6)))
    with pytest.raises(OutOfOrderPredictionError):
        process(repo, prediction(BASE))


def test_incompatible_provenance_is_explicit():
    repo = SQLitePredictionHistoryRepository()
    process(repo, prediction(BASE, reference="a"))
    with pytest.raises(ProvenanceConflictError):
        process(repo, prediction(BASE + timedelta(hours=6), reference="b"))
    assert len(repo.history(1)) == 1


def test_objects_never_merge_and_negative_does_not_create_card():
    repo = SQLitePredictionHistoryRepository()
    process(repo, prediction(BASE - timedelta(hours=6), object_id=1, positive=False))
    assert repo.cards(1) == []
    process(repo, prediction(BASE, object_id=1, positive=True))
    process(repo, prediction(BASE, object_id=2, positive=True))
    assert len(repo.cards(1)) == len(repo.cards(2)) == 1
