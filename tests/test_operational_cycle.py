from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.operational_cycle import (
    CycleAlreadyRunning,
    due_canonical_snapshots,
    OperationalForecastCycleService,
    ReferenceContractError,
    SQLiteOperationalCycleRepository,
)
from backend.app.services.prediction_history import OperationalForecastService, SQLitePredictionHistoryRepository


SNAPSHOT = datetime(2024, 1, 1, 2, 59, 59, tzinfo=timezone.utc)  # 05:59:59 Europe/Moscow
OBJECTS = list(range(1, 79))


class FakeProvider:
    def __init__(self, *, reference=True):
        self.reference = reference

    def metadata(self):
        return ({"reference_snapshot_id": "ref-v1", "reference_hash": "hash-v1"}
                if self.reference else {})


class FakeInference:
    def __init__(self, *, degraded_ids=(), reference=True, fail=False):
        self.provider = FakeProvider(reference=reference)
        self.degraded_ids = set(degraded_ids)
        self.fail = fail
        self.calls = 0

    def batch_predict(self, requests):
        self.calls += 1
        if self.fail:
            raise RuntimeError("synthetic runner failure")
        return [{
            "object_id": object_id, "snapshot_ts": snapshot_ts.isoformat(),
            "generated_at": snapshot_ts.isoformat(),
            "risk_probability": None if object_id in self.degraded_ids else .5,
            "risk_flag": None if object_id in self.degraded_ids else object_id % 2 == 0,
            "threshold": None if object_id in self.degraded_ids else .3987734643,
            "status": "degraded" if object_id in self.degraded_ids else "available",
            "risk_score_kind": "ml",
            "degraded_reason": "provider_unavailable" if object_id in self.degraded_ids else None,
            "model_version": None if object_id in self.degraded_ids else "model-v1",
            "feature_schema_version": None if object_id in self.degraded_ids else "schema-v1",
            "reference_snapshot_id": None if object_id in self.degraded_ids else "ref-v1",
            "reference_hash": None if object_id in self.degraded_ids else "hash-v1",
        } for object_id, snapshot_ts in requests]


def make_service(tmp_path, inference=None, forecast=None, cycles=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return OperationalForecastCycleService(
        inference or FakeInference(), forecast or OperationalForecastService(SQLitePredictionHistoryRepository(tmp_path / "history.db")),
        cycles or SQLiteOperationalCycleRepository(tmp_path / "cycles.db"), lambda: OBJECTS,
    )


def test_full_78_object_cycle_and_idempotent_rerun(tmp_path):
    inference = FakeInference()
    service = make_service(tmp_path, inference=inference)
    result = service.run_once(snapshot_ts=SNAPSHOT)
    assert (result.status, result.object_count, result.available_count, result.degraded_count, result.positive_count) == (
        "completed", 78, 78, 0, 39)
    again = service.run_once(snapshot_ts=SNAPSHOT)
    assert again.cycle_id == result.cycle_id and inference.calls == 1


def test_partial_degradation_is_counted_and_persisted(tmp_path):
    service = make_service(tmp_path, inference=FakeInference(degraded_ids={1, 3, 5}))
    result = service.run_once(snapshot_ts=SNAPSHOT)
    assert result.available_count == 75 and result.degraded_count == 3


def test_reference_mismatch_fails_closed_before_cycle(tmp_path):
    service = make_service(tmp_path, inference=FakeInference(reference=False))
    with pytest.raises(ReferenceContractError):
        service.run_once(snapshot_ts=SNAPSHOT)
    assert service.cycle_repository.get(SNAPSHOT) is None


def test_crash_then_rerun_resumes_via_prediction_idempotency(tmp_path):
    failing = make_service(tmp_path, inference=FakeInference(fail=True))
    with pytest.raises(RuntimeError):
        failing.run_once(snapshot_ts=SNAPSHOT)
    assert failing.cycle_repository.get(SNAPSHOT).status == "failed"
    resumed = make_service(tmp_path, inference=FakeInference())
    assert resumed.run_once(snapshot_ts=SNAPSHOT).status == "completed"


def test_cycle_lease_blocks_duplicate_running_worker(tmp_path):
    cycles = SQLiteOperationalCycleRepository(tmp_path / "cycles.db", lease_seconds=3600)
    cycles.begin(SNAPSHOT, 78)
    service = make_service(tmp_path, cycles=cycles)
    with pytest.raises(CycleAlreadyRunning):
        service.run_once(snapshot_ts=SNAPSHOT)


def test_same_cycle_concurrent_attempts_have_one_completed_record(tmp_path):
    service = make_service(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.run_once, snapshot_ts=SNAPSHOT) for _ in range(2)]
        results = []
        rejected = 0
        for future in futures:
            try:
                results.append(future.result())
            except CycleAlreadyRunning:
                rejected += 1
    assert rejected + len(results) == 2 and results and results[0].status == "completed"
    assert len(service.forecast_service.repository.history(1)) == 1


def test_due_snapshots_are_enumerated_oldest_first():
    due = due_canonical_snapshots(SNAPSHOT, SNAPSHOT + timedelta(hours=12))
    assert due == [SNAPSHOT + timedelta(hours=6), SNAPSHOT + timedelta(hours=12)]


def test_bootstrap_processes_only_latest_not_historical_years(tmp_path):
    service = make_service(tmp_path)
    results = service.run_due(now=datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc))
    assert len(results) == 1 and results[0].run_mode == "bootstrap"
    assert results[0].snapshot_ts == datetime(2024, 1, 1, 14, 59, 59, tzinfo=timezone.utc)


def test_missed_cycles_are_caught_up_in_order(tmp_path):
    service = make_service(tmp_path)
    service.run_once(snapshot_ts=SNAPSHOT)
    results = service.run_due(now=datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc))
    assert [item.snapshot_ts for item in results] == [
        SNAPSHOT + timedelta(hours=6), SNAPSHOT + timedelta(hours=12)
    ]
    assert all(item.run_mode == "catch_up" for item in results)
    assert service.cycle_repository.latest_completed_snapshot() == SNAPSHOT + timedelta(hours=12)


def test_unreconstructable_missed_cycle_is_explicit_and_not_silently_skipped(tmp_path):
    service = make_service(tmp_path)
    service.run_once(snapshot_ts=SNAPSHOT)
    service.inference_service = FakeInference(reference=False)
    results = service.run_due(now=datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc))
    assert len(results) == 1 and results[0].status == "unavailable"
    assert results[0].run_mode == "catch_up"
    assert service.cycle_repository.latest_completed_snapshot() == SNAPSHOT


def test_catch_up_card_history_matches_uninterrupted_sequence(tmp_path):
    uninterrupted = make_service(tmp_path / "uninterrupted")
    for snapshot in (SNAPSHOT, SNAPSHOT + timedelta(hours=6), SNAPSHOT + timedelta(hours=12)):
        uninterrupted.run_once(snapshot_ts=snapshot)
    caught_up = make_service(tmp_path / "caught-up")
    caught_up.run_once(snapshot_ts=SNAPSHOT)
    caught_up.run_due(now=datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc))
    def semantic(snapshot):
        return {
            "history": [{k: v for k, v in row.items() if k != "created_at"}
                        for row in snapshot["history"]],
            "cards": [{k: v for k, v in row.items() if k not in {"created_at", "updated_at"}}
                      for row in snapshot["cards"]],
        }
    assert semantic(caught_up.forecast_service.repository.snapshot()) == semantic(uninterrupted.forecast_service.repository.snapshot())
