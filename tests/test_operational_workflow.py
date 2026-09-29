from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import sqlite3
import sys
from time import monotonic
from urllib.parse import quote

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.demo_feature_state import bootstrap_demo_feature_state
from backend.app.services.operational_actions import SQLiteOperationalActionRepository
from backend.app.services.operational_notifications import SQLiteOperationalNotificationRepository
from backend.app.services.prediction_history import OperationalPrediction
from backend.app.services.auth import accessible_object_ids, auth_provider
from backend.app.services.mock_storage import audit_repository
from backend.app.services.reference_data import get_objects_by_id


SNAPSHOT = datetime(2026, 6, 30, 20, 59, 59, tzinfo=timezone.utc)


def _login(client, username="central", password="central-demo"):
    user = auth_provider.authenticate(username, password)
    client.headers["Authorization"] = f"Bearer {auth_provider.issue_token(user)}"


def test_bounded_operational_prediction_review_outcome_and_notification_flow(
    tmp_path, monkeypatch, processed_dir,
):
    state_path = tmp_path / "feature-state.sqlite"
    history_path = tmp_path / "operational.sqlite"
    bootstrap_demo_feature_state(state_path)
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(state_path))
    monkeypatch.setenv("TGOAT_PREDICTION_HISTORY_DB", str(history_path))
    monkeypatch.setenv("TGOAT_OPERATIONAL_CYCLE_DB", str(history_path))
    monkeypatch.setenv("TGOAT_RECOMMENDATION_DB", str(history_path))

    with TestClient(app) as client:
        _login(client)
        history_repository = app.state.operational_read_service.forecast_service.repository
        for index in range(1001):
            snapshot = SNAPSHOT - timedelta(hours=6 * (1001 - index))
            history_repository.process_operational(OperationalPrediction(
                object_id=5343, snapshot_ts=snapshot, generated_at=snapshot,
                risk_probability=0.1, risk_flag=False, threshold=0.4,
                prediction_status="available", degraded_reason=None,
                risk_score_kind="ml", model_version="journal-regression-model",
                feature_schema_version="test-v1", reference_snapshot_id="test-reference",
                reference_hash="a" * 64,
            ))
        response = client.post(
            "/api/v1/objects/5343/risk/operational",
            params={"snapshot_ts": SNAPSHOT.isoformat()},
        )
        assert response.status_code == 200, response.text
        prediction = response.json()["prediction"]
        identity = prediction["identity"]
        assert prediction["prediction_status"] == "available"
        assert prediction["risk_score_kind"] == "ml"
        assert prediction["risk_flag"] is True
        assert response.json()["card"] is not None
        assert response.json()["recommendation"]["status"] == "available"

        dashboard = client.get("/api/v1/operational/dashboard").json()
        item = next(value for value in dashboard["items"] if value["object_id"] == 5343)
        assert item["prediction_identity"] == identity
        assert item["risk_probability"] == prediction["risk_probability"]

        cycle_repository = app.state.operational_cycle_service.cycle_repository
        cycle = cycle_repository.begin(SNAPSHOT, object_count=78)
        cycle_repository.complete(cycle.cycle_id, available=1, degraded=0, positive=1)
        read_service = app.state.operational_read_service
        read_service.clock = lambda: datetime(2026, 6, 30, 21, 0, tzinfo=timezone.utc)
        assert client.get("/api/v1/operational/dashboard").json()["stale"] is False
        read_service.clock = lambda: datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        historical_dashboard = client.get("/api/v1/operational/dashboard").json()
        assert historical_dashboard["stale"] is True
        assert historical_dashboard["cycle_status"] == "stale"
        assert datetime.fromisoformat(historical_dashboard["snapshot_ts"]) == SNAPSHOT

        identity_path = quote(identity, safe="")
        review_body = {
            "decision": "confirmed",
            "reason_code": "operator_check",
            "comment": "Проверить объект по реальному прогнозу.",
            "idempotency_key": "review-demo-1",
        }
        review_url = f"/api/v1/operational/predictions/{identity_path}/review"
        first_review = client.post(review_url, json=review_body)
        repeated_review = client.post(review_url, json=review_body)
        assert first_review.status_code == 201, first_review.text
        assert repeated_review.status_code == 201
        assert repeated_review.json()["id"] == first_review.json()["id"]
        assert first_review.json()["prediction_identity"] == identity
        assert client.get(f"/api/v1/operational/predictions/{identity_path}/reviews").json() == [
            first_review.json()
        ]
        conflict = client.post(review_url, json={
            **review_body, "decision": "monitoring",
        })
        assert conflict.status_code == 409
        central_user = auth_provider.authenticate("central", "central-demo")
        audit_events, _ = audit_repository.filter(limit=None)
        successful_review_audit = [
            event for event in audit_events
            if event.get("action") == "request.post"
            and event.get("path", "").endswith("/review")
            and event.get("status_code") == 201
        ]
        assert successful_review_audit
        assert all(
            event["user_id"] == central_user.user_id
            and event["role"] == "central_dispatcher"
            and event["result"] == "success"
            for event in successful_review_audit
        )

        outcome_body = {
            "actual_outcome": "inspection_completed",
            "reason_code": "inspection",
            "comment": "Действие завершено, результат ожидается.",
            "idempotency_key": "outcome-demo-1",
        }
        outcome_url = f"/api/v1/operational/predictions/{identity_path}/outcomes"
        first_outcome = client.post(outcome_url, json=outcome_body)
        repeated_outcome = client.post(outcome_url, json=outcome_body)
        assert first_outcome.status_code == 201, first_outcome.text
        assert repeated_outcome.json()["id"] == first_outcome.json()["id"]
        assert client.get(outcome_url).json() == [first_outcome.json()]

        journal = client.get("/api/v1/operational/predictions?limit=10&offset=0")
        assert journal.status_code == 200, journal.text
        journal_body = journal.json()
        entry = next(value for value in journal_body["items"] if value["identity"] == identity)
        assert journal_body["total"] == 1002
        assert entry["object_id"] == 5343
        assert entry["risk_score_kind"] == "ml"
        assert entry["review_count"] == 1
        assert entry["latest_review_decision"] == "confirmed"
        assert entry["outcome_count"] == 1
        assert entry["latest_actual_outcome"] == "inspection_completed"
        detail = client.get(f"/api/v1/operational/predictions/{identity_path}")
        assert detail.status_code == 200, detail.text
        assert detail.json() == entry

        mixed_read_paths = [
            "/api/v1/operational/dashboard",
            "/api/v1/operational/predictions?limit=10",
            "/api/v1/operational/objects/5343",
            "/api/v1/objects/5343/risk/history?limit=10",
            "/api/v1/objects/5343/risk/cards",
            "/api/v1/objects/5343/risk/recommendations",
            "/api/v1/notifications?limit=20",
            f"/api/v1/operational/predictions/{identity_path}/reviews",
            f"/api/v1/operational/predictions/{identity_path}/outcomes",
        ]
        with ThreadPoolExecutor(max_workers=20) as pool:
            mixed_responses = list(pool.map(
                lambda path: client.get(path), mixed_read_paths * 8,
            ))
        assert all(response.status_code == 200 for response in mixed_responses), [
            (response.request.url, response.status_code, response.text)
            for response in mixed_responses if response.status_code != 200
        ]

        oldest = client.get("/api/v1/operational/predictions?limit=1&offset=1001").json()["items"][0]
        assert oldest["identity"] not in {
            value["identity"] for value in client.get(
                "/api/v1/objects/5343/risk/history?limit=1000"
            ).json()
        }
        oldest_detail = client.get(
            f"/api/v1/operational/predictions/{quote(oldest['identity'], safe='')}"
        )
        assert oldest_detail.status_code == 200, oldest_detail.text
        assert oldest_detail.json() == oldest

        missing_path = quote("unknown|snapshot|model", safe="")
        assert client.get(f"/api/v1/operational/predictions/{missing_path}/reviews").status_code == 404

        notifications = client.get("/api/v1/notifications").json()["items"]
        matching = [value for value in notifications if value["prediction_id"] == identity]
        assert len(matching) == 1
        notification = matching[0]
        assert notification["source_kind"] == "operational_ml"
        assert notification["object_id"] == 5343
        assert notification["card_id"] == response.json()["card"]["card_id"]
        assert client.patch(f"/api/v1/notifications/{notification['id']}",
                            json={"read": True}).json()["read"] is True

        # A repeat operational write must not create a second user-facing notification.
        repeated_forecast = client.post(
            "/api/v1/objects/5343/risk/operational",
            params={"snapshot_ts": SNAPSHOT.isoformat()},
        )
        assert repeated_forecast.status_code == 200
        notifications = client.get("/api/v1/notifications").json()["items"]
        assert sum(value["prediction_id"] == identity for value in notifications) == 1

        _login(client, "dispatch-9", "dispatch-demo")
        scoped_dashboard = client.get("/api/v1/operational/dashboard").json()
        scoped_objects = [value["object_id"] for value in scoped_dashboard["items"]]
        district_user = auth_provider.authenticate("dispatch-9", "dispatch-demo")
        assert district_user is not None
        district_scope = accessible_object_ids(district_user)
        scoped_journal = client.get("/api/v1/operational/predictions?limit=10").json()
        assert all(value["object_id"] in district_scope for value in scoped_journal["items"])
        assert client.get("/api/v1/operational/predictions?limit=501").status_code == 422
        accessible_channel_objects = [
            object_id for object_id, value in get_objects_by_id().items()
            if value["has_channels"] and object_id in district_scope
        ]
        assert scoped_dashboard["object_count"] == len(accessible_channel_objects)
        assert 9 not in scoped_objects
        _login(client, "tech-28", "tech-demo")
        tech_snapshot = SNAPSHOT + timedelta(hours=6)
        tech_record, _, inserted = history_repository.process_operational(OperationalPrediction(
            object_id=28, snapshot_ts=tech_snapshot, generated_at=tech_snapshot,
            risk_probability=0.2, risk_flag=False, threshold=0.4,
            prediction_status="available", degraded_reason=None, risk_score_kind="ml",
            model_version="technician-review-regression", feature_schema_version="test-v1",
            reference_snapshot_id="test-reference", reference_hash="b" * 64,
        ))
        assert inserted is True
        tech_identity = tech_record.identity
        tech_review_url = f"/api/v1/operational/predictions/{quote(tech_identity, safe='')}/review"
        tech_review = client.post(tech_review_url, json={
            "decision": "confirmed", "idempotency_key": "technician-review-in-scope",
        })
        assert tech_review.status_code == 201, tech_review.text
        assert tech_review.json()["actor_role"] == "technician"
        assert len(app.state.operational_action_repository.reviews(tech_identity)) == 1

        denied_scope = client.post(review_url, json={
            "decision": "monitoring", "idempotency_key": "technician-review-out-of-scope",
        })
        assert denied_scope.status_code == 404
        assert len(app.state.operational_action_repository.reviews(identity)) == 1
        tech_outcome_url = f"/api/v1/operational/predictions/{quote(tech_identity, safe='')}/outcomes"
        denied_outcome = client.post(tech_outcome_url, json={
            "actual_outcome": "inspection_completed",
            "idempotency_key": "technician-outcome-denied",
        })
        assert denied_outcome.status_code == 403
        assert app.state.operational_action_repository.outcomes(tech_identity) == []
        assert client.post("/api/v1/predictions/1/close").status_code == 403
        assert client.patch("/api/v1/maintenance-requests/1", json={
            "status": "assigned",
        }).status_code == 403

        _login(client, "dispatch-9", "dispatch-demo")
        forbidden = client.post(review_url, json={
            "decision": "confirmed", "idempotency_key": "outside-scope",
        })
        assert forbidden.status_code == 404
        assert len(app.state.operational_action_repository.reviews(identity)) == 1
        assert len(app.state.operational_action_repository.outcomes(identity)) == 1
        audit_events, _ = audit_repository.filter(limit=None)
        denied_audit = [
            event for event in audit_events
            if event.get("action") == "request.post"
            and event.get("path", "").endswith("/review")
            and event.get("status_code") == 404
            and event.get("user_id") == district_user.user_id
        ]
        assert len(denied_audit) == 1
        assert denied_audit[0]["user_id"] == district_user.user_id
        assert denied_audit[0]["role"] == "district_dispatcher"
        assert denied_audit[0]["result"] == "not_found"

        # Reading the state/history of a degraded forecast must remain possible;
        # only new dispatcher actions are restricted to available predictions.
        _login(client)
        with sqlite3.connect(history_path) as connection:
            connection.execute(
                "UPDATE prediction_history SET prediction_status='degraded' WHERE identity=?",
                (identity,),
            )
        degraded_state = client.get("/api/v1/operational/objects/5343").json()
        assert degraded_state["status"] == "degraded"
        assert client.get(
            f"/api/v1/operational/predictions/{identity_path}/reviews"
        ).status_code == 200
        assert client.get(
            f"/api/v1/operational/predictions/{identity_path}/outcomes"
        ).status_code == 200
        rejected_action = client.post(review_url, json={
            "decision": "monitoring", "idempotency_key": "degraded-read-only",
        })
        assert rejected_action.status_code == 409

    history_backup = tmp_path / "operational-backup.sqlite"
    feature_backup = tmp_path / "feature-state-backup.sqlite"
    for source_path, backup_path in (
        (history_path, history_backup), (state_path, feature_backup),
    ):
        with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as backup:
            source.backup(backup)
            assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(feature_backup))
    monkeypatch.setenv("TGOAT_PREDICTION_HISTORY_DB", str(history_backup))
    monkeypatch.setenv("TGOAT_OPERATIONAL_CYCLE_DB", str(history_backup))
    monkeypatch.setenv("TGOAT_RECOMMENDATION_DB", str(history_backup))

    # New repository connections model a process restart and must see the saved actions.
    actions = SQLiteOperationalActionRepository(history_path)
    assert len(actions.reviews(identity)) == 1
    assert len(actions.outcomes(identity)) == 1
    assert len(actions.reviews(tech_identity)) == 1
    assert actions.outcomes(tech_identity) == []
    actions.close()
    durable_notifications = SQLiteOperationalNotificationRepository(history_path)
    persisted, unread = durable_notifications.filter_for(
        "central", object_ids=None, include_global=True, limit=None, offset=0,
    )
    assert sum(item["prediction_id"] == identity for item in persisted) == 1
    assert unread == 0
    durable_notifications.close()

    # A second application lifespan reopens the same configured files as a
    # fresh backend startup and must expose the same user-facing records.
    with TestClient(app) as restarted_client:
        _login(restarted_client)
        restored_prediction = restarted_client.get(
            f"/api/v1/operational/predictions/{identity_path}"
        )
        assert restored_prediction.status_code == 200, restored_prediction.text
        assert restored_prediction.json()["identity"] == identity
        assert restarted_client.get(
            f"/api/v1/operational/predictions/{identity_path}/reviews"
        ).json() == [first_review.json()]
        assert restarted_client.get(
            f"/api/v1/operational/predictions/{identity_path}/outcomes"
        ).json() == [first_outcome.json()]
        restored_notifications = restarted_client.get("/api/v1/notifications").json()
        restored_matches = [
            item for item in restored_notifications["items"]
            if item["prediction_id"] == identity
        ]
        assert len(restored_matches) == 1
        assert restored_matches[0]["read"] is True


def test_default_shared_memory_database_supports_separate_runtime_repositories(
    tmp_path, monkeypatch, processed_dir,
):
    state_path = tmp_path / "feature-state.sqlite"
    bootstrap_demo_feature_state(state_path)
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(state_path))
    for name in ("TGOAT_PREDICTION_HISTORY_DB", "TGOAT_OPERATIONAL_CYCLE_DB",
                 "TGOAT_RECOMMENDATION_DB"):
        monkeypatch.delenv(name, raising=False)

    with TestClient(app) as client:
        _login(client)
        started = monotonic()
        with ThreadPoolExecutor(max_workers=20) as pool:
            responses = list(pool.map(
                lambda _: client.post(
                    "/api/v1/objects/5343/risk/operational",
                    params={"snapshot_ts": SNAPSHOT.isoformat()},
                ),
                range(20),
            ))
        elapsed = monotonic() - started
        assert elapsed < 300, f"20 concurrent operational requests took {elapsed:.2f}s"
        assert all(response.status_code == 200 for response in responses), [
            response.text for response in responses if response.status_code != 200
        ]
        predictions = [response.json()["prediction"] for response in responses]
        assert len({prediction["identity"] for prediction in predictions}) == 1
        prediction = predictions[0]
        assert prediction["prediction_status"] == "available"
        notifications = client.get("/api/v1/notifications")
        assert notifications.status_code == 200, notifications.text
        assert sum(
            item["prediction_id"] == prediction["identity"]
            for item in notifications.json()["items"]
        ) == 1
        identity_path = quote(prediction["identity"], safe="")
        action = client.post(
            f"/api/v1/operational/predictions/{identity_path}/review",
            json={"decision": "confirmed", "idempotency_key": "shared-memory"},
        )
        assert action.status_code == 201, action.text


def test_demo_cycle_cli_forces_manifest_object_scope(monkeypatch, capsys):
    from scripts import run_operational_demo_cycle
    from backend.app.services.operational_cycle import OperationalCycleResult

    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", "/tmp/demo-feature.sqlite")
    monkeypatch.setenv("TGOAT_PREDICTION_HISTORY_DB", "/tmp/demo-history.sqlite")
    monkeypatch.setenv("TGOAT_OPERATIONAL_OBJECT_IDS", "28")
    monkeypatch.setattr(sys, "argv", ["run_operational_demo_cycle"])

    class DemoCycle:
        def run_once(self, *, snapshot_ts, run_mode):
            assert snapshot_ts == SNAPSHOT
            assert run_mode == "demo"
            assert os.environ["TGOAT_OPERATIONAL_OBJECT_IDS"] == "5343"
            return OperationalCycleResult(
                cycle_id="demo-test", snapshot_ts=snapshot_ts, status="completed",
                object_count=1, available_count=1, degraded_count=0,
                positive_count=1, started_at=SNAPSHOT, finished_at=SNAPSHOT,
                run_mode=run_mode,
            )

    monkeypatch.setattr(
        run_operational_demo_cycle, "build_operational_cycle_service", DemoCycle,
    )
    run_operational_demo_cycle.main()
    assert '"object_count": 1' in capsys.readouterr().out
