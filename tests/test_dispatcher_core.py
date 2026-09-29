from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from backend.app.api.routes.signals import SignalDecision
from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow, WorkflowConflict, WorkflowNotFound
from backend.app.services.prediction_history import SQLitePredictionHistoryRepository
from tests.test_prediction_history import BASE, prediction


def setup(path=':memory:'):
    history = SQLitePredictionHistoryRepository(path)
    record, card, _ = history.process_operational(prediction(BASE))
    return history, SQLiteDispatcherWorkflow(history), record, card


def command(record, **changes):
    return dict(expected_version=0, idempotency_key='command-1', decision='inspection_requested',
                prediction_identity=record.identity, reason_code=None, comment='Проверить',
                request={'reason': 'Проверка сигнала', 'priority': 'normal'}, **changes)


def decide(repo, card, payload, **kwargs):
    return repo.decide(card.card_id, actor_user_id='a', actor_role='central_dispatcher', payload=payload, **kwargs)


def test_decision_reason_minimum_is_enforced_by_api_and_workflow():
    history, repo, record, card = setup()
    inspection = {**command(record), 'request': {'reason': 'плохо', 'priority': 'normal'}}
    false_alarm = {**command(record), 'decision': 'false_alarm', 'request': None, 'comment': '   '}
    for payload in (inspection, false_alarm):
        with pytest.raises(ValidationError):
            SignalDecision.model_validate(payload)
        with pytest.raises(WorkflowConflict, match='at least 8 characters'):
            decide(repo, card, payload)

    for table in ('signal_decisions', 'signal_work', 'maintenance_requests', 'signal_command_receipts'):
        assert history._connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    history.close()


def test_episode_context_never_uses_later_card_and_decision_is_exact():
    history = SQLitePredictionHistoryRepository()
    repo = SQLiteDispatcherWorkflow(history)
    a, card_a, _ = history.process_operational(prediction(BASE, object_id=28, probability=.63))
    history.process_operational(prediction(BASE+timedelta(hours=18), object_id=28, positive=False, probability=.1))
    b, card_b, _ = history.process_operational(prediction(BASE+timedelta(hours=24), object_id=28, probability=.95))
    assert card_a.card_id != card_b.card_id
    old, new = repo.signal(card_a.card_id), repo.signal(card_b.card_id)
    assert (old['risk_probability'], old['prediction_identity'], old['last_model_update']) == (.63, a.identity, a.prediction.snapshot_ts.isoformat())
    assert (new['risk_probability'], new['prediction_identity']) == (.95, b.identity)
    assert old['latest_context']['identity'] == a.identity
    assert old['object_current']['risk_probability'] == new['object_current']['risk_probability'] == .95
    result = decide(repo, card_a, command(a))
    persisted = history._connection.execute('SELECT prediction_identity FROM signal_decisions WHERE id=?', (result['decision_id'],)).fetchone()
    assert persisted[0] == a.identity
    history.close()


def test_same_episode_updates_model_context_without_human_version_reset():
    history = SQLitePredictionHistoryRepository()
    repo = SQLiteDispatcherWorkflow(history)
    a, card, _ = history.process_operational(prediction(BASE, probability=.63))
    second, same, _ = history.process_operational(prediction(BASE+timedelta(hours=6), probability=.71))
    assert same.card_id == card.card_id and repo.signals()['total'] == 1
    assert repo.signal(card.card_id)['risk_probability'] == .71
    assert repo.signal(card.card_id)['work_version'] == 0
    result = decide(repo, card, {**command(second), 'decision': 'monitoring', 'request': None})
    history.process_operational(prediction(BASE+timedelta(hours=12), probability=.73))
    current = repo.signal(card.card_id)
    assert current['risk_probability'] == .73
    assert current['state'] == 'monitoring' and current['work_version'] == 1
    assert current['last_decision'] == result['signal']['last_decision']
    history.process_operational(prediction(BASE+timedelta(hours=18), positive=False, probability=.1))
    assert repo.signal(card.card_id)['risk_probability'] == .73
    assert repo.signal(card.card_id)['object_current']['risk_probability'] == .1
    history.close()


def test_legacy_receipt_projection_is_normalized_without_replaying_business_writes():
    import json
    history, repo, record, card = setup()
    result = decide(repo, card, command(record))
    old = json.loads(json.dumps(result))
    latest = old['signal'].pop('object_current')['context']
    latest = {**latest, 'identity':'later-object-prediction', 'risk_probability':.95}
    old['signal'].update(latest_context=latest, risk_probability=.95)
    with history._connection:
        history._connection.execute('UPDATE signal_command_receipts SET result_json=?', (json.dumps(old),))
    repeated = decide(repo, card, command(record))
    assert repeated['signal']['risk_probability'] == record.prediction.risk_probability
    assert repeated['signal']['prediction_identity'] == record.identity
    assert repeated['signal']['object_current']['risk_probability'] == .95
    assert repeated['request'] == result['request'] and repeated['work_version'] == result['work_version']
    assert repo.filter()[1] == 1
    assert history._connection.execute('SELECT COUNT(*) FROM signal_decisions').fetchone()[0] == 1
    history.close()


def test_atomic_idempotent_restart(tmp_path):
    path = tmp_path / 'operational.sqlite'
    history, repo, record, card = setup(path)
    result = decide(repo, card, command(record))
    assert result['request']['status']=='new'
    assert result['signal']['state']=='inspection_requested'
    history.close()
    history = SQLitePredictionHistoryRepository(path)
    repo = SQLiteDispatcherWorkflow(history)
    assert repo.signal(card.card_id)==result['signal']
    assert decide(repo, card, command(record))==result
    assert repo.filter()[1]==1
    assert repo.get(result['request']['id'])['decision_id']==result['decision_id']
    history.close()


def test_failure_rolls_back_every_business_write():
    history, repo, record, card = setup()
    def fail():
        raise RuntimeError('injected before request')
    with pytest.raises(RuntimeError):
        decide(repo, card, command(record), after_decision=fail)
    for table in ('signal_decisions', 'signal_work', 'maintenance_requests', 'signal_command_receipts'):
        assert history._connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
    assert repo.signal(card.card_id)['state']=='new'
    result = decide(repo, card, command(record))
    assert result['work_version']==1 and repo.filter()[1]==1


def test_inspection_request_domain_audit_is_idempotent_in_sqlite():
    history, repo, record, card = setup()
    payload = command(record)
    first = decide(repo, card, payload)
    repeated = decide(repo, card, payload)

    events, total = repo.domain_audit_repository.filter(
        action='maintenance_request.created', limit=None,
    )
    assert total == 1 and len(events) == 1
    event = events[0]
    assert event['user_id'] == 'a'
    assert event['entity_type'] == 'maintenance_request'
    assert event['entity_id'] == str(first['request']['id'])
    assert event['object_id'] == record.prediction.object_id
    assert event['timestamp'] == first['request']['created_at']
    assert repeated['request']['id'] == first['request']['id']
    assert history._connection.execute('SELECT COUNT(*) FROM maintenance_requests').fetchone()[0] == 1
    assert history._connection.execute('SELECT COUNT(*) FROM domain_audit_events').fetchone()[0] == 1
    history.close()


def test_sqlite_domain_audit_failure_rolls_back_signal_request(monkeypatch):
    from backend.app.services.sqlite_domain_audit import SQLiteDomainAuditRepository

    history, repo, record, card = setup()
    append = SQLiteDomainAuditRepository.append_in_connection

    def append_then_fail(connection, event):
        append(connection, event)
        raise RuntimeError('injected domain audit failure')

    monkeypatch.setattr(SQLiteDomainAuditRepository, 'append_in_connection',
                        staticmethod(append_then_fail))
    with pytest.raises(RuntimeError, match='injected domain audit failure'):
        decide(repo, card, command(record))

    for table in ('signal_decisions', 'signal_work', 'maintenance_requests',
                  'maintenance_events', 'signal_command_receipts', 'domain_audit_events'):
        assert history._connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert repo.signal(card.card_id)['state'] == 'new'
    history.close()


def test_database_unique_guard_protects_linked_request():
    import sqlite3
    history, repo, record, card = setup()
    result = decide(repo, card, command(record))
    with pytest.raises(sqlite3.IntegrityError), history._connection:
        history._connection.execute('''INSERT INTO maintenance_requests
            (object_id,card_id,prediction_identity,decision_id,created_by_user_id,created_by_role,
             reason,priority,status,origin,created_at,updated_at)
            SELECT object_id,card_id,prediction_identity,decision_id,created_by_user_id,created_by_role,
             reason,priority,status,origin,created_at,updated_at FROM maintenance_requests WHERE id=?''',
            (result['request']['id'],))
    assert repo.filter()[1]==1
    assert repo.signal(card.card_id)['work_version']==1


def test_version_key_membership_and_foreign_scope():
    history, repo, record, card = setup()
    with pytest.raises(WorkflowNotFound):
        decide(repo, card, command(record), allowed_object_ids=[])
    with pytest.raises(WorkflowConflict):
        decide(repo, card, {**command(record), 'prediction_identity': 'foreign'})
    result = decide(repo, card, {**command(record), 'decision': 'monitoring'})
    assert result['signal']['state']=='monitoring'
    with pytest.raises(WorkflowConflict):
        decide(repo, card, command(record))
    with pytest.raises(WorkflowConflict):
        decide(repo, card, {**command(record), 'idempotency_key': 'b'})
    assert history._connection.execute('SELECT COUNT(*) FROM signal_decisions').fetchone()[0]==1


def test_same_card_context_closed_resolved_and_future_episode():
    history, repo, record, card = setup()
    decide(repo, card, {**command(record), 'decision': 'monitoring'})
    history.process_operational(prediction(BASE+timedelta(hours=6)))
    assert repo.signals()['total']==1
    assert repo.signal(card.card_id)['work_version']==1
    history.process_operational(prediction(BASE+timedelta(hours=18), positive=False))
    assert repo.signal(card.card_id)['model_episode_status']=='closed'
    assert repo.signal(card.card_id)['state']=='monitoring'
    decide(repo, card, {**command(record), 'idempotency_key':'resolve', 'expected_version':1, 'decision':'false_alarm'})
    history.process_operational(prediction(BASE+timedelta(hours=24)))
    assert repo.signals()['total']==2
    assert repo.signal(card.card_id)['state']=='resolved'


def test_manual_persistent_and_linked_patch_protection(tmp_path):
    history, repo, record, card = setup(tmp_path/'db.sqlite')
    manual = repo.create(dict(object_id=1, reason='Осмотр', priority='normal',
                             created_by_user_id='a',created_by_role='central_dispatcher',
                             created_at=BASE.isoformat(),updated_at=BASE.isoformat()))
    assert manual['status']=='draft' and manual['card_id'] is None
    linked = decide(repo, card, command(record))['request']
    with pytest.raises(WorkflowConflict):
        repo.update(linked['id'], {'status':'closed', 'updated_at':BASE.isoformat()})
    history.close()
    history = SQLitePredictionHistoryRepository(tmp_path/'db.sqlite')
    assert SQLiteDispatcherWorkflow(history).get(manual['id'])==manual
    history.close()


def test_membership_upgrade_deterministic_and_duplicate_old_card(tmp_path):
    history, repo, record, card = setup(tmp_path/'db.sqlite')
    history.process_operational(prediction(BASE+timedelta(hours=24)))
    assert history.process_operational(prediction(BASE))[1].card_id==card.card_id
    with history._connection:
        history._connection.execute('DELETE FROM forecast_card_predictions')
    history.close()
    history = SQLitePredictionHistoryRepository(tmp_path/'db.sqlite')
    assert SQLiteDispatcherWorkflow(history).signal(card.card_id)['prediction_identity']==record.identity
    history.close()


def test_two_connections_no_last_write_wins(tmp_path):
    path = tmp_path/'db.sqlite'
    history, repo, record, card = setup(path)
    second = SQLitePredictionHistoryRepository(path)
    another = SQLiteDispatcherWorkflow(second)
    def write(pair):
        target, key = pair
        try:
            decide(target, card, {**command(record), 'decision':'monitoring','idempotency_key':key})
            return 'ok'
        except WorkflowConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write, [(repo,'a'),(another,'b')]))==['conflict','ok']
    assert repo.signal(card.card_id)['work_version']==1
    second.close()
    history.close()


def test_scoped_api_legacy_guard_and_read_only_projection(tmp_path, monkeypatch, processed_dir):
    from fastapi.testclient import TestClient
    from urllib.parse import quote
    from backend.app.main import app
    from backend.app.services.auth import auth_provider, accessible_object_ids
    monkeypatch.setenv('TGOAT_PREDICTION_HISTORY_DB', str(tmp_path/'operational.sqlite'))
    with TestClient(app) as client:
        central = auth_provider.authenticate('central','central-demo')
        district = auth_provider.authenticate('dispatch-9','dispatch-demo')
        allowed = accessible_object_ids(district)
        foreign = next(i for i in (5343,4370,111,20) if i not in allowed)
        history = app.state.operational_forecast_service.repository
        rec, card, _ = history.process_operational(prediction(BASE, object_id=foreign))
        path = '/api/v1/signals/' + quote(card.card_id, safe='')
        client.headers['Authorization'] = 'Bearer ' + auth_provider.issue_token(central)
        before = history._connection.total_changes
        assert client.get(path).json()['state']=='new'
        assert client.get('/api/v1/signals').json()['total']==1
        assert history._connection.total_changes==before
        legacy = '/api/v1/operational/predictions/'+quote(rec.identity,safe='')+'/review'
        for action in ('inspection_requested','monitoring','false_alarm'):
            assert client.post(legacy, json={'decision':action}).status_code==409
        result = client.post(path+'/decision', json=command(rec))
        assert result.status_code==200, result.text
        request_id = result.json()['request']['id']
        client.headers['Authorization'] = 'Bearer ' + auth_provider.issue_token(district)
        before = history._connection.total_changes
        assert client.get(path).status_code==404
        assert client.post(path+'/decision',json=command(rec)).status_code==404
        assert client.get(f'/api/v1/maintenance-requests/{request_id}').status_code==404
        assert client.get('/api/v1/signals').json()['total']==0
        dashboard = client.get('/api/v1/signals/monitoring').json()
        assert not dashboard['items'] and all(item['id'] in allowed for item in dashboard['objects'])
        assert history._connection.total_changes==before


def test_two_process_http_restart_receipt(tmp_path):
    import json
    import os
    import subprocess
    import sys
    script = '''
import json,sys
from urllib.parse import quote
from fastapi.testclient import TestClient
from backend.app.main import app
from tests.test_prediction_history import prediction,BASE
from backend.app.services.auth import auth_provider
from tests.test_dispatcher_core import command
with TestClient(app) as client:
    login=client.post('/api/v1/auth/login',json={'username':'central','password':'central-demo'})
    client.headers['Authorization']='Bearer '+login.json()['access_token']
    repo=app.state.operational_forecast_service.repository
    rec,card,_=repo.process_operational(prediction(BASE,object_id=28))
    path='/api/v1/signals/'+quote(card.card_id,safe='')
    response=client.post(path+'/decision',json=command(rec))
    assert response.status_code==200,response.text
    result=response.json()
    assert client.get(path).json()==result['signal']
    assert client.get('/api/v1/maintenance-requests/'+str(result['request']['id'])).json()['card_id']==card.card_id
    assert client.get('/api/v1/maintenance-requests').json()['total']==1
    print(json.dumps(result,sort_keys=True))
'''
    env = {**os.environ, 'TGOAT_RUNTIME_MODE':'normal', 'TGOAT_PREDICTION_HISTORY_DB':str(tmp_path/'normal.sqlite')}
    results = [subprocess.run([sys.executable,'-c',script],env=env,text=True,capture_output=True,check=True,timeout=60) for _ in range(2)]
    assert json.loads(results[0].stdout)==json.loads(results[1].stdout)
