from datetime import datetime, timezone

from backend.app.services.mock_storage import audit_repository


def _login(client, username, password):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def test_operator_changes_are_audited_with_authenticated_actor(client):
    _login(client, "dispatch-9", "dispatch-demo")
    response = client.patch("/api/v1/predictions/1042/review", json={
        "dispatcher_result": "inspection_requested", "reviewed_by": "spoofed-user",
    })
    assert response.status_code == 200
    assert client.post("/api/v1/predictions/1042/close").status_code == 200
    draft = client.post('/api/v1/maintenance-requests',json={'object_id':28,'reason':'Проверка','priority':'normal'}).json()
    assert client.post(f"/api/v1/maintenance-requests/{draft['id']}/submit",json={'expected_version':draft['version'],'idempotency_key':'audit-submit'}).status_code == 200
    assert client.patch("/api/v1/notifications/701", json={"read": True}).status_code == 200

    _login(client, "central", "central-demo")
    assert client.post("/api/v1/predictions/1042/outcomes", json={
        "actual_outcome": "confirmed", "confirmed_by": "spoofed-user",
    }).status_code == 201

    events = client.get("/api/v1/audit-events").json()["items"]
    assert {event["action"] for event in events} >= {
        "prediction.review.created", "prediction.closed", "maintenance_request.submit",
        "notification.read_state_changed", "prediction.outcome.confirmed",
    }
    dispatcher_events = [event for event in events if event["user_id"] == "dispatch-9"]
    assert dispatcher_events and all(event["role"] == "district_dispatcher" for event in dispatcher_events)
    stored, _ = audit_repository.filter(action="request.patch", limit=None)
    assert any(event["path"] == "/api/v1/predictions/1042/review"
               and event["result"] == "success" for event in stored)
    stored, _ = audit_repository.filter(action="request.post", limit=None)
    assert any(event["path"] == "/api/v1/predictions/1042/outcomes"
               and event["result"] == "success" for event in stored)
    assert all(datetime.fromisoformat(event["timestamp"]).utcoffset() is not None for event in events)
    review = client.get("/api/v1/predictions/1042/reviews").json()["items"][0]
    outcome = client.get("/api/v1/predictions/1042/outcomes").json()["items"][0]
    assert review["reviewed_by"] == "dispatch-9"
    assert outcome["confirmed_by"] == "central"


def test_audit_is_read_only_and_noop_notification_does_not_create_event(client):
    assert client.patch("/api/v1/notifications/701", json={"read": True}).status_code == 200
    domain_before = audit_repository.filter(action="notification.read_state_changed", limit=None)[1]
    requests_before = audit_repository.filter(action="request.patch", limit=None)[1]
    assert client.patch("/api/v1/notifications/701", json={"read": True}).status_code == 200
    assert audit_repository.filter(action="notification.read_state_changed", limit=None)[1] == domain_before
    assert audit_repository.filter(action="request.patch", limit=None)[1] == requests_before + 1
    assert client.post("/api/v1/audit-events", json={}).status_code == 405
    assert client.patch("/api/v1/audit-events", json={}).status_code == 405


def test_request_audit_captures_reads_and_audit_journal_without_recursion(client):
    _login(client, "central", "central-demo")
    assert client.get("/api/v1/dashboard").status_code == 200
    recorded = audit_repository.filter(action="request.get", limit=None)[0]
    dashboard = next(event for event in recorded if event["path"] == "/api/v1/dashboard")
    assert dashboard["http_method"] == "GET" and dashboard["result"] == "success"
    assert dashboard["status_code"] == 200 and dashboard["request_id"]

    before = audit_repository.filter(limit=None)[1]
    assert client.get("/api/v1/audit-events").status_code == 200
    after = audit_repository.filter(limit=None)[1]
    assert after == before + 2  # ordinary request plus privileged audit-journal read
    last = audit_repository.filter(action="request.get", entity_type="audit_journal", limit=1)[0]
    assert len(last) == 1 and last[0]["path"] == "/api/v1/audit-events"
    privileged = audit_repository.filter(action="access.privileged_read", limit=None)[0]
    assert any(event["path"] == "/api/v1/audit-events" for event in privileged)


def test_request_audit_uses_only_route_declared_query_identifiers(client):
    requests = [
        ("/api/v1/objects", {"object_id": "999999"}, None),
        ("/api/v1/notifications", {"object_id": "999999"}, None),
        ("/api/v1/predictions", {"object_id": "28"}, 28),
        ("/api/v1/maintenance-requests",
         {"object_id": "28", "channel_id": "2866", "prediction_id": "1042"}, 28),
        ("/api/v1/maintenance-requests", {"channel_id": "2866"}, 28),
        ("/api/v1/maintenance-requests", {"prediction_id": "1042"}, 28),
        ("/api/v1/objects/28", {}, 28),
        ("/api/v1/channels/2866", {}, 28),
        ("/api/v1/predictions/1042", {}, 28),
    ]
    for path, params, expected_object_id in requests:
        response = client.get(path, params=params)
        assert response.status_code == 200
        events, _ = audit_repository.filter(action="request.get", limit=None)
        event = max((item for item in events if item["path"] == path), key=lambda item: item["id"])
        assert event["object_id"] == expected_object_id


def test_unknown_object_query_does_not_assign_audit_scope(client):
    login = client.post("/api/v1/auth/login", json={
        "username": "dispatch-9", "password": "dispatch-demo",
    })
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    response = client.get("/api/v1/objects", params={"object_id": "999999"})
    assert response.status_code == 200
    events, _ = audit_repository.filter(action="request.get", limit=None)
    request_event = next(item for item in events if item["path"] == "/api/v1/objects"
                         and item["user_id"] == "dispatch-9")
    assert request_event["object_id"] is None


def test_login_success_failure_and_sensitive_values_are_not_audited(client):
    password = "never-store-this-password"
    failed = client.post("/api/v1/auth/login", json={"username": "missing-user", "password": password})
    assert failed.status_code == 401
    success = client.post("/api/v1/auth/login", json={"username": "central", "password": "central-demo"})
    assert success.status_code == 200
    token = success.json()["access_token"]
    events, _ = audit_repository.filter(limit=None)
    serialized = repr(events)
    assert "login.failed" in serialized and "login.succeeded" in serialized
    requests, _ = audit_repository.filter(action="request.post", limit=None)
    login_requests = [event for event in requests if event["path"] == "/api/v1/auth/login"]
    assert {(event["result"], event["status_code"]) for event in login_requests} >= {
        ("success", 200), ("unauthorized", 401),
    }
    assert password not in serialized and "central-demo" not in serialized and token not in serialized
    assert all("authorization" not in event and "password" not in event
               for event in events)


def test_validation_failed_login_is_request_audited_without_credentials(client):
    response = client.post("/api/v1/auth/login", json={"username": "central"})
    assert response.status_code == 422
    events, _ = audit_repository.filter(action="request.post", limit=None)
    event = next(item for item in events if item["path"] == "/api/v1/auth/login"
                 and item["status_code"] == 422)
    assert event["result"] == "validation_error"
    all_events, _ = audit_repository.filter(limit=None)
    serialized = repr(all_events)
    assert "Authorization" not in serialized and "Bearer " not in serialized
    assert "password" not in serialized and "central-demo" not in serialized


def test_forbidden_requests_are_audited_and_technical_routes_are_excluded(client):
    _login(client, "tech-28", "tech-demo")
    assert client.post("/api/v1/predictions/1042/close").status_code == 403
    events, _ = audit_repository.filter(action="request.post", limit=None)
    forbidden = next(event for event in events if event["path"].endswith("/1042/close"))
    assert forbidden["result"] == "forbidden" and forbidden["status_code"] == 403

    before = audit_repository.filter(limit=None)[1]
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health/").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert audit_repository.filter(limit=None)[1] == before


def test_unauthorized_business_request_is_audited(client):
    del client.headers["Authorization"]
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 401
    events, _ = audit_repository.filter(action="request.get", limit=None)
    events = [event for event in events if event["path"] == "/api/v1/dashboard"]
    assert len(events) == 1
    assert events[0]["result"] == "unauthorized" and events[0]["status_code"] == 401
    assert events[0]["user_id"] is None and events[0]["role"] is None


def test_request_audit_failure_does_not_break_business_request(client, monkeypatch, caplog):
    import logging

    _login(client, "central", "central-demo")
    def fail(_event):
        raise RuntimeError("audit backend unavailable")
    monkeypatch.setattr(audit_repository, "add", fail)
    with caplog.at_level(logging.ERROR):
        response = client.get("/api/v1/dashboard")
    assert response.status_code == 200
    assert "Audit event recording failed" in caplog.text


def test_audit_pagination_and_scoped_visibility(client):
    for i in range(1005):
        audit_repository.add({"timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": "central", "role": "central_dispatcher",
            "action": "test.pagination", "entity_type": "object", "entity_id": str(i),
            "object_id": 9 if i % 2 else 28})
    page, total = audit_repository.filter(limit=1, offset=1001)
    assert total == 1005 and len(page) == 1
    login = client.post("/api/v1/auth/login", json={"username": "tech-28", "password": "tech-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    response = client.get("/api/v1/audit-events", params={"action": "test.pagination", "limit": 2, "offset": 1})
    assert response.status_code == 403
    login = client.post("/api/v1/auth/login", json={"username": "dispatch-9", "password": "dispatch-demo"})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    response = client.get("/api/v1/audit-events", params={"action": "test.pagination", "limit": 2, "offset": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1005
    assert all(item["object_id"] in {9, 28} for item in body["items"])
