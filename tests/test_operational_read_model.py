from datetime import datetime, timezone
from xml.etree import ElementTree
from fastapi.testclient import TestClient

from backend.app.services.operational_cycle import SQLiteOperationalCycleRepository
from backend.app.services.operational_read_model import OperationalRiskReadService
from backend.app.services.prediction_history import OperationalForecastService, SQLitePredictionHistoryRepository
from backend.app.services.recommendations import RecommendationService, SQLiteRecommendationRepository
from backend.app.services.auth import auth_provider
from backend.app.main import app


def test_empty_operational_read_model_is_explicit_no_prediction(tmp_path):
    forecast = OperationalForecastService(SQLitePredictionHistoryRepository(tmp_path / "history.db"),
                                          RecommendationService(SQLiteRecommendationRepository(tmp_path / "rec.db")))
    read = OperationalRiskReadService(forecast, SQLiteOperationalCycleRepository(tmp_path / "cycles.db"), lambda: [1, 2])
    dashboard = read.dashboard([1, 2])
    state = read.object_state(1)
    assert dashboard.cycle_status == "no_cycle" and dashboard.no_prediction_count == 2
    assert state.status == "no_prediction" and state.risk_probability is None and state.risk_score_kind == "ml"


def test_read_model_keeps_degraded_separate_from_negative(tmp_path):
    forecast = OperationalForecastService(SQLitePredictionHistoryRepository(tmp_path / "history.db"),
                                          RecommendationService(SQLiteRecommendationRepository(tmp_path / "rec.db")))
    cycles = SQLiteOperationalCycleRepository(tmp_path / "cycles.db")
    read = OperationalRiskReadService(forecast, cycles, lambda: [1, 2])
    result = {
        "object_id": 1, "snapshot_ts": "2024-01-01T02:59:59+00:00", "generated_at": "2024-01-01T03:00:00+00:00",
        "risk_probability": None, "risk_flag": None, "threshold": None, "status": "degraded",
        "risk_score_kind": "ml", "degraded_reason": "provider_unavailable", "model_version": None,
        "feature_schema_version": None, "reference_snapshot_id": None, "reference_hash": None,
    }
    forecast.record(result)
    state = read.object_state(1)
    assert state.status == "degraded" and state.risk_flag is None
    assert read.object_state(2).status == "no_prediction"


def test_operational_dashboard_api_is_scoped_and_does_not_fallback_to_mock(processed_dir):
    with TestClient(app) as client:
        central_user = auth_provider.authenticate("central", "central-demo")
        client.headers["Authorization"] = f"Bearer {auth_provider.issue_token(central_user)}"
        central = client.get("/api/v1/operational/dashboard")
        assert central.status_code == 200
        central_body = central.json()
        assert central_body["cycle_status"] == "no_cycle"
        assert central_body["no_prediction_count"] == central_body["object_count"]
        assert central_body["positive_count"] == 0
        central_xml = client.get(
            "/api/v1/operational/dashboard", headers={"Accept": "application/xml"}
        )
        assert central_xml.status_code == 200
        assert central_xml.headers["content-type"].startswith("application/xml")
        central_root = ElementTree.fromstring(central_xml.content)
        assert central_root.tag == "operational_dashboard"
        assert central_root.findtext("object_count") == str(central_body["object_count"])
        assert len(central_root.findall("./items/item")) == central_body["object_count"]
        technician = auth_provider.authenticate("tech-28", "tech-demo")
        client.headers["Authorization"] = f"Bearer {auth_provider.issue_token(technician)}"
        scoped = client.get("/api/v1/operational/dashboard")
        assert scoped.status_code == 200
        assert scoped.json()["object_count"] < central_body["object_count"]
        assert scoped.json()["positive_count"] == 0
        scoped_xml = client.get(
            "/api/v1/operational/dashboard", headers={"Accept": "application/xml"}
        )
        scoped_root = ElementTree.fromstring(scoped_xml.content)
        assert scoped_xml.status_code == 200
        assert scoped_root.findtext("object_count") == str(scoped.json()["object_count"])
        assert len(scoped_root.findall("./items/item")) == scoped.json()["object_count"]
        assert client.get("/api/v1/operational/objects/9").status_code == 404
