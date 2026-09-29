import csv
from datetime import datetime, timezone
from types import SimpleNamespace

from backend.app.services.planned_work import FIELDS


def test_planned_work_api_reports_missing_source_without_claiming_no_work(client, tmp_path, monkeypatch):
    from backend.app.services import planned_work

    monkeypatch.setattr(planned_work, "DEFAULT_PATH", tmp_path / "not-provided.csv")
    response = client.get("/api/v1/objects/28/planned-work")
    assert response.status_code == 200
    body = response.json()
    assert body["source_status"] == "not_provided"
    assert body["active"] == body["upcoming"] == body["recent"] == []


def test_supplied_schedules_are_available_but_never_guessed_onto_an_object(client):
    response = client.get("/api/v1/objects/28/planned-work")
    assert response.status_code == 200
    body = response.json()
    assert body["source_status"] == "available"
    assert body["unmapped_records"] == 613
    assert body["active"] == body["upcoming"] == body["recent"] == []


def test_planned_work_api_respects_dispatcher_object_scope(client):
    from backend.app.services.auth import auth_provider

    district_user = auth_provider.authenticate("dispatch-9", "dispatch-demo")
    client.headers["Authorization"] = f"Bearer {auth_provider.issue_token(district_user)}"
    assert client.get("/api/v1/objects/28/planned-work").status_code == 200
    assert client.get("/api/v1/objects/5773/planned-work").status_code == 404


def test_planned_work_api_caps_requested_time_to_replay_virtual_time(client, tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services import planned_work

    path = tmp_path / "canonical.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({"planned_work_id": "future-work", "source_file": "customer.xlsx",
                         "source_row": 2, "object_id": 28, "object_name_raw": "Object 28",
                         "equipment_context": "gas sensors", "work_type": "testing",
                         "planned_start": "2026-05-10", "planned_end": "2026-05-10",
                         "date_precision": "date_only", "mapping_method": "source_object_id",
                         "mapping_confidence": "exact", "notes": ""})
    monkeypatch.setattr(planned_work, "DEFAULT_PATH", path)
    virtual_now = datetime(2026, 5, 5, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(app.state, "replay_runtime", SimpleNamespace(
        clock=SimpleNamespace(now=lambda: virtual_now), end_at=datetime(2026, 5, 20, tzinfo=timezone.utc)
    ), raising=False)

    response = client.get("/api/v1/objects/28/planned-work?as_of=2026-05-20T12:00:00Z")
    assert response.status_code == 200
    body = response.json()
    assert body["as_of"].startswith("2026-05-05T15:00:00+03:00")
    assert body["active"] == []
    assert [item["planned_work_id"] for item in body["upcoming"]] == ["future-work"]
