from datetime import datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from backend.app.schemas import Object, Prediction
from backend.app.services import event_store, reference_data
from backend.app.services.mock_storage import (
    maintenance_repository, notification_repository, prediction_repository,
)
from backend.app.services.repositories import InMemoryMaintenanceRepository, InMemoryPredictionRepository
from backend.app.services import repositories
from backend.app.services.reference_data import get_channels_by_id, get_objects_by_id


def test_health_reports_components(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "degraded"
    assert body["components"]["reference_data"]["status"] == "available"
    assert body["components"]["postgresql"]["status"] == "unavailable"
    assert body["components"]["event_history"] == {
        "status": "available", "expected_years": [2025, 2026],
        "available_years": [2025, 2026], "detail": None,
    }


def test_replay_controls_are_disabled_in_normal_mode(client):
    response = client.get("/api/v1/replay/status")
    assert response.status_code == 200
    assert response.json() == {"mode": "normal", "state": "disabled"}
    assert client.post("/api/v1/replay/start").status_code == 409


def test_replay_mutation_requires_dispatcher_role(client):
    from backend.app.services.auth import auth_provider

    technician = auth_provider.authenticate("tech-28", "tech-demo")
    client.headers["Authorization"] = f"Bearer {auth_provider.issue_token(technician)}"
    assert client.post("/api/v1/replay/start").status_code == 403
    assert client.post("/api/v1/replay/step").status_code == 403


def test_replay_step_delegates_to_runtime_for_dispatcher(client, monkeypatch):
    class Runtime:
        def step(self):
            return {"mode": "replay", "state": "paused", "next_inference_at": "2026-05-05T14:59:59+00:00"}

    monkeypatch.setattr(client.app.state, "replay_runtime", Runtime(), raising=False)
    response = client.post("/api/v1/replay/step")
    assert response.status_code == 200
    assert response.json()["state"] == "paused"


def test_manifest_matching_current_reference_hash_is_available(client):
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "available"


def test_manifest_stale_reference_hash_is_corrupt(client, processed_dir):
    manifest = processed_dir / "events_2026.parquet.meta.json"
    data = json.loads(manifest.read_text())
    data["reference_sha256"] = "0" * 64
    manifest.write_text(json.dumps(data))
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "corrupt"
    assert "does not match current reference data" in component["detail"]


def test_manifest_malformed_reference_hash_is_corrupt(client, processed_dir):
    manifest = processed_dir / "events_2026.parquet.meta.json"
    data = json.loads(manifest.read_text())
    data["reference_sha256"] = "not-a-sha256"
    manifest.write_text(json.dumps(data))
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "corrupt"
    assert "invalid reference_sha256" in component["detail"]


def test_manifest_malformed_source_fingerprint_is_corrupt(client, processed_dir):
    manifest = processed_dir / "events_2026.parquet.meta.json"
    data = json.loads(manifest.read_text())
    data["source"]["mtime_ns"] = True
    manifest.write_text(json.dumps(data))
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "corrupt"
    assert "invalid source fingerprint" in component["detail"]


def test_health_and_history_distinguish_unavailable_storage(client, tmp_path, monkeypatch):
    monkeypatch.setattr(event_store, "PROCESSED_DIR", tmp_path)
    health = client.get("/api/v1/health").json()
    assert health["status"] == "degraded"
    assert health["components"]["event_history"]["status"] == "unavailable"
    response = client.get("/api/v1/channels/2866/history")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "event_history_unavailable"


def test_corrupted_history_storage_is_degraded(client, processed_dir):
    (processed_dir / "events_2026.parquet").write_bytes(b"corrupt")
    assert client.get("/api/v1/health").json()["status"] == "degraded"
    assert client.get("/api/v1/channels/2866/history").status_code == 503


def test_missing_manifest_is_not_available(client, processed_dir):
    (processed_dir / "events_2026.parquet.meta.json").unlink()
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "missing"


def test_partial_manifest_is_not_available(client, processed_dir):
    manifest = processed_dir / "events_2026.parquet.meta.json"
    data = json.loads(manifest.read_text())
    data.update(partial=True, limit=1)
    manifest.write_text(json.dumps(data))
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "partial"


def test_manifest_row_count_mismatch_is_corrupt(client, processed_dir):
    manifest = processed_dir / "events_2026.parquet.meta.json"
    data = json.loads(manifest.read_text())
    data["rows"] += 1
    manifest.write_text(json.dumps(data))
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "corrupt"


def test_manifest_wrong_pipeline_version_is_corrupt(client, processed_dir):
    manifest = processed_dir / "events_2026.parquet.meta.json"
    data = json.loads(manifest.read_text())
    data["pipeline_version"] = "2"
    manifest.write_text(json.dumps(data))
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "corrupt"


def test_missing_history_partition_is_partial_and_not_silent(client, processed_dir):
    (processed_dir / "events_2025.parquet").unlink()
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "partial" and component["available_years"] == [2026]
    assert client.get("/api/v1/channels/2866/history").status_code == 503


def test_missing_year_does_not_hide_corrupt_manifest(client, processed_dir):
    (processed_dir / "events_2025.parquet").unlink()
    manifest = processed_dir / "events_2026.parquet.meta.json"
    data = json.loads(manifest.read_text())
    data["reference_sha256"] = "bad"
    manifest.write_text(json.dumps(data))
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "corrupt"
    assert "invalid reference_sha256" in component["detail"]


def test_missing_year_does_not_hide_missing_manifest(client, processed_dir):
    (processed_dir / "events_2025.parquet").unlink()
    (processed_dir / "events_2026.parquet.meta.json").unlink()
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "missing"
    assert "manifest is missing" in component["detail"]


def test_missing_year_and_partial_manifest_are_both_reported(client, processed_dir):
    (processed_dir / "events_2025.parquet").unlink()
    manifest = processed_dir / "events_2026.parquet.meta.json"
    data = json.loads(manifest.read_text())
    data.update(partial=True, limit=1)
    manifest.write_text(json.dumps(data))
    component = client.get("/api/v1/health").json()["components"]["event_history"]
    assert component["status"] == "partial"
    assert "partial manifest" in component["detail"]
    assert "missing years: [2025]" in component["detail"]


def test_wrong_history_schema_is_corrupt(client, processed_dir):
    import polars as pl
    pl.DataFrame({"channel_id": [2866]}).write_parquet(processed_dir / "events_2026.parquet")
    assert client.get("/api/v1/health").json()["components"]["event_history"]["status"] == "corrupt"


def test_extra_history_column_is_corrupt(client, processed_dir):
    import polars as pl
    path = processed_dir / "events_2026.parquet"
    pl.read_parquet(path).with_columns(pl.lit("unexpected").alias("extra_column")).write_parquet(path)
    assert client.get("/api/v1/health").json()["components"]["event_history"]["status"] == "corrupt"


def test_empty_available_history_is_200(client, processed_dir):
    response = client.get("/api/v1/channels/2868/history")
    assert response.status_code == 200
    assert response.json()["events"] == []


def test_object_channel_dashboard_maintenance_consistency(client):
    prediction = client.get("/api/v1/predictions/1042").json()
    channel = client.get("/api/v1/channels/2866").json()
    obj = client.get("/api/v1/objects/28").json()
    dashboard = client.get("/api/v1/dashboard").json()
    maintenance = client.get("/api/v1/maintenance-requests", params={"object_id": 28}).json()
    assert prediction["risk_score"] == channel["risk_score"] == obj["risk_score"] == 0.82
    assert prediction["risk_level"] == channel["risk_level"] == obj["risk_level"] == "high"
    assert prediction["id"] == channel["risk_prediction_id"] == obj["risk_prediction_id"]
    assert prediction["score_kind"] == channel["risk_score_kind"] == obj["risk_score_kind"]
    assert channel["last_event_at"] == "2026-01-01T00:00:00Z"
    assert obj["active_predictions"] == 3
    assert obj["active_requests"] == maintenance["total"] == 1
    assert dashboard["high_risk_count"] == 1
    assert dashboard["top_objects"][0]["id"] == 28
    filtered = client.get("/api/v1/objects", params={"risk_level": "high"}).json()
    assert any(item["id"] == 28 for item in filtered["items"])


def test_dashboard_ranks_by_risk_level_not_incomparable_score(client, monkeypatch):
    from backend.app.api.routes import dashboard as dashboard_route
    monkeypatch.setattr(dashboard_route.prediction_repository, "active_object_ids", lambda: [28, 9])
    monkeypatch.setattr(dashboard_route, "object_risks", lambda ids: {
        28: {"risk_level": "high", "risk_score": 0.99, "risk_prediction_id": 1042,
             "risk_score_kind": "probability", "active_predictions": 1},
        9: {"risk_level": "critical", "risk_score": 0.01, "risk_prediction_id": 7,
            "risk_score_kind": "anomaly_score", "active_predictions": 1},
    })
    monkeypatch.setattr(dashboard_route, "active_request_counts", lambda ids: {i: 0 for i in ids})
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["top_objects"][:2]] == [9, 28]


def test_dashboard_active_request_count_matches_object_semantics(client):
    from backend.app.services.mock_storage import maintenance_repository
    for request_id, status in enumerate(("new", "assigned", "in_progress", "closed", "cancelled"), 600):
        maintenance_repository.create({
            "id": request_id, "object_id": 28, "channel_id": None, "prediction_id": None,
            "reason": "counter semantics", "priority": "low", "status": status,
            "source": "local", "external_request_id": None, "sync_status": "not_synced",
            "external_status": None, "created_at": "2026-09-16T10:00:00+00:00",
            "updated_at": "2026-09-16T10:00:00+00:00",
        })
    dashboard = client.get("/api/v1/dashboard").json()
    obj = client.get("/api/v1/objects/28").json()
    assert dashboard["active_requests_count"] == obj["active_requests"]
    assert obj["active_requests"] == 4  # seeded draft + new/assigned/in_progress


def test_object_channels_uses_batch_last_event_lookup(client, monkeypatch):
    calls = []
    original = event_store.get_latest_channel_events
    def tracked(ids, **kwargs): calls.append(ids); return original(ids, **kwargs)
    monkeypatch.setattr(event_store, "get_latest_channel_events", tracked)
    response = client.get("/api/v1/objects/28/channels")
    assert response.status_code == 200 and response.json()["total"] == 134
    assert len(calls) == 1 and len(calls[0]) == 134


def test_maintenance_active_count_is_not_limited_to_dashboard_page_size():
    items = [{"id": i, "status": "draft", "object_id": 28} for i in range(10_005)]
    items.extend({"id": 20_000 + i, "status": "closed", "object_id": 28} for i in range(3))
    assert InMemoryMaintenanceRepository(items).count_active() == 10_005


def test_broken_parent_is_exposed_safely(client):
    item = client.get("/api/v1/objects/5773").json()
    assert item["source_parent_id"] == 3831
    assert item["parent_id"] is None
    assert item["parent_reference_found"] is False


def test_geometry_provenance_is_explicit_and_reference_has_no_fake_coordinates(client):
    response = client.get("/api/v1/objects/28").json()
    assert response["geometry"] is None
    assert response["geometry_source"] == "not_provided"
    with pytest.raises(ValidationError, match="source requires geometry"):
        Object.model_validate({**response, "geometry_source": "synthetic_mvp"})
    with pytest.raises(ValidationError, match="requires real or synthetic_mvp"):
        Object.model_validate({**response, "geometry": {"type": "Point", "coordinates": [1, 2]}})
    reference_objects = json.loads(reference_data.DATA_DIR.joinpath("objects.json").read_text())
    assert all("geometry" not in item and "coordinates" not in item for item in reference_objects)


def test_history_order_limit_ranges_and_utc_output(client):
    body = client.get("/api/v1/channels/2866/history", params={"limit": 2}).json()
    assert [x["value"] for x in body["events"]] == ["v3", "v2"]
    assert all(x["timestamp"].endswith("Z") for x in body["events"])
    ranged = client.get("/api/v1/channels/2866/history", params={
        "from": "2025-12-31T22:00:00Z", "to": "2025-12-31T23:00:00Z"
    }).json()
    assert [x["value"] for x in ranged["events"]] == ["v2", "v1"]
    assert client.get("/api/v1/channels/2866/history", params={
        "from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"
    }).status_code == 422


def test_history_equivalent_timezones_and_naive_source_policy(client):
    urls = ["2025-12-31T22:00:00Z", "2026-01-01T01:00:00+03:00", "2026-01-01T01:00:00"]
    responses = [client.get("/api/v1/channels/2866/history", params={"from": value}).json()
                 for value in urls]
    assert responses[0] == responses[1] == responses[2]


def test_year_file_pruning(processed_dir):
    selected = event_store.select_event_files(
        datetime.fromisoformat("2026-01-01T00:00:00+03:00"),
        datetime.fromisoformat("2026-12-31T23:59:59+03:00"),
    )
    assert [year for year, _ in selected] == [2026]


def prediction_payload(**overrides):
    payload = {"id": 1, "scope_type": "object", "incident_type": "future_type",
               "object_id": 28, "score_kind": "mock", "risk_score": 0.5,
               "risk_level": "medium", "forecast_from_hours": 24,
               "forecast_to_hours": 48, "factors": [],
               "created_at": "2026-01-01T00:00:00+00:00", "status": "active"}
    payload.update(overrides)
    return payload


def test_prediction_schema_scopes_and_forecast():
    assert Prediction(**prediction_payload(scope_type="channel", channel_id=2866)).channel_id == 2866
    assert Prediction(**prediction_payload()).channel_id is None
    assert Prediction(**prediction_payload(scope_type="engineering_system", system_ref="opaque")).system_ref == "opaque"
    for changes in ({"object_id": None}, {"forecast_from_hours": -1}, {"forecast_to_hours": -1},
                    {"scope_type": "channel"}, {"scope_type": "engineering_system"}):
        with pytest.raises(ValidationError): Prediction(**prediction_payload(**changes))


def test_short_forecast_is_valid_and_reversed_horizon_is_invalid():
    assert Prediction(**prediction_payload(
        forecast_from_hours=6, forecast_to_hours=12,
    )).forecast_from_hours == 6
    with pytest.raises(ValidationError):
        Prediction(**prediction_payload(forecast_from_hours=12, forecast_to_hours=6))


def test_prediction_close_is_independent_from_review_and_outcome(client):
    assert client.patch("/api/v1/predictions/1042/review", json={
        "dispatcher_result": "inspection_requested",
    }).status_code == 200
    closed = client.post("/api/v1/predictions/1042/close")
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert client.get("/api/v1/predictions/1042/reviews").json()["total"] == 1
    assert client.post("/api/v1/predictions/1042/outcomes", json={
        "actual_outcome": "not_confirmed",
    }).status_code == 201
    assert client.get("/api/v1/dashboard").json()["high_risk_count"] == 0


def test_expired_prediction_is_excluded_from_current_risk():
    fixed_now = datetime.fromisoformat("2026-01-01T02:00:00+00:00")
    repository = InMemoryPredictionRepository([prediction_payload(
        created_at="2026-01-01T00:00:00+00:00", forecast_from_hours=0,
        forecast_to_hours=1,
    )], {28: {}}, {}, clock=lambda: fixed_now)
    assert repository.get(1)["status"] == "expired"
    assert repository.object_risk_summaries([28])[28]["risk_score"] is None


@pytest.mark.parametrize("reference_time", [
    datetime.fromisoformat("2000-01-01T00:00:00+00:00"),
    datetime.fromisoformat("2050-12-31T23:59:00+00:00"),
])
def test_demo_prediction_seed_is_active_after_reset_at_any_date(monkeypatch, reference_time):
    from backend.app.services import mock_storage

    with monkeypatch.context() as patcher:
        patcher.setattr(mock_storage, "utc_now", lambda: reference_time)
        mock_storage.prediction_repository.reset()
        active, total = prediction_repository.filter(status="active", limit=None)
        assert total == 3
        assert {item["id"] for item in active} == {1042, 1043, 1044}
        assert all(0 < (reference_time - datetime.fromisoformat(item["created_at"])).total_seconds()
                   <= 60 * 3 for item in active)
    mock_storage.reset_mock_storage()


@pytest.mark.parametrize("reference_time", [
    datetime.fromisoformat("1990-01-01T00:00:00+00:00"),
    datetime.fromisoformat("2099-12-31T23:59:00+00:00"),
])
def test_related_demo_seeds_share_a_causal_clock(monkeypatch, reference_time):
    from backend.app.services import mock_storage

    with monkeypatch.context() as patcher:
        patcher.setattr(mock_storage, "utc_now", lambda: reference_time)
        mock_storage.reset_mock_storage()
        prediction = prediction_repository.get(1042)
        notification, _ = notification_repository.filter_for(
            "central", object_ids=None, include_global=True, limit=None
        )
        maintenance = maintenance_repository.get(501)
        assert prediction["status"] == "active"
        assert notification[0]["id"] == 701 and notification[0]["prediction_id"] == prediction["id"]
        assert datetime.fromisoformat(notification[0]["created_at"]) >= datetime.fromisoformat(
            prediction["created_at"]
        )
        assert maintenance["prediction_id"] == prediction["id"]
        assert maintenance["object_id"] == prediction["object_id"] == notification[0]["object_id"]
        assert maintenance["channel_id"] == prediction["channel_id"] == notification[0]["channel_id"]
        assert datetime.fromisoformat(maintenance["created_at"]) >= datetime.fromisoformat(
            prediction["created_at"]
        )
        assert datetime.fromisoformat(maintenance["created_at"]) >= datetime.fromisoformat(
            notification[0]["created_at"]
        )
        assert datetime.fromisoformat(maintenance["updated_at"]) >= datetime.fromisoformat(
            maintenance["created_at"]
        )
    mock_storage.reset_mock_storage()


@pytest.mark.parametrize("kind,score", [
    ("probability", -0.1), ("probability", 1.1),
    ("proxy_probability", float("nan")), ("anomaly_score", float("inf")),
])
def test_prediction_score_validation(kind, score):
    with pytest.raises(ValidationError):
        Prediction(**prediction_payload(score_kind=kind, risk_score=score))


def test_entity_risk_provenance_fields_are_atomic():
    from backend.app.schemas import Object
    with pytest.raises(ValidationError, match="all be null or all be populated"):
        Object(id=1, name="x", type=None, channels_count=0, risk_score=.5,
               risk_level=None, risk_prediction_id=None, risk_score_kind=None,
               active_predictions=0, active_requests=0)


def test_risk_aggregation_uses_level_then_newest_then_id(monkeypatch):
    monkeypatch.setattr(repositories, "utc_now", lambda: datetime.fromisoformat(
        "2026-01-03T00:00:00+00:00"
    ))
    predictions = [
        prediction_payload(id=1, risk_level="high", risk_score=.95, score_kind="probability"),
        prediction_payload(id=2, risk_level="critical", risk_score=.2, score_kind="anomaly_score"),
        prediction_payload(id=3, risk_level="critical", risk_score=.1, score_kind="rule_score",
                           created_at="2026-01-02T00:00:00+00:00"),
        prediction_payload(id=4, risk_level="critical", risk_score=.05, score_kind="mock",
                           created_at="2026-01-02T00:00:00+00:00"),
    ]
    repo = InMemoryPredictionRepository(predictions, {28: {}}, {})
    risk = repo.object_risk_summaries([28])[28]
    assert (risk["risk_level"], risk["risk_prediction_id"], risk["risk_score_kind"]) == (
        "critical", 4, "mock"
    )


def test_prediction_repository_rejects_channel_object_mismatch():
    with pytest.raises(ValueError, match="does not belong"):
        InMemoryPredictionRepository(
            [prediction_payload(scope_type="channel", channel_id=2866, object_id=9)],
            get_objects_by_id(), get_channels_by_id(),
        )


def test_prediction_filters_pagination_and_time_policy(client):
    assert client.get("/api/v1/predictions", params={"scope_type": "object"}).json()["total"] == 1
    assert client.get("/api/v1/predictions", params={"incident_type": "proxy_event"}).json()["total"] == 1
    page = client.get("/api/v1/predictions", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    equivalent = [client.get("/api/v1/predictions", params={"from": value}).json()
                  for value in ("2026-09-16T10:00:00Z", "2026-09-16T13:00:00+03:00",
                                "2026-09-16T13:00:00")]
    assert equivalent[0] == equivalent[1] == equivalent[2]
    assert client.get("/api/v1/predictions", params={
        "from": "2026-09-17T00:00:00Z", "to": "2026-09-16T00:00:00Z"
    }).status_code == 422
    assert client.get("/api/v1/predictions", params={"status": "invalid"}).status_code == 422


def test_review_is_append_only_and_utc(client):
    for decision, comment in (("monitoring", "first"), ("confirmed", "second")):
        assert client.patch("/api/v1/predictions/1042/review", json={
            "dispatcher_result": decision, "reason_code": "code", "comment": comment,
            "reviewed_by": "dispatcher",
        }).status_code == 200
    items = client.get("/api/v1/predictions/1042/reviews").json()["items"]
    assert [x["comment"] for x in items] == ["first", "second"]
    assert all(x["reviewed_at"].endswith("Z") for x in items)
    assert "actual_outcome" not in items[0]
    prediction = client.get("/api/v1/predictions/1042").json()
    assert prediction["status"] == "active" and prediction["dispatcher_result"] == "confirmed"
    assert client.get("/api/v1/objects/28").json()["risk_prediction_id"] == 1042


def test_recommendations_are_typed_and_linked(client):
    prediction = client.get("/api/v1/predictions/1042").json()
    assert prediction["recommendations"] == [{
        "prediction_id": 1042, "code": "inspect-channel", "title": "Проверить канал",
        "description": "Проверить датчик и линию связи по регламенту.", "source": "mock",
    }]


def test_outcome_is_separate_append_only_and_utc(client):
    assert client.post("/api/v1/predictions/1042/outcomes", json={}).status_code == 422
    for outcome in ("incident_confirmed", "repair_confirmed"):
        response = client.post("/api/v1/predictions/1042/outcomes", json={
            "actual_outcome": outcome, "reason_code": "inspection", "confirmed_by": "engineer"
        })
        assert response.status_code == 201 and response.json()["confirmed_at"].endswith("Z")
    outcomes = client.get("/api/v1/predictions/1042/outcomes").json()
    assert [x["actual_outcome"] for x in outcomes["items"]] == ["incident_confirmed", "repair_confirmed"]
    assert client.get("/api/v1/predictions/1042/reviews").json()["total"] == 0


def test_maintenance_reference_validation(client):
    base = {"object_id": 28, "reason": "Проверка", "priority": "high"}
    created = client.post("/api/v1/maintenance-requests", json={
        **base, "channel_id": 2866, "prediction_id": 1042
    })
    assert created.status_code == 201
    assert created.json()["created_at"].endswith("Z")
    assert created.json()["status"] == "draft"
    assert created.json()["source"] == "local"
    assert created.json()["external_request_id"] is None
    assert created.json()["sync_status"] == "not_synced"
    assert created.json()["external_status"] is None
    assert client.post("/api/v1/maintenance-requests", json={**base, "object_id": 999999}).status_code == 404
    assert client.post("/api/v1/maintenance-requests", json={**base, "channel_id": 999999}).status_code == 404
    assert client.post("/api/v1/maintenance-requests", json={**base, "prediction_id": 999999}).status_code == 404
    assert client.post("/api/v1/maintenance-requests", json={**base, "object_id": 9, "channel_id": 2866}).status_code == 409


def test_maintenance_integration_fields_are_server_owned(client):
    base = {"object_id": 28, "channel_id": 2866, "prediction_id": 1042,
            "reason": "Проверка", "priority": "high"}
    attempted_create = {
        **base, "source": "external", "external_request_id": "forged-1",
        "sync_status": "synced", "external_status": "assigned",
    }
    assert client.post("/api/v1/maintenance-requests", json=attempted_create).status_code == 422
    created = client.post("/api/v1/maintenance-requests", json=base)
    assert created.status_code == 201
    request_id = created.json()["id"]
    before = created.json()
    response = client.patch(f"/api/v1/maintenance-requests/{request_id}", json={
        "status": "assigned", "sync_status": "synced", "external_status": "assigned",
    })
    assert response.status_code == 422
    unchanged = client.get("/api/v1/maintenance-requests", params={"object_id": 28}).json()
    saved = next(item for item in unchanged["items"] if item["id"] == request_id)
    assert {key: saved[key] for key in ("source", "external_request_id", "sync_status", "external_status")} == {
        "source": "local", "external_request_id": None,
        "sync_status": "not_synced", "external_status": None,
    }
    assert saved["status"] == before["status"] == "draft"


def test_maintenance_prediction_conflicts(client):
    other_channel = next(x for x in get_channels_by_id().values()
                         if x["object_id"] == 28 and x["channel_id"] != 2866)
    response = client.post("/api/v1/maintenance-requests", json={
        "object_id": 28, "channel_id": other_channel["channel_id"], "prediction_id": 1042,
        "reason": "conflict", "priority": "high",
    })
    assert response.status_code == 409


def test_notifications_are_per_recipient_and_utc(client):
    listing = client.get("/api/v1/notifications").json()
    assert listing["unread_count"] == 1 and listing["items"][0]["created_at"].endswith("Z")
    assert listing["items"][0]["severity"] == "critical"
    assert listing["items"][0]["source_kind"] == "legacy_mock"
    assert client.patch("/api/v1/notifications/701", json={"read": True}).json()["read"] is True
    login = client.post("/api/v1/auth/login", json={"username": "dispatch-9", "password": "dispatch-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    assert client.get("/api/v1/notifications").json()["items"][0]["read"] is False
    assert client.patch("/api/v1/notifications/701", json={"read": True}).json()["read"] is True
    login = client.post("/api/v1/auth/login", json={"username": "central", "password": "central-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    assert client.get("/api/v1/notifications").json()["items"][0]["read"] is True
    other, unread = notification_repository.filter_for("other-user", limit=100, offset=0)
    assert other[0]["read"] is False and unread == 1


def test_operational_notifications_exclude_legacy_mock_and_sort_datetime_timestamps(client):
    class OperationalNotifications:
        def filter_for(self, *_args, **_kwargs):
            return ([{
                "id": 1_000_001, "source_kind": "operational_ml", "type": "risk",
                "severity": "warning", "title": "PostgreSQL notification",
                "message": "Operational notification fixture", "prediction_id": "prediction/pg",
                "object_id": 28, "channel_id": None, "card_id": "card/pg", "read": False,
                "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            }, {
                "id": 1_000_002, "source_kind": "operational_ml", "type": "risk",
                "severity": "warning", "title": "Older PostgreSQL notification",
                "message": "Older operational notification fixture", "prediction_id": "prediction/pg-old",
                "object_id": 28, "channel_id": None, "card_id": "card/pg-old", "read": False,
                "created_at": "2098-01-01T00:00:00+00:00",
            }], 2)

        def get(self, _notification_id):
            return None

    client.app.state.operational_notification_repository = OperationalNotifications()
    response = client.get("/api/v1/notifications")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [1_000_001, 1_000_002]
    assert body["items"][0]["source_kind"] == "operational_ml"
    assert body["unread_count"] == 2
    assert client.patch("/api/v1/notifications/701", json={"read": True}).status_code == 404


def test_notifications_are_filtered_by_object_scope(client):
    notification_repository._items.append({
        "id": 702, "type": "risk", "severity": "high", "title": "Out of scope",
        "message": "scope check", "prediction_id": None, "object_id": 9, "channel_id": None,
        "created_at": "2026-09-16T10:02:00+00:00",
    })
    login = client.post("/api/v1/auth/login", json={"username": "tech-28", "password": "tech-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    listing = client.get("/api/v1/notifications").json()
    assert [item["id"] for item in listing["items"]] == [701]
    assert listing["unread_count"] == 1
    assert client.patch("/api/v1/notifications/702", json={"read": True}).status_code == 404
    assert notification_repository.is_read(702, "tech-28") is False


def test_central_sees_global_notifications_and_scoped_user_does_not(client):
    notification_repository._items.append({
        "id": 703, "type": "system", "severity": "info", "title": "System notice",
        "message": "global", "prediction_id": None, "object_id": None, "channel_id": None,
        "created_at": "2026-09-16T10:03:00+00:00",
    })
    central = client.get("/api/v1/notifications").json()
    assert 703 in {item["id"] for item in central["items"]}
    assert client.patch("/api/v1/notifications/703", json={"read": True}).json()["read"] is True
    login = client.post("/api/v1/auth/login", json={"username": "tech-28", "password": "tech-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    scoped = client.get("/api/v1/notifications").json()
    assert 703 not in {item["id"] for item in scoped["items"]}


def test_scoped_pagination_filters_before_offset_for_predictions(client, monkeypatch):
    from backend.app.services.mock_storage import prediction_repository
    monkeypatch.setitem(prediction_repository._objects, 9, {})
    for i in range(1005):
        prediction_repository._items.append(prediction_repository._validate({
            "id": 30_000 + i, "scope_type": "object", "incident_type": "pagination",
            "object_id": 9, "channel_id": None, "system_ref": None, "score_kind": "mock",
            "model_version": "test", "risk_score": 0.1, "risk_level": "low",
            "forecast_from_hours": 0, "forecast_to_hours": 1_000_000, "factors": [],
            "created_at": f"2028-01-01T00:00:{(i % 60):02d}+00:00", "status": "active",
            "dispatcher_result": None,
        }))
    # Object 28 is the allowed scope; the much larger object-9 set must be filtered first.
    login = client.post("/api/v1/auth/login", json={"username": "tech-28", "password": "tech-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    result = client.get("/api/v1/predictions", params={"limit": 1, "offset": 1}).json()
    assert result["total"] == 3
    assert result["items"][0]["object_id"] == 28


def test_pagination_beyond_first_thousand_predictions(client):
    from backend.app.services.mock_storage import prediction_repository
    for i in range(1005):
        prediction_repository._items.append(prediction_repository._validate({
            "id": 40_000 + i, "scope_type": "object", "incident_type": "pagination",
            "object_id": 28, "channel_id": None, "system_ref": None, "score_kind": "mock",
            "model_version": "test", "risk_score": 0.1, "risk_level": "low",
            "forecast_from_hours": 0, "forecast_to_hours": 1_000_000, "factors": [],
            "created_at": (datetime(2029, 1, 1, tzinfo=timezone.utc)
                           - timedelta(seconds=i)).isoformat(), "status": "active",
            "dispatcher_result": None,
        }))
    result = client.get("/api/v1/predictions", params={"limit": 1, "offset": 1002}).json()
    assert result["total"] == 1008 and result["items"][0]["id"] == 41_002


def test_maintenance_pagination_applies_scope_before_offset(client):
    from backend.app.main import app
    maintenance_repository = app.state.dispatcher_workflow
    for i in range(1002):
        maintenance_repository.create({"id": 50_000 + i, "object_id": 9,
            "channel_id": None, "prediction_id": None, "reason": "foreign pagination",
            "priority": "low", "status": "draft", "source": "local", "created_by_user_id":"tech-28", "created_by_role":"technician",
            "external_request_id": None, "sync_status": "not_synced", "external_status": None,
            "created_at": "2025-01-01T00:00:00+00:00", "updated_at": "2025-01-01T00:00:00+00:00"})
    for i in range(2):
        maintenance_repository.create({"id": 51_000 + i, "object_id": 28,
            "channel_id": None, "prediction_id": None, "reason": "in scope",
            "priority": "low", "status": "draft", "source": "local", "created_by_user_id":"tech-28", "created_by_role":"technician",
            "external_request_id": None, "sync_status": "not_synced", "external_status": None,
            "created_at": "2025-01-01T00:00:00+00:00", "updated_at": "2025-01-01T00:00:00+00:00"})
    login = client.post("/api/v1/auth/login", json={"username": "tech-28", "password": "tech-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    result = client.get("/api/v1/maintenance-requests", params={"limit": 1, "offset": 1}).json()
    assert result["total"] == 2
    assert result["items"][0]["object_id"] == 28 and result["items"][0]["reason"] == "in scope"


def test_notification_pagination_filters_scope_before_offset(client):
    for i in range(1002):
        notification_repository._items.append({"id": 60_000 + i, "type": "risk",
            "severity": "info", "title": "foreign", "message": "foreign",
            "prediction_id": None, "object_id": 9, "channel_id": None,
            "created_at": "2025-01-01T00:00:00+00:00"})
    notification_repository._items.append({"id": 61_002, "type": "risk", "severity": "info",
        "title": "in scope", "message": "in scope", "prediction_id": None,
        "object_id": 28, "channel_id": None, "created_at": "2025-01-01T00:00:00+00:00"})
    login = client.post("/api/v1/auth/login", json={"username": "tech-28", "password": "tech-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    result = client.get("/api/v1/notifications", params={"limit": 1, "offset": 1}).json()
    assert [item["id"] for item in result["items"]] == [61_002]
    assert result["unread_count"] == 2


def test_dashboard_aggregations_include_records_after_first_thousand(client):
    from backend.app.services.mock_storage import maintenance_repository, prediction_repository
    for i in range(1001):
        prediction_repository._items.append(prediction_repository._validate({
            "id": 70_000 + i, "scope_type": "object", "incident_type": "aggregate",
            "object_id": 28, "channel_id": None, "system_ref": None, "score_kind": "mock",
            "model_version": "test", "risk_score": 0.1, "risk_level": "low",
            "forecast_from_hours": 0, "forecast_to_hours": 1_000_000, "factors": [],
            "created_at": (datetime(2030, 1, 1, tzinfo=timezone.utc)
                           + timedelta(seconds=i)).isoformat(), "status": "active",
            "dispatcher_result": None,
        }))
        maintenance_repository.create({"id": 80_000 + i, "object_id": 28,
            "channel_id": None, "prediction_id": None, "reason": "aggregate",
            "priority": "low", "status": "draft", "source": "local",
            "external_request_id": None, "sync_status": "not_synced", "external_status": None,
            "created_at": "2025-01-01T00:00:00+00:00", "updated_at": "2025-01-01T00:00:00+00:00"})
    dashboard = client.get("/api/v1/dashboard").json()
    assert dashboard["risk_distribution"]["low"] == 1001
    assert dashboard["active_requests_count"] == 1002
    assert len(dashboard["recent_predictions"]) == 10


def test_openapi_has_expected_models_and_paths(client):
    document = client.get("/openapi.json").json()
    expected = {"/", "/api/v1/health", "/api/v1/dashboard", "/api/v1/objects",
                    "/api/v1/objects/{object_id}", "/api/v1/objects/{object_id}/channels",
                    "/api/v1/objects/{object_id}/risk",
                    "/api/v1/objects/{object_id}/risk/operational",
                    "/api/v1/objects/{object_id}/risk/history",
                    "/api/v1/objects/{object_id}/risk/cards",
                    "/api/v1/objects/{object_id}/risk/recommendations",
                    "/api/v1/objects/{object_id}/planned-work",
                "/api/v1/channels/{channel_id}", "/api/v1/channels/{channel_id}/history",
                "/api/v1/channels/{channel_id}/predictions", "/api/v1/predictions",
                "/api/v1/predictions/{prediction_id}",
                "/api/v1/predictions/{prediction_id}/close",
                "/api/v1/predictions/{prediction_id}/reviews",
                "/api/v1/predictions/{prediction_id}/review",
                "/api/v1/predictions/{prediction_id}/outcomes",
        "/api/v1/maintenance-requests", "/api/v1/maintenance-requests/{request_id}",
        "/api/v1/maintenance-requests/eligible-technicians", "/api/v1/maintenance-requests/{request_id}/{action}",
        "/api/v1/notifications", "/api/v1/notifications/{notification_id}"}
    expected |= {"/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/auth/logout"}
    expected |= {"/api/v1/audit-events"}
    expected |= {"/api/v1/access/principals", "/api/v1/access/principals/{user_id}"}
    expected |= {
        "/api/v1/replay/status", "/api/v1/replay/start", "/api/v1/replay/pause",
        "/api/v1/replay/resume", "/api/v1/replay/step", "/api/v1/replay/reset", "/api/v1/replay/speed",
        "/api/v1/operational/dashboard", "/api/v1/operational/objects/{object_id}",
        "/api/v1/operational/predictions",
        "/api/v1/operational/predictions/{identity}",
        "/api/v1/operational/predictions/{identity}/reviews",
        "/api/v1/operational/predictions/{identity}/review",
        "/api/v1/operational/predictions/{identity}/outcomes",
    }
    expected |= {"/api/v1/signals", "/api/v1/signals/monitoring", "/api/v1/signals/{card_id}", "/api/v1/signals/{card_id}/decision"}
    expected |= {'/api/v1/objects/catalog','/api/v1/operational/history'}
    expected |= {
        '/api/v1/integrations/status', '/api/v1/integrations/simulated-live/status',
        '/api/v1/integrations/simulated-live/step', '/api/v1/integrations/simulated-live/start',
        '/api/v1/integrations/simulated-live/pause', '/api/v1/integrations/equipment',
        '/api/v1/integrations/work-status/{request_id}', '/api/v1/integrations/weather',
        '/api/v1/scenarios/{scenario_type}/objects/{object_id}',
    }
    assert expected == set(document["paths"])
    assert document["info"]["version"] == "0.2.0"
    dashboard_formats = document["paths"]["/api/v1/operational/dashboard"]["get"]["responses"]["200"]["content"]
    assert {"application/json", "application/xml"} <= dashboard_formats.keys()
    assert dashboard_formats["application/xml"]["schema"]["type"] == "string"
    schemas = document["components"]["schemas"]
    assert {"PredictionOutcome", "Recommendation",
            "AuthTokenResponse", "CurrentUserResponse", "OperationalReviewRequest",
            "OperationalReviewResponse", "OperationalOutcomeRequest",
            "OperationalOutcomeResponse", "OperationalPredictionJournalItem",
            "OperationalPredictionJournalResponse"} <= schemas.keys()
    assert set(schemas["MaintenanceCreate"]["properties"]) == {
        "object_id", "channel_id", "prediction_id", "reason", "priority",
    }
    assert set(schemas["MaintenanceUpdate"]["properties"]) == {"status"}
    assert {"risk_prediction_id", "risk_score_kind"} <= schemas["Object"]["properties"].keys()
    assert schemas["Object"]["properties"]["geometry_source"]["enum"] == [
        "real", "synthetic_mvp", "unknown", "not_provided"
    ]
    assert {"risk_prediction_id", "risk_score_kind"} <= schemas["Channel"]["properties"].keys()
    assert {item.get("type") for item in schemas["Object"]["properties"]["risk_prediction_id"]["anyOf"]} == {
        "integer", "null"
    }
    assert schemas["Prediction"]["properties"]["recommendations"]["items"]["$ref"].endswith(
        "/Recommendation"
    )
    assert "reviewed_by" not in schemas["PredictionReviewRequest"]["properties"]
    assert "confirmed_by" not in schemas["PredictionOutcomeCreate"]["properties"]
    assert document["paths"]["/api/v1/auth/login"]["post"]["responses"]["200"]["content"][
        "application/json"]["schema"]["$ref"].endswith("/AuthTokenResponse")
    get_predictions = document["paths"]["/api/v1/predictions"]["get"]
    parameters = {x["name"]: x for x in get_predictions["parameters"]}
    assert parameters.keys() >= {
        "from", "to", "object_id", "channel_id", "sensor_type", "scope_type",
        "incident_type", "risk_level", "status", "limit", "offset",
    }
    assert parameters["limit"]["schema"]["maximum"] == 1000
    assert parameters["offset"]["schema"]["minimum"] == 0


def test_root_does_not_claim_operational_health(client):
    assert client.get("/").json() == {
        "service": "T.G.O.A.T. Moscollector API", "version": "0.2.0", "docs": "/docs"
    }


def test_reference_corruption_is_degraded_and_routes_return_503(client, tmp_path, monkeypatch):
    for name in ("objects.json", "channels.json", "reference_meta.json"):
        (tmp_path / name).write_text("{}" if name == "reference_meta.json" else "[]")
    (tmp_path / "objects.json").write_text("not-json")
    monkeypatch.setattr(reference_data, "DATA_DIR", tmp_path)
    reference_data.clear_reference_cache()
    health = client.get("/api/v1/health").json()
    assert health["components"]["reference_data"]["status"] == "corrupt"
    response = client.get("/api/v1/objects")
    assert response.status_code == 503
    reference_data.clear_reference_cache()
