"""Stage 8 authorization and durable audit acceptance checks."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.auth import auth_provider
from backend.app.services.postgres_access import PostgreSQLAccessRepository
from backend.app.services.postgres_audit import PostgreSQLAuditRepository
from backend.app.services.postgres_dispatcher_workflow import PostgreSQLDispatcherWorkflow
from backend.app.services.postgres_storage import PostgreSQLDatabase


def _login(client, name, password):
    response = client.post('/api/v1/auth/login', json={'username': name, 'password': password})
    assert response.status_code == 200
    client.headers['Authorization'] = f"Bearer {response.json()['access_token']}"
    return response.json()['access_token']


def test_capability_and_scope_matrix_on_api(client):
    _login(client, 'is-admin', 'admin-demo')
    assert client.get('/api/v1/audit-events').status_code == 200
    assert client.get('/api/v1/objects').status_code == 403
    assert client.get('/api/v1/signals').status_code == 403
    _login(client, 'manager-demo', 'manager-demo')
    assert client.get('/api/v1/dashboard').status_code == 200
    assert client.get('/api/v1/maintenance-requests').status_code == 200
    assert client.get('/api/v1/signals').status_code == 403
    _login(client, 'analyst-demo', 'analyst-demo')
    assert client.get('/api/v1/objects/28').status_code == 200
    assert client.get('/api/v1/maintenance-requests').status_code == 403
    assert client.get('/api/v1/signals').status_code == 403
    _login(client, 'tech-28', 'tech-demo')
    assert client.get('/api/v1/audit-events').status_code == 403
    assert client.get('/api/v1/objects/28').status_code == 200
    assert client.get('/api/v1/objects/9').status_code == 404
    _login(client, 'dual-demo', 'dual-demo')
    me = client.get('/api/v1/auth/me').json()
    assert set(me['roles']) == {'district_dispatcher', 'technician'}
    assert client.get('/api/v1/signals').status_code == 200
    assert client.get('/api/v1/objects/28').status_code == 200
    _login(client, 'dispatch-6', 'dispatch-demo-2')
    assert client.get('/api/v1/objects/20').status_code == 200
    assert client.get('/api/v1/objects/28').status_code == 404
    assert client.get('/api/v1/maintenance-requests', params={'object_id': 28}).json()['total'] == 0
    _login(client, 'tech-20', 'tech-demo-20')
    assert client.get('/api/v1/objects/20').status_code == 200
    assert client.get('/api/v1/objects/28').status_code == 404


@pytest.fixture
def database():
    url = os.getenv('TGOAT_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('TGOAT_TEST_POSTGRES_URL not configured')
    database = PostgreSQLDatabase(url)
    database.open()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def pg_client(database):
    app.state.operational_database = database
    app.state.audit_repository = PostgreSQLAuditRepository(database)
    app.state.access_repository = PostgreSQLAccessRepository(database)
    app.state.dispatcher_workflow = PostgreSQLDispatcherWorkflow(database, None)
    app.state.operational_storage = 'postgres'
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()


def test_audit_survives_new_database_instance_and_is_append_only(database):
    marker = 'stage8.' + uuid4().hex
    original = PostgreSQLAuditRepository(database).add({
        'actor_user_id': 'stage8-test', 'actor_roles': ['system_administrator'],
        'action': marker, 'resource_type': 'test', 'resource_id': marker,
    })
    second = PostgreSQLDatabase(os.environ['TGOAT_TEST_POSTGRES_URL'])
    second.open()
    try:
        second.migrate()
        rows, total = PostgreSQLAuditRepository(second).filter(action=marker)
        assert total == 1 and rows[0]['id'] == original['id']
        with pytest.raises(Exception, match='append-only'):
            with second.unit_of_work() as uow:
                uow.connection.execute('UPDATE audit_events SET action=%s WHERE id=%s',
                                       ('tampered', original['id']))
        with pytest.raises(Exception, match='append-only'):
            with second.unit_of_work() as uow:
                uow.connection.execute('TRUNCATE audit_events')
    finally:
        second.close()


def test_admin_grant_change_reaches_existing_session_and_logout_revokes(pg_client, database):
    _login(pg_client, 'tech-28', 'tech-demo')
    tech_token = pg_client.headers['Authorization']
    assert pg_client.get('/api/v1/signals').status_code == 403
    _login(pg_client, 'is-admin', 'admin-demo')
    admin_token = pg_client.headers['Authorization']
    before = next(row for row in pg_client.get('/api/v1/access/principals').json()
                  if row['user_id'] == 'tech-28')
    result = pg_client.put('/api/v1/access/principals/tech-28', json={
        'roles': ['district_dispatcher'], 'grants': [], 'groups': ['district:9'],
        'expected_version': before['version'],
    })
    assert result.status_code == 200, result.text
    stale = pg_client.put('/api/v1/access/principals/tech-28', json={
        'roles': ['technician'], 'grants': [], 'groups': ['complex:28'],
        'expected_version': before['version'],
    })
    assert stale.status_code == 409
    pg_client.headers['Authorization'] = tech_token
    assert pg_client.get('/api/v1/signals').status_code == 200
    assert pg_client.get('/api/v1/auth/me').json()['roles'] == ['district_dispatcher']
    assert pg_client.post('/api/v1/auth/logout').status_code == 204
    assert pg_client.get('/api/v1/auth/me').status_code == 401
    pg_client.headers['Authorization'] = admin_token
    restored = pg_client.put('/api/v1/access/principals/tech-28', json={
        'roles': ['technician'], 'grants': [], 'groups': ['complex:28'],
        'expected_version': result.json()['version'],
    })
    assert restored.status_code == 200, restored.text
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM audit_events WHERE action='access.assignment.changed'").fetchone()[0] >= 2


def test_failed_durable_audit_rolls_back_request_creation(pg_client, database, monkeypatch):
    from backend.app.services.postgres_audit import PostgreSQLAuditRepository

    _login(pg_client, 'central', 'central-demo')
    marker = 'stage8-rollback-' + uuid4().hex
    original = PostgreSQLAuditRepository.append_in_connection

    def fail_domain_event(connection, event):
        if event.get('action') == 'maintenance_request.created':
            raise RuntimeError('injected audit write failure')
        return original(connection, event)

    monkeypatch.setattr(PostgreSQLAuditRepository, 'append_in_connection',
                        staticmethod(fail_domain_event))
    response = pg_client.post('/api/v1/maintenance-requests', json={
        'object_id': 28, 'reason': marker, 'priority': 'normal',
    })
    assert response.status_code >= 500
    with database.connection() as connection:
        assert connection.execute('SELECT count(*) FROM maintenance_requests WHERE reason=%s',
                                  (marker,)).fetchone()[0] == 0
