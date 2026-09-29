from datetime import datetime
import json
import os

import polars as pl
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.processed_schema import PROCESSED_SCHEMA
from backend.app.services import event_store
from backend.app.services.reference_data import get_reference_meta
from backend.app.services.mock_storage import reset_mock_storage
from backend.app.services.auth import auth_provider
from scripts.normalization import PIPELINE_VERSION, metadata_path


_LIFESPAN_STATE = (
    "runtime_mode",
    "risk_inference_service",
    "operational_forecast_service",
    "operational_cycle_service",
    "operational_cycle_repository",
    "operational_action_repository",
    "operational_notification_repository",
    "operational_read_service",
    "replay_runtime",
    "dispatcher_workflow",
    "operational_database",
    "audit_repository",
    "access_repository",
)


def _clear_application_lifespan_state():
    for name in _LIFESPAN_STATE:
        if hasattr(app.state, name):
            delattr(app.state, name)


@pytest.fixture(autouse=True)
def isolated_storage():
    previous_storage = os.environ.get("TGOAT_OPERATIONAL_STORAGE")
    os.environ["TGOAT_OPERATIONAL_STORAGE"] = "sqlite-test"
    _clear_application_lifespan_state()
    reset_mock_storage()
    yield
    _clear_application_lifespan_state()
    reset_mock_storage()
    if previous_storage is None:
        os.environ.pop("TGOAT_OPERATIONAL_STORAGE", None)
    else:
        os.environ["TGOAT_OPERATIONAL_STORAGE"] = previous_storage


@pytest.fixture
def processed_dir(tmp_path, monkeypatch):
    processed = tmp_path / "processed"
    processed.mkdir()
    def frame(channel_ids, timestamps, values, alarms, year):
        size = len(channel_ids)
        data = {name: [None] * size for name in PROCESSED_SCHEMA}
        data.update({
            "event_id_raw": [str(i) for i in range(size)],
            "channel_id_raw": [str(i) for i in channel_ids], "date": ["x"] * size,
            "time": ["x"] * size, "alarm_raw": [str(x) for x in alarms],
            "event_id": list(range(size)), "channel_id": channel_ids,
            "timestamp": timestamps, "sensor_value": values, "is_alarm": alarms,
            "invalid_alarm": [False] * size, "source_year": [year] * size,
            "channel_reference_found": [True] * size,
            "object_reference_found": [True] * size,
            "invalid_channel_id": [False] * size, "invalid_event_id": [False] * size,
            "invalid_timestamp": [False] * size,
        })
        return pl.DataFrame(data, schema=PROCESSED_SCHEMA)
    frame([2866], [datetime(2025, 12, 31, 23, 0)], ["old"], [False], 2025).write_parquet(
        processed / "events_2025.parquet"
    )
    frame(
        [2866, 2866, 2866, 9999],
        [datetime(2026, 1, 1, 1), datetime(2026, 1, 1, 2),
         datetime(2026, 1, 1, 3), datetime(2026, 1, 1, 4)],
        ["v1", "v2", "v3", "other"], [True, False, True, False], 2026,
    ).write_parquet(processed / "events_2026.parquet")
    for year in (2025, 2026):
        output = processed / f"events_{year}.parquet"
        rows = pl.scan_parquet(output).select(pl.len()).collect().item()
        metadata_path(output).write_text(json.dumps({
            "pipeline_version": PIPELINE_VERSION, "year": year,
            "source": {"size": 1, "mtime_ns": 1},
            "reference_sha256": get_reference_meta()["source_sha256"],
            "rows": rows, "limit": None, "partial": False,
        }), encoding="utf-8")
    monkeypatch.setattr(event_store, "PROCESSED_DIR", processed)
    monkeypatch.setattr(event_store, "YEARS", [2025, 2026])
    return processed


@pytest.fixture
def client(processed_dir):
    from backend.app.services.prediction_history import SQLitePredictionHistoryRepository
    from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow
    from backend.app.services.mock_storage import maintenance_repository
    history = SQLitePredictionHistoryRepository()
    workflow = SQLiteDispatcherWorkflow(history)
    for seed in maintenance_repository.filter(limit=1000)[0]:
        item = workflow.create({**seed, 'created_by_user_id':'central', 'created_by_role':'central_dispatcher'})
        with history._connection:
            history._connection.execute('PRAGMA defer_foreign_keys=ON')
            history._connection.execute('UPDATE maintenance_events SET request_id=? WHERE request_id=?', (seed['id'],item['id']))
            history._connection.execute('UPDATE maintenance_requests SET id=? WHERE id=?', (seed['id'],item['id']))
    app.state.dispatcher_workflow = workflow
    test_client = TestClient(app)
    central = auth_provider.authenticate("central", "central-demo")
    test_client.headers["Authorization"] = f"Bearer {auth_provider.issue_token(central)}"
    yield test_client
    history.close()
