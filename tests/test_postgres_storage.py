"""Real-PostgreSQL integration checks; set TGOAT_TEST_POSTGRES_URL to enable."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.postgres_storage import PostgreSQLDatabase
from backend.app.services.postgres_prediction_history import PostgreSQLPredictionHistoryRepository
from backend.app.services.prediction_history import OperationalPrediction
from backend.app.services.postgres_projections import (
    PostgreSQLRecommendationRepository,
    PostgreSQLOperationalNotificationRepository,
)
from backend.app.services.recommendations import RecommendationService
from backend.app.services.postgres_dispatcher_workflow import PostgreSQLDispatcherWorkflow
from backend.app.services.postgres_audit import PostgreSQLAuditRepository
from backend.app.services.auth import AuthUser
from backend.app.services.postgres_actions import PostgreSQLOperationalActionRepository
from backend.app.services.postgres_projections import PostgreSQLOperationalCycleRepository
from backend.app.services.operational_cycle import CycleAlreadyRunning
from backend.app.services.operational_actions import OperationalActionIdempotencyConflict


def _create_postgres_inspection(database, idempotency_key):
    object_id = int(uuid4().int % 1_000_000_000) + 1
    at = datetime.now(timezone.utc) + timedelta(seconds=uuid4().int % 100_000)
    history = PostgreSQLPredictionHistoryRepository(database)
    record, card, inserted = history.process_operational(OperationalPrediction(
        object_id, at, at, .91, True, .4, "available", None, "ml", "frozen-v1",
        "87-v1", "reference-v1", "reference-hash",
    ))
    assert inserted
    workflow = PostgreSQLDispatcherWorkflow(database, history)
    payload = {
        "expected_version": 0,
        "idempotency_key": idempotency_key,
        "decision": "inspection_requested",
        "prediction_identity": record.identity,
        "reason_code": None,
        "comment": "Regression audit creation",
        "request": {"reason": "Regression audit request", "priority": "normal"},
    }
    context = {"user_id": "pg-audit-regression", "actor_roles": ["central_dispatcher"]}
    return object_id, card, workflow, payload, context


POSTGRES_URL = os.environ.get("TGOAT_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TGOAT_TEST_POSTGRES_URL not configured")


@pytest.fixture(scope="module")
def database():
    database = PostgreSQLDatabase(POSTGRES_URL)
    database.open()
    database.migrate()
    yield database
    database.close()


def test_signal_request_creation_audit_is_atomic_and_idempotent(database):
    object_id, card, workflow, payload, context = _create_postgres_inspection(
        database, f"audit-create-{uuid4()}"
    )
    first = workflow.decide(
        card.card_id, actor_user_id=context["user_id"], actor_role="central_dispatcher",
        payload=payload, audit_context=context,
    )
    repeated = workflow.decide(
        card.card_id, actor_user_id=context["user_id"], actor_role="central_dispatcher",
        payload=payload, audit_context=context,
    )

    assert repeated == first
    request_id = first["request"]["id"]
    with database.pool.connection() as connection:
        events = connection.execute(
            """SELECT timestamp, actor_user_id, action, resource_type, resource_id,
                      object_id, metadata
               FROM audit_events
               WHERE action='maintenance_request.created' AND resource_id=%s
                 AND object_id=%s""",
            (str(request_id), object_id),
        ).fetchall()
        assert connection.execute(
            "SELECT count(*) FROM maintenance_requests WHERE card_id=%s", (card.card_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM signal_command_receipts WHERE idempotency_key=%s",
            (payload["idempotency_key"],),
        ).fetchone()[0] == 1

    assert len(events) == 1
    timestamp, user_id, action, entity_type, entity_id, event_object_id, metadata = events[0]
    assert user_id == context["user_id"]
    assert action == "maintenance_request.created"
    assert entity_type == "maintenance_request"
    assert entity_id == str(request_id)
    assert event_object_id == object_id
    assert timestamp == datetime.fromisoformat(first["request"]["created_at"])
    assert metadata["decision_id"] == first["decision_id"]
    assert metadata["signal_card_id"] == card.card_id
    assert metadata["prediction_identity"] == payload["prediction_identity"]


def test_signal_request_audit_failure_rolls_back_postgres_command(database, monkeypatch):
    object_id, card, workflow, payload, context = _create_postgres_inspection(
        database, f"audit-rollback-{uuid4()}"
    )
    append = PostgreSQLAuditRepository.append_in_connection

    def append_then_fail(connection, event):
        append(connection, event)
        raise RuntimeError("injected domain audit failure")

    monkeypatch.setattr(
        PostgreSQLAuditRepository, "append_in_connection", staticmethod(append_then_fail),
    )
    with pytest.raises(RuntimeError, match="injected domain audit failure"):
        workflow.decide(
            card.card_id, actor_user_id=context["user_id"], actor_role="central_dispatcher",
            payload=payload, audit_context=context,
        )

    with database.pool.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM signal_decisions WHERE card_id=%s", (card.card_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM signal_work WHERE card_id=%s", (card.card_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM maintenance_requests WHERE card_id=%s", (card.card_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM signal_command_receipts WHERE idempotency_key=%s",
            (payload["idempotency_key"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT count(*) FROM audit_events
               WHERE action='maintenance_request.created' AND object_id=%s
                 AND metadata->>'signal_card_id'=%s""",
            (object_id, card.card_id),
        ).fetchone()[0] == 0


def test_migrations_are_idempotent_and_require_supported_postgres(database):
    assert database.migrate() == []
    assert int(database.healthcheck()["server_version"].split(".", 1)[0]) >= 12


def test_concurrent_clean_database_startup_applies_migration_once(database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    schema = "migration_race_" + uuid4().hex[:12]
    with database.unit_of_work() as uow:
        uow.connection.execute(f'CREATE SCHEMA "{schema}"')
    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
    first = PostgreSQLDatabase(POSTGRES_URL, min_size=1, max_size=1)
    second = PostgreSQLDatabase(POSTGRES_URL, min_size=1, max_size=1)
    first.open()
    second.open()
    barrier = threading.Barrier(2)
    try:
        def migrate(instance):
            barrier.wait(timeout=5)
            return instance.migrate()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(migrate, (first, second)))
        assert sorted(version for result in outcomes for version in result) == [1, 2, 3, 4]
        with first.pool.connection() as connection:
            assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 4
            assert connection.execute("SELECT to_regclass('prediction_history')").fetchone()[0] == "prediction_history"
    finally:
        first.close()
        second.close()
        monkeypatch.delenv("PGOPTIONS", raising=False)
        with database.unit_of_work() as uow:
            uow.connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.parametrize(
    ("status", "probability", "risk"),
    [("available", 0.1, False), ("degraded", None, None), ("available", 0.91, True)],
    ids=["negative", "degraded", "positive"],
)
def test_prediction_projection_retries_are_idempotent_for_all_outcomes(database, status, probability, risk):
    history = PostgreSQLPredictionHistoryRepository(database)
    recommendation_service = RecommendationService(PostgreSQLRecommendationRepository(database))
    notifications = PostgreSQLOperationalNotificationRepository(database)
    object_id = int(uuid4().int % 1_000_000_000) + 1
    at = datetime.now(timezone.utc) + timedelta(seconds=uuid4().int % 100_000)
    prediction = OperationalPrediction(object_id, at, at, probability, risk, .4, status,
        "source_unavailable" if status == "degraded" else None, "ml", "frozen-v1", "87-v1",
        "reference-v1", "reference-hash")

    record, card, inserted = history.process_operational(prediction)
    assert inserted
    recommendation_service.generate(record, card)
    notifications.create_for_prediction(record, card)
    with database.pool.connection() as connection:
        before = tuple(connection.execute(
            f"SELECT count(*) FROM {table} WHERE object_id=%s", (object_id,)
        ).fetchone()[0] for table in ("prediction_history", "forecast_cards", "recommendations", "operational_notifications"))

    repeated_record, repeated_card, inserted = history.process_operational(prediction)
    assert not inserted
    assert repeated_card == card
    recommendation_service.generate(repeated_record, repeated_card)
    notifications.create_for_prediction(repeated_record, repeated_card)
    with database.pool.connection() as connection:
        after = tuple(connection.execute(
            f"SELECT count(*) FROM {table} WHERE object_id=%s", (object_id,)
        ).fetchone()[0] for table in ("prediction_history", "forecast_cards", "recommendations", "operational_notifications"))
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    assert after == before
    assert before == (1, 1 if risk is True else 0, 1, 1 if risk is True else 0)


def test_recommendation_retry_with_conflicting_content_fails_closed(database):
    history = PostgreSQLPredictionHistoryRepository(database)
    repository = PostgreSQLRecommendationRepository(database)
    service = RecommendationService(repository)
    object_id = int(uuid4().int % 1_000_000_000) + 1
    at = datetime.now(timezone.utc) + timedelta(seconds=uuid4().int % 100_000)
    record, _, _ = history.process_operational(OperationalPrediction(object_id,at,at,.2,False,.4,
        "available",None,"ml","frozen-v1","87-v1","reference-v1","reference-hash"))
    saved = service.generate(record)
    with pytest.raises(RuntimeError, match="incompatible content"):
        repository.save(replace(saved, action="content changed for same deterministic identity"))
    with database.pool.connection() as connection:
        row = connection.execute("SELECT action FROM recommendations WHERE recommendation_id=%s",
                                 (saved.recommendation_id,)).fetchone()
        assert row[0] == saved.action
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_one_uow_rolls_back_related_rows_together(database):
    identity = f"stage7-rollback-{uuid4()}"
    object_id = int(uuid4().int % 1_000_000_000) + 1
    with pytest.raises(RuntimeError, match="injected"):
        with database.unit_of_work() as uow:
            uow.lock_object(object_id)
            uow.connection.execute(
                "INSERT INTO prediction_history(identity,object_id,snapshot_ts,generated_at,"
                "prediction_status,risk_score_kind,created_at) VALUES (%s,%s,now(),now(),'available','ml',now())",
                (identity, object_id),
            )
            uow.connection.execute(
                "INSERT INTO forecast_cards(card_id,object_id,first_positive_snapshot_ts,"
                "last_positive_snapshot_ts,max_probability,status,created_at,updated_at) "
                "VALUES (%s,%s,now(),now(),0.5,'active',now(),now())",
                (identity, object_id),
            )
            raise RuntimeError("injected failure after first related insert")

    with database.pool.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM prediction_history WHERE identity=%s", (identity,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM forecast_cards WHERE card_id=%s", (identity,)
        ).fetchone()[0] == 0


def test_object_lock_serializes_two_independent_connections(database):
    object_id = int(uuid4().int % 1_000_000_000) + 1
    second_acquired = threading.Event()
    first_holds_lock = threading.Event()
    finished: list[float] = []

    def first_writer():
        with database.unit_of_work() as uow:
            uow.lock_object(object_id)
            first_holds_lock.set()
            time.sleep(0.25)

    def second_writer():
        assert first_holds_lock.wait(timeout=3)
        start = time.monotonic()
        with database.unit_of_work() as uow:
            uow.lock_object(object_id)
            finished.append(time.monotonic() - start)
            second_acquired.set()

    left = threading.Thread(target=first_writer)
    right = threading.Thread(target=second_writer)
    left.start()
    right.start()
    left.join(timeout=5)
    right.join(timeout=5)
    assert not left.is_alive() and not right.is_alive()
    assert second_acquired.is_set()
    assert finished[0] >= 0.18


def test_prediction_history_preserves_identity_and_forecast_card_episode_rules(database):
    repository = PostgreSQLPredictionHistoryRepository(database)
    object_id = int(uuid4().int % 1_000_000_000) + 1
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def prediction(at, probability, *, status="available", risk=True):
        return OperationalPrediction(
            object_id=object_id, snapshot_ts=at, generated_at=at,
            risk_probability=probability, risk_flag=risk, threshold=0.4,
            prediction_status=status, degraded_reason=None, risk_score_kind="ml",
            model_version="frozen-v1", feature_schema_version="87-v1",
            reference_snapshot_id="reference-v1", reference_hash="sha256:reference",
        )

    first, card_a, inserted = repository.process_operational(prediction(t0, 0.63))
    assert inserted and card_a is not None
    updated, same_card, inserted = repository.process_operational(prediction(t0 + timedelta(hours=6), 0.71))
    assert inserted and same_card.card_id == card_a.card_id
    assert same_card.max_probability == 0.71

    repository.process_operational(prediction(t0 + timedelta(hours=18), None, risk=False))
    later, card_b, inserted = repository.process_operational(prediction(t0 + timedelta(hours=24), 0.95))
    assert inserted and card_b is not None and card_b.card_id != card_a.card_id
    assert repository.cards(object_id)[0].card_id == card_b.card_id
    assert repository.get(first.identity).identity == f"{object_id}|{t0.isoformat()}|frozen-v1"
    with database.pool.connection() as connection:
        memberships = connection.execute(
            "SELECT prediction_identity FROM forecast_card_predictions WHERE card_id=%s",
            (card_a.card_id,),
        ).fetchall()
    assert {row[0] for row in memberships} == {first.identity, updated.identity}
    assert later.identity not in {row[0] for row in memberships}


def test_prediction_card_recommendation_and_notification_share_rollback(database):
    history = PostgreSQLPredictionHistoryRepository(database)
    recommendations = RecommendationService(PostgreSQLRecommendationRepository(database))
    notifications = PostgreSQLOperationalNotificationRepository(database)
    object_id = int(uuid4().int % 1_000_000_000) + 1
    at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    prediction = OperationalPrediction(object_id, at, at, 0.91, True, 0.4, "available", None, "ml",
        "frozen-v1", "87-v1", "reference-v1", "reference-hash")

    with pytest.raises(RuntimeError, match="injected projection failure"):
        with history.batch():
            record, card, _ = history.process_operational(prediction)
            recommendations.generate(record, card)
            notifications.create_for_prediction(record, card)
            raise RuntimeError("injected projection failure")

    with database.pool.connection() as connection:
        assert connection.execute("SELECT count(*) FROM prediction_history WHERE object_id=%s", (object_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM forecast_cards WHERE object_id=%s", (object_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM recommendations WHERE object_id=%s", (object_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operational_notifications WHERE object_id=%s", (object_id,)).fetchone()[0] == 0


def test_postgres_dispatcher_atomic_linked_request_receipt_and_stale_version(database):
    history = PostgreSQLPredictionHistoryRepository(database)
    workflow = PostgreSQLDispatcherWorkflow(database, history)
    object_id = int(uuid4().int % 1_000_000_000) + 1
    at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    prediction = OperationalPrediction(object_id, at, at, .83, True, .4, "available", None, "ml",
        "frozen-v1", "87-v1", "reference-v1", "reference-hash")
    record, card, _ = history.process_operational(prediction)
    central = AuthUser("dispatcher-test", "dispatcher-test", "central_dispatcher", None)
    command = {"expected_version": 0, "idempotency_key": f"decision-{uuid4()}",
        "decision": "inspection_requested", "prediction_identity": record.identity,
        "comment": "check", "reason_code": "risk", "request": {"reason": "verify request", "priority": "high"}}
    result = workflow.decide(card.card_id, actor_user_id=central.user_id, actor_role=central.role, payload=command)
    assert result["request"]["status"] == "new"
    assert result["signal"]["state"] == "inspection_requested"
    assert workflow.decide(card.card_id, actor_user_id=central.user_id, actor_role=central.role, payload=command) == result
    rows,total,_=workflow.operational_history_projection(object_ids=[object_id],user=central,state="inspection_requested")
    assert total==1 and rows[0]["identity"]==record.identity and rows[0]["request_id"]==result["request"]["id"]
    current,_=workflow.object_catalog_projection([object_id])
    assert current[object_id]["risk_probability"]==.83
    with pytest.raises(Exception, match="changed by another dispatcher"):
        workflow.decide(card.card_id, actor_user_id=central.user_id, actor_role=central.role,
                        payload={**command, "idempotency_key": f"stale-{uuid4()}"})
    with database.pool.connection() as connection:
        assert connection.execute("SELECT count(*) FROM signal_decisions WHERE card_id=%s", (card.card_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM maintenance_requests WHERE card_id=%s", (card.card_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT prediction_identity FROM signal_decisions WHERE card_id=%s", (card.card_id,)).fetchone()[0] == record.identity


def test_postgres_competing_dispatchers_have_one_versioned_winner(database):
    from concurrent.futures import ThreadPoolExecutor
    from backend.app.services.dispatcher_workflow import WorkflowConflict
    history=PostgreSQLPredictionHistoryRepository(database)
    workflow=PostgreSQLDispatcherWorkflow(database,history)
    object_id=int(uuid4().int%1_000_000_000)+1
    at=datetime.now(timezone.utc)
    record,card,_=history.process_operational(OperationalPrediction(object_id,at,at,.72,True,.4,"available",None,"ml",
        "frozen-v1","87-v1","reference-v1","reference-hash"))
    central=AuthUser("race-dispatcher","race-dispatcher","central_dispatcher",None)
    def send(decision):
        return workflow.decide(card.card_id,actor_user_id=central.user_id,actor_role=central.role,payload={
            "expected_version":0,"idempotency_key":f"race-{decision}-{uuid4()}","decision":decision,
            "prediction_identity":record.identity,"request":None,"comment":None,"reason_code":None})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(send,"monitoring"),pool.submit(send,"false_alarm")]
        results=[]
        for future in futures:
            try: results.append(future.result(timeout=5))
            except WorkflowConflict as error: results.append(error)
    assert sum(isinstance(result,dict) for result in results)==1
    assert sum(isinstance(result,WorkflowConflict) for result in results)==1
    with database.pool.connection() as connection:
        assert connection.execute("SELECT count(*) FROM signal_decisions WHERE card_id=%s",(card.card_id,)).fetchone()[0]==1
        assert connection.execute("SELECT version FROM signal_work WHERE card_id=%s",(card.card_id,)).fetchone()[0]==1


def test_postgres_technician_return_and_accept_lifecycle(database):
    history = PostgreSQLPredictionHistoryRepository(database)
    workflow = PostgreSQLDispatcherWorkflow(database, history)
    object_id = 28
    central = AuthUser("dispatcher-stage7", "dispatcher-stage7", "central_dispatcher", None)
    technician = AuthUser("tech-stage7", "tech-stage7", "technician", (28,))
    now = datetime.now(timezone.utc)
    request = workflow.create({"object_id": object_id, "channel_id": None, "prediction_id": None,
        "reason": "manual check", "priority": "normal", "created_by_user_id": central.user_id,
        "created_by_role": central.role, "created_at": now, "updated_at": now})

    def act(action, version, **values):
        return workflow.request_command(request["id"], action, central if action in {"submit", "assign", "accept", "return"} else technician,
            {"expected_version": version, "idempotency_key": f"{action}-{uuid4()}", **values},
            directory=(technician,))

    assert act("submit", 1)["status"] == "new"
    assert act("assign", 2, assignee_user_id=technician.user_id)["status"] == "assigned"
    assert workflow.request_command(request["id"], "start", technician,
        {"expected_version": 3, "idempotency_key": f"start-{uuid4()}"})["status"] == "in_progress"
    first = workflow.request_command(request["id"], "result", technician,
        {"expected_version": 4, "idempotency_key": f"result-{uuid4()}", "result_code": "needs_followup", "summary": "recheck"})
    assert first["status"] == "awaiting_review" and first["latest_result"]["revision"] == 1
    returned = act("return", 5, comment="add details")
    assert returned["status"] == "in_progress"
    workflow.request_command(request["id"], "result", technician,
        {"expected_version": 6, "idempotency_key": f"result-{uuid4()}", "result_code": "issue_found", "summary": "confirmed"})
    accepted = act("accept", 7)
    assert accepted["status"] == "closed" and accepted["latest_result"]["revision"] == 2


@pytest.mark.parametrize(("result_code", "expected_signal_state"), [
    ("no_issue_found", "resolved"),
    ("issue_found", "new"),
    ("needs_followup", "new"),
    ("unable_to_verify", "new"),
], ids=["clear", "issue-remains", "follow-up-needed", "unable-to-verify"])
def test_postgres_accept_resolves_linked_signal_only_for_clear_result(database, result_code, expected_signal_state):
    history = PostgreSQLPredictionHistoryRepository(database)
    workflow = PostgreSQLDispatcherWorkflow(database, history)
    run_id = uuid4().hex
    object_id = {
        "no_issue_found": 20,
        "issue_found": 28,
        "needs_followup": 42,
        "unable_to_verify": 99,
    }[result_code]
    now = datetime.now(timezone.utc)
    prediction = OperationalPrediction(object_id, now, now, .88, True, .4, "available", None, "ml",
        "frozen-v1", "87-v1", "reference-v1", "reference-hash")
    record, card, _ = history.process_operational(prediction)
    central = AuthUser(f"central-{run_id}", f"central-{run_id}", "central_dispatcher", None)
    technician = AuthUser(f"tech-{run_id}", f"tech-{run_id}", "technician", (object_id,))
    decision = workflow.decide(card.card_id, actor_user_id=central.user_id, actor_role=central.role,
        payload={"expected_version": 0, "idempotency_key": f"{run_id}-decision",
            "decision": "inspection_requested", "prediction_identity": record.identity,
            "request": {"reason": "Signal resolution acceptance regression", "priority": "normal"}})
    request = decision["request"]

    def act(action, version, user, **fields):
        return workflow.request_command(request["id"], action, user,
            {"expected_version": version, "idempotency_key": f"{run_id}-{action}", **fields},
            directory=(technician,))

    assigned = act("assign", 1, central, assignee_user_id=technician.user_id)
    started = act("start", assigned["version"], technician)
    awaiting = act("result", started["version"], technician, result_code=result_code,
        summary="Technician inspection result")
    accepted = act("accept", awaiting["version"], central, comment="Dispatcher reviewed report")
    repeated = act("accept", awaiting["version"], central, comment="Dispatcher reviewed report")

    assert repeated == accepted
    assert accepted["status"] == "closed"
    assert accepted["latest_result"]["result_code"] == result_code
    assert accepted["accepted_by_user_id"] == central.user_id
    assert sum(event["event"] == "accepted" for event in accepted["timeline"]) == 1
    assert workflow.signal(card.card_id)["state"] == expected_signal_state
    visible = workflow.signals(object_id=object_id, state=expected_signal_state)
    assert any(signal["card_id"] == card.card_id for signal in visible["items"])


def test_postgres_operational_actions_are_persistent_and_idempotent(database):
    history=PostgreSQLPredictionHistoryRepository(database)
    actions=PostgreSQLOperationalActionRepository(database)
    object_id=int(uuid4().int % 1_000_000_000)+1
    at=datetime(2026,4,1,tzinfo=timezone.utc)
    record,_,_=history.process_operational(OperationalPrediction(object_id,at,at,.7,True,.4,"available",None,"ml",
        "frozen-v1","87-v1","reference-v1","reference-hash"))
    args=dict(actor_user_id="dispatcher",actor_role="central_dispatcher",decision="confirmed",
        reason_code="reviewed",comment="ok",idempotency_key="review-once")
    saved=actions.add_review(record.identity,object_id,**args)
    repeated=actions.add_review(record.identity,object_id,**args)
    assert saved["id"]==repeated["id"]
    assert actions.summaries([record.identity])[record.identity]["review_count"]==1
    with pytest.raises(OperationalActionIdempotencyConflict):
        actions.add_review(record.identity,object_id,**{**args,"comment":"different"})


def test_postgres_cycle_lease_serializes_competing_workers(database):
    from datetime import datetime, timezone
    cycles=PostgreSQLOperationalCycleRepository(database,lease_seconds=60)
    snapshot=datetime(2099,1,1,tzinfo=timezone.utc)+timedelta(seconds=uuid4().int % 10_000_000)
    claimed=cycles.begin(snapshot,78)
    assert claimed.status=="running"
    with pytest.raises(CycleAlreadyRunning):
        cycles.begin(snapshot,78)
    completed=cycles.complete(claimed.cycle_id,available=78,degraded=0,positive=5)
    assert completed.status=="completed" and cycles.get(snapshot).status=="completed"


def test_normal_api_postgres_mode_survives_application_restart(database, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.services.auth import auth_provider

    history=PostgreSQLPredictionHistoryRepository(database)
    object_id=28
    with database.pool.connection() as connection:
        latest=connection.execute("SELECT max(snapshot_ts) FROM prediction_history WHERE object_id=%s",(object_id,)).fetchone()[0]
    at=max(datetime.now(timezone.utc),latest+timedelta(hours=13) if latest else datetime.now(timezone.utc))
    record,card,_=history.process_operational(OperationalPrediction(object_id,at,at,.88,True,.4,"available",None,"ml",
        f"frozen-{uuid4()}","87-v1","reference-v1","reference-hash"))
    monkeypatch.setenv("TGOAT_OPERATIONAL_STORAGE","postgres")
    monkeypatch.setenv("TGOAT_OPERATIONAL_DATABASE_URL",POSTGRES_URL)
    central=auth_provider.authenticate("central","central-demo")
    headers={"Authorization":f"Bearer {auth_provider.issue_token(central)}"}
    payload={"expected_version":0,"idempotency_key":f"api-{uuid4()}","decision":"inspection_requested",
        "prediction_identity":record.identity,"comment":"validate","request":{"reason":"Inspect linked request","priority":"high"}}
    with TestClient(app) as client:
        signal=client.get(f"/api/v1/signals/{card.card_id}",headers=headers)
        assert signal.status_code==200 and signal.json()["risk_probability"]==.88
        monitoring=client.get("/api/v1/signals/monitoring",headers=headers)
        assert monitoring.status_code==200
        catalog=client.get("/api/v1/objects/catalog",headers=headers)
        assert catalog.status_code==200 and any(item["id"]==object_id for item in catalog.json()["items"])
        journal=client.get(f"/api/v1/operational/history?object_id={object_id}",headers=headers)
        assert journal.status_code==200 and any(item["identity"]==record.identity for item in journal.json()["items"])
        response=client.post(f"/api/v1/signals/{card.card_id}/decision",headers=headers,json=payload)
        assert response.status_code==200 and response.json()["request"]["status"]=="new"
        request_id=response.json()["request"]["id"]
    with TestClient(app) as client:
        restored=client.get(f"/api/v1/maintenance-requests/{request_id}",headers=headers)
        assert restored.status_code==200 and restored.json()["prediction_identity"]==record.identity
        replayed=client.post(f"/api/v1/signals/{card.card_id}/decision",headers=headers,json=payload)
        assert replayed.status_code==200 and replayed.json()["request"]["id"]==request_id
        stale_payload={**payload,"idempotency_key":f"api-stale-{uuid4()}"}
        stale=client.post(f"/api/v1/signals/{card.card_id}/decision",headers=headers,json=stale_payload)
        assert stale.status_code==409
        assert stale.json()["detail"]["code"]=="workflow_conflict"
    with database.pool.connection() as connection:
        assert connection.execute("SELECT count(*) FROM signal_decisions WHERE card_id=%s",(card.card_id,)).fetchone()[0]==1
        assert connection.execute("SELECT count(*) FROM maintenance_requests WHERE card_id=%s",(card.card_id,)).fetchone()[0]==1
        assert connection.execute("SELECT version FROM signal_work WHERE card_id=%s",(card.card_id,)).fetchone()[0]==1


def test_normal_mode_fails_closed_without_postgres_url(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    monkeypatch.setenv("TGOAT_OPERATIONAL_STORAGE","postgres")
    monkeypatch.delenv("TGOAT_OPERATIONAL_DATABASE_URL",raising=False)
    with pytest.raises(Exception,match="TGOAT_OPERATIONAL_DATABASE_URL"):
        with TestClient(app):
            pass


def test_sqlite_import_is_read_only_idempotent_and_preserves_workflow(database, tmp_path, monkeypatch):
    from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow
    from backend.app.services.prediction_history import SQLitePredictionHistoryRepository
    from backend.app.services.postgres_storage import PostgreSQLDatabase
    from scripts.migrate_operational_sqlite_to_postgres import import_sources

    source_path=tmp_path/"legacy-operational.sqlite"
    source=SQLitePredictionHistoryRepository(source_path)
    workflow=SQLiteDispatcherWorkflow(source)
    object_id=int(uuid4().int % 1_000_000_000)+1
    at=datetime(2026,7,1,tzinfo=timezone.utc)
    record,card,_=source.process_operational(OperationalPrediction(object_id,at,at,.77,True,.4,"available",None,"ml",
        "frozen-v1","87-v1","reference-v1","reference-hash"))
    payload={"expected_version":0,"idempotency_key":"migration-receipt","decision":"inspection_requested",
        "prediction_identity":record.identity,"comment":"keep","reason_code":"checked",
        "request":{"reason":"preserve me","priority":"high"}}
    workflow.decide(card.card_id,actor_user_id="source-dispatcher",actor_role="central_dispatcher",payload=payload)
    source.close()

    schema="migration_test_"+uuid4().hex[:12]
    with database.unit_of_work() as uow:
        uow.connection.execute(f'CREATE SCHEMA "{schema}"')
    monkeypatch.setenv("PGOPTIONS",f"-c search_path={schema}")
    isolated=PostgreSQLDatabase(POSTGRES_URL)
    isolated.open()
    try:
        isolated.migrate()
        first=import_sources(isolated,history=source_path)
        second=import_sources(isolated,history=source_path)
        assert first["tables"]["prediction_history"]["source_rows"]==1
        assert first["tables"]["maintenance_requests"]["source_rows"]==1
        # Missing optional tables in a valid source database are distinct from
        # an explicitly configured source database that does not exist.
        assert first["tables"]["recommendations"]["status"] == "absent"
        assert first["tables"]["operational_cycles"]["status"] == "absent"
        assert second["tables"]["signal_decisions"]["imported"]==0
        with isolated.pool.connection() as connection:
            assert connection.execute("SELECT prediction_identity FROM signal_decisions").fetchone()[0]==record.identity
            row=connection.execute("SELECT id,reason,status FROM maintenance_requests").fetchone()
            assert row[1]=="preserve me" and row[2]=="new"
            assert connection.execute("SELECT count(*) FROM maintenance_events").fetchone()[0]==1
    finally:
        isolated.close()
        monkeypatch.delenv("PGOPTIONS",raising=False)
        with database.unit_of_work() as uow:
            uow.connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.parametrize("source_kind", ["recommendations", "cycles"])
def test_missing_explicit_sqlite_source_aborts_before_any_postgres_import(
    database, tmp_path, monkeypatch, source_kind
):
    import sqlite3
    from scripts.migrate_operational_sqlite_to_postgres import import_sources

    history = tmp_path / "valid-history.sqlite"
    connection = sqlite3.connect(history)
    connection.close()
    missing = tmp_path / f"missing-{source_kind}.sqlite"
    schema = "missing_source_" + uuid4().hex[:12]
    with database.unit_of_work() as uow:
        uow.connection.execute(f'CREATE SCHEMA "{schema}"')
    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
    isolated = PostgreSQLDatabase(POSTGRES_URL)
    isolated.open()
    try:
        isolated.migrate()
        with isolated.pool.connection() as pg:
            before = (pg.execute("SELECT count(*) FROM schema_migrations").fetchone()[0],
                      pg.execute("SELECT count(*) FROM prediction_history").fetchone()[0],
                      pg.execute("SELECT count(*) FROM recommendations").fetchone()[0],
                      pg.execute("SELECT count(*) FROM operational_cycles").fetchone()[0])
        arguments = {source_kind: missing}
        with pytest.raises(FileNotFoundError, match=f"SQLite {source_kind} source does not exist"):
            import_sources(isolated, history=history, **arguments)
        with isolated.pool.connection() as pg:
            after = (pg.execute("SELECT count(*) FROM schema_migrations").fetchone()[0],
                     pg.execute("SELECT count(*) FROM prediction_history").fetchone()[0],
                     pg.execute("SELECT count(*) FROM recommendations").fetchone()[0],
                     pg.execute("SELECT count(*) FROM operational_cycles").fetchone()[0])
        assert after == before
    finally:
        isolated.close()
        monkeypatch.delenv("PGOPTIONS", raising=False)
        with database.unit_of_work() as uow:
            uow.connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
