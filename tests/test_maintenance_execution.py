from datetime import timedelta
import sqlite3

import pytest

from backend.app.services.auth import AuthUser
from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow, WorkflowConflict, WorkflowNotFound
from backend.app.services.prediction_history import SQLitePredictionHistoryRepository
from tests.test_dispatcher_core import command, decide
from tests.test_prediction_history import BASE, prediction

CENTRAL = AuthUser('central','central','central_dispatcher',None)
TECH = AuthUser('tech-28','tech-28','technician',(28,))
OTHER = AuthUser('other','other','technician',(28,))


@pytest.mark.parametrize('status',['new','assigned','in_progress','awaiting_review'])
def test_unique_active_index_matches_projection(status):
    history,repo,card,item=linked()
    conn=history._connection
    with conn:
        conn.execute('UPDATE maintenance_requests SET status=?,assignee_user_id=? WHERE id=?',(status,TECH.user_id,item['id']))
    assert repo.signal(card.card_id)['state']=='inspection_requested'
    with pytest.raises(sqlite3.IntegrityError,match='maintenance_requests.card_id'),conn:
        decision=conn.execute('''INSERT INTO signal_decisions
            (card_id,object_id,prediction_identity,actor_user_id,actor_role,decision,created_at,work_version,command_key)
            SELECT card_id,object_id,prediction_identity,actor_user_id,actor_role,decision,created_at,work_version+1,'second'
            FROM signal_decisions WHERE id=?''',(item['decision_id'],)).lastrowid
        conn.execute('''INSERT INTO maintenance_requests
            (object_id,card_id,prediction_identity,decision_id,created_by_user_id,created_by_role,reason,priority,status,origin,created_at,updated_at)
            SELECT object_id,card_id,prediction_identity,?,created_by_user_id,created_by_role,reason,priority,'new',origin,created_at,updated_at
            FROM maintenance_requests WHERE id=?''',(decision,item['id']))
    assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    history.close()


def test_two_connections_assign_race_and_submit_cancel_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    history,repo,card,item=linked(tmp_path/'race.sqlite')
    other_history=SQLitePredictionHistoryRepository(tmp_path/'race.sqlite')
    other=SQLiteDispatcherWorkflow(other_history)
    def attempt(target,action,user,key,**fields):
        try:return act(target,item,action,user,key=key,**fields)['status']
        except WorkflowConflict:return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(attempt,repo,'assign',CENTRAL,'a',assignee_user_id=TECH.user_id)
        b=pool.submit(attempt,other,'assign',CENTRAL,'b',assignee_user_id=OTHER.user_id)
        assert sorted([a.result(),b.result()])==['assigned','conflict']
    item=repo.get(item['id'])
    technician=TECH if item['assignee_user_id']==TECH.user_id else OTHER
    item=act(repo,item,'start',technician)
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(attempt,repo,'result',technician,'result',result_code='issue_found',summary='Проверка')
        b=pool.submit(attempt,other,'cancel',CENTRAL,'cancel',comment='Отмена')
        states=[a.result(),b.result()]
        assert states.count('conflict')==1
        assert ('awaiting_review' in states) != ('cancelled' in states)
    history.close();other_history.close()


def test_private_drafts_and_submitted_retry_receipt():
    history,repo,card,item=linked()
    draft=repo.create(dict(object_id=28,reason='Черновик',priority='normal',created_by_user_id=TECH.user_id,created_by_role=TECH.role,created_at=BASE.isoformat(),updated_at=BASE.isoformat()))
    assert repo.request_visible(draft,TECH) and not repo.request_visible(draft,CENTRAL)
    assert repo.filter(viewer=TECH,object_ids={28})[1]==1
    result=act(repo,draft,'submit',TECH)
    assert result['status']=='new' and not repo.request_visible(result,TECH)
    assert act(repo,draft,'submit',TECH)==result
    with pytest.raises(WorkflowNotFound):repo.request_detail(draft['id'],TECH)
    assert repo.request_visible(result,CENTRAL)
    history.close()


def linked(path=':memory:'):
    history = SQLitePredictionHistoryRepository(path)
    record, card, _ = history.process_operational(prediction(BASE,object_id=28,probability=.63))
    repo = SQLiteDispatcherWorkflow(history)
    item = decide(repo,card,command(record))['request']
    return history,repo,card,item


def act(repo,item,action,user=CENTRAL,key=None,**fields):
    return repo.request_command(item['id'],action,user,
        dict(expected_version=item['version'],idempotency_key=key or action,**fields),directory=(TECH,OTHER,CENTRAL))


def test_technician_task_projection_includes_assignment_and_return_events():
    history,repo,card,item=linked()
    item=act(repo,item,'assign',assignee_user_id=TECH.user_id)
    listed,total=repo.filter(viewer=TECH,object_ids={28},active_only=True)
    assert total==1 and listed[0]['latest_event']=='assigned'
    item=act(repo,item,'start',TECH)
    item=act(repo,item,'result',TECH,result_code='issue_found',summary='Датчик не отвечает')
    item=act(repo,item,'return',CENTRAL,comment='Повторить проверку с доступом')
    listed,total=repo.filter(viewer=TECH,object_ids={28},active_only=True)
    assert total==1 and listed[0]['status']=='in_progress'
    assert listed[0]['latest_event']=='returned'
    history.close()


def test_happy_path_restart_and_exact_basis(tmp_path):
    path=tmp_path/'operational.sqlite'
    history,repo,card,item=linked(path)
    assert repo.signal(card.card_id)['state']=='inspection_requested'
    item=act(repo,item,'assign',assignee_user_id=TECH.user_id)
    assert repo.signal(card.card_id)['state']=='inspection_requested'
    item=act(repo,item,'start',TECH)
    assert repo.signal(card.card_id)['state']=='inspection_requested'
    old=item
    item=act(repo,item,'result',TECH,result_code='no_issue_found',summary='Проверено на месте')
    assert item['status']=='awaiting_review' and item['latest_result']['revision']==1
    assert repo.signal(card.card_id)['state']=='inspection_requested'
    history.close()
    history=SQLitePredictionHistoryRepository(path)
    repo=SQLiteDispatcherWorkflow(history)
    assert repo.request_detail(item['id'])==item
    assert act(repo,old,'result',TECH,result_code='no_issue_found',summary='Проверено на месте')==item
    history.process_operational(prediction(BASE+timedelta(hours=6),object_id=28,probability=.95))
    assert repo.request_detail(item['id'])['historical_basis']['risk_probability']==.63
    assert repo.request_detail(item['id'])['object_current']['risk_probability']==.95
    awaiting=item
    item=act(repo,item,'accept',comment='Результат принят')
    assert item['status']=='closed' and repo.signal(card.card_id)['state']=='resolved'
    assert repo.signal(card.card_id)['work_version']==2
    history.close()
    history=SQLitePredictionHistoryRepository(path)
    repo=SQLiteDispatcherWorkflow(history)
    assert act(repo,awaiting,'accept',comment='Результат принят')==item
    assert len(repo.request_detail(item['id'])['results'])==1
    assert [e['event'] for e in item['timeline']]==['created','assigned','started','result_submitted','accepted']
    history.close()


@pytest.mark.parametrize(('result_code','expected_signal_state'),[
    ('no_issue_found','resolved'),
    ('issue_found','new'),
    ('needs_followup','new'),
    ('unable_to_verify','new'),
], ids=['clear','issue-remains','follow-up-needed','unable-to-verify'])
def test_accept_closes_linked_request_but_only_clear_result_resolves_vita_signal(result_code,expected_signal_state):
    history,repo,card,item=linked()
    assert card.object_id==28
    item=act(repo,item,'assign',assignee_user_id=TECH.user_id)
    item=act(repo,item,'start',TECH)
    item=act(repo,item,'result',TECH,result_code=result_code,summary='Результат осмотра')
    awaiting=item

    accepted=act(repo,awaiting,'accept',key='accept-once',comment='Отчёт рассмотрен')
    repeated=act(repo,awaiting,'accept',key='accept-once',comment='Отчёт рассмотрен')

    assert repeated==accepted
    assert accepted['status']=='closed'
    assert accepted['latest_result']['result_code']==result_code
    assert accepted['accepted_by_user_id']==CENTRAL.user_id
    assert sum(event['event']=='accepted' for event in accepted['timeline'])==1
    assert repo.signal(card.card_id)['state']==expected_signal_state
    visible=repo.signals(object_id=28,state=expected_signal_state)
    assert any(signal['card_id']==card.card_id for signal in visible['items'])
    history.close()


def test_return_resubmit_revision_and_cancel_disposition():
    history,repo,card,item=linked()
    item=act(repo,item,'assign',assignee_user_id=TECH.user_id)
    item=act(repo,item,'start',TECH)
    item=act(repo,item,'result',TECH,result_code='unable_to_verify',summary='Нужен доступ')
    item=act(repo,item,'return',comment='Повторить с доступом')
    assert item['assignee_user_id']==TECH.user_id and item['status']=='in_progress'
    item=act(repo,item,'result',TECH,key='revision-2',result_code='issue_found',summary='Проверка завершена')
    assert [r['revision'] for r in item['results']]==[1,2]
    act(repo,item,'accept')
    assert repo.signal(card.card_id)['state']=='new'
    history.close()
    history,repo,card,item=linked()
    item=act(repo,item,'cancel',comment='Проверка отложена')
    assert item['status']=='cancelled' and repo.signal(card.card_id)['state']=='new'
    assert repo.signal(card.card_id)['work_version']==2
    history.close()


def test_roles_visibility_conflict_and_rollback():
    history,repo,card,item=linked()
    assert not repo.request_visible(item,TECH)
    with pytest.raises(WorkflowNotFound): act(repo,item,'start',TECH)
    with pytest.raises(WorkflowConflict): act(repo,item,'assign',assignee_user_id=CENTRAL.user_id)
    outside=AuthUser('outside','outside','technician',(999999,))
    with pytest.raises(WorkflowConflict):
        repo.request_command(item['id'],'assign',CENTRAL,dict(expected_version=1,idempotency_key='bad',assignee_user_id='outside'),directory=(outside,))
    assigned=act(repo,item,'assign',assignee_user_id=TECH.user_id)
    with pytest.raises(WorkflowConflict): act(repo,item,'assign',key='stale',assignee_user_id=OTHER.user_id)
    with pytest.raises(WorkflowNotFound): repo.request_detail(item['id'],OTHER)
    with pytest.raises(WorkflowConflict): act(repo,assigned,'start',CENTRAL)
    progress=act(repo,assigned,'start',TECH)
    with pytest.raises(WorkflowConflict): act(repo,progress,'result',CENTRAL,result_code='issue_found',summary='Подмена')
    def fail(): raise RuntimeError('rollback')
    before=repo.request_detail(item['id'])
    with pytest.raises(RuntimeError):
        repo.request_command(item['id'],'result',TECH,dict(expected_version=progress['version'],idempotency_key='rollback',result_code='issue_found',summary='Тест'),after_write=fail)
    assert repo.request_detail(item['id'])==before
    assert history._connection.execute("SELECT COUNT(*) FROM maintenance_command_receipts WHERE idempotency_key='rollback'").fetchone()[0]==0
    history.close()


def test_stage2_schema_migration_preserves_linked_manual_receipts(tmp_path):
    # Build the actual accepted Stage 2 schema, not a fresh Stage 3 approximation.
    history=SQLitePredictionHistoryRepository(tmp_path/'migration.sqlite')
    record,card,_=history.process_operational(prediction(BASE,object_id=28))
    old=SQLiteDispatcherWorkflow(history)
    original=decide(old,card,command(record))
    manual=old.create(dict(object_id=28,reason='Черновик',priority='normal',created_by_user_id='central',created_by_role='central_dispatcher',created_at=BASE.isoformat(),updated_at=BASE.isoformat()))
    counts={table:history._connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('maintenance_requests','signal_decisions','signal_command_receipts','forecast_cards','forecast_card_predictions','signal_work')}
    conn=history._connection
    conn.execute('PRAGMA foreign_keys=OFF')
    with conn:
        conn.execute('''CREATE TABLE maintenance_stage2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,object_id INTEGER NOT NULL,
            card_id TEXT REFERENCES forecast_cards(card_id), prediction_identity TEXT REFERENCES prediction_history(identity),
            decision_id INTEGER UNIQUE REFERENCES signal_decisions(id), created_by_user_id TEXT NOT NULL,
            created_by_role TEXT NOT NULL, reason TEXT NOT NULL,comment TEXT,priority TEXT NOT NULL,status TEXT NOT NULL,
            origin TEXT NOT NULL CHECK(origin IN ('manual','signal')),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,command_key TEXT,channel_id INTEGER,prediction_id INTEGER,
            CHECK(origin='manual' OR (card_id IS NOT NULL AND prediction_identity IS NOT NULL AND decision_id IS NOT NULL AND status='new')))''')
        columns=','.join(row[1] for row in conn.execute('PRAGMA table_info(maintenance_stage2)'))
        conn.execute(f'INSERT INTO maintenance_stage2 ({columns}) SELECT {columns} FROM maintenance_requests')
        for table in ('maintenance_command_receipts','maintenance_events','maintenance_results','signal_task_dispositions'):
            conn.execute(f'DROP TABLE {table}')
        conn.execute('DROP TABLE maintenance_requests')
        conn.execute('ALTER TABLE maintenance_stage2 RENAME TO maintenance_requests')
        conn.execute("CREATE UNIQUE INDEX one_active_inspection_per_card ON maintenance_requests(card_id) WHERE origin='signal' AND status='new'")
    conn.execute('PRAGMA foreign_keys=ON')
    repo=SQLiteDispatcherWorkflow(history)
    assert repo.get(original['request']['id'])['prediction_identity']==record.identity
    assert repo.get(manual['id'])['status']=='draft'
    assert all(history._connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==count for table,count in counts.items())
    act(repo,repo.get(original['request']['id']),'assign',assignee_user_id=TECH.user_id)
    assert history._connection.execute('PRAGMA foreign_key_check').fetchall()==[]
    history.close()


def test_three_process_http_restart_preserves_execution(tmp_path):
    import json
    import os
    import subprocess
    import sys
    script=r'''
import json, os
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.services.prediction_history import SQLitePredictionHistoryRepository
from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow
from tests.test_prediction_history import BASE,prediction
phase=int(os.environ['TEST_PHASE'])
if phase==0:
    h=SQLitePredictionHistoryRepository(os.environ['TGOAT_PREDICTION_HISTORY_DB'])
    p,c,_=h.process_operational(prediction(BASE,object_id=28,probability=.63))
    r=SQLiteDispatcherWorkflow(h)
    r.decide(c.card_id,actor_user_id='central',actor_role='central_dispatcher',payload=dict(expected_version=0,idempotency_key='inspection',decision='inspection_requested',prediction_identity=p.identity,request=dict(reason='Проверка',priority='normal')))
    h.close()
with TestClient(app) as client:
    def login(name,password):
        client.headers['Authorization']='Bearer '+client.post('/api/v1/auth/login',json=dict(username=name,password=password)).json()['access_token']
    def command(action,version,key,**fields):
        response=client.post('/api/v1/maintenance-requests/1/'+action,json=dict(expected_version=version,idempotency_key=key,**fields))
        assert response.status_code==200,response.text
        return response.json()
    login('central','central-demo')
    if phase==0:
        command('assign',1,'assign',assignee_user_id='tech-28')
        login('tech-28','tech-demo')
        command('start',2,'start')
        result=command('result',3,'result',result_code='no_issue_found',summary='Проверено')
    elif phase==1:
        result=client.get('/api/v1/maintenance-requests/1').json()
        assert result['status']=='awaiting_review' and result['assignee_user_id']=='tech-28'
        assert len(result['results'])==1 and len(result['timeline'])==4
        login('tech-28','tech-demo')
        assert command('result',3,'result',result_code='no_issue_found',summary='Проверено')==result
        login('central','central-demo')
        result=command('accept',4,'accept',comment='Принято')
    else:
        result=client.get('/api/v1/maintenance-requests/1').json()
        assert result['status']=='closed' and len(result['results'])==1 and len(result['timeline'])==5
        assert command('accept',4,'accept',comment='Принято')==result
        assert app.state.dispatcher_workflow.signal(result['card_id'])['state']=='resolved'
    print(json.dumps(result))
'''
    environment={**os.environ,'TGOAT_RUNTIME_MODE':'normal','TGOAT_PREDICTION_HISTORY_DB':str(tmp_path/'process.sqlite')}
    results=[]
    for phase in range(3):
        output=subprocess.check_output([sys.executable,'-c',script],env={**environment,'TEST_PHASE':str(phase)},text=True)
        results.append(json.loads(output.strip().splitlines()[-1]))
    assert results[0]['id']==results[1]['id']==results[2]['id']
    assert results[1]==results[2]


def test_http_task_visibility_directory_and_denied_writes(client):
    from backend.app.main import app
    from backend.app.services.auth import auth_provider
    repo=app.state.dispatcher_workflow
    record,card,_=repo.history.process_operational(prediction(BASE,object_id=28))
    request=repo.decide(card.card_id,actor_user_id='central',actor_role='central_dispatcher',payload=dict(expected_version=0,idempotency_key='http-inspection',decision='inspection_requested',prediction_identity=record.identity,request=dict(reason='Проверка',priority='normal')))['request']
    def login(username,password):
        response=client.post('/api/v1/auth/login',json=dict(username=username,password=password))
        client.headers['Authorization']='Bearer '+response.json()['access_token']
    eligible=client.get('/api/v1/maintenance-requests/eligible-technicians',params={'object_id':28}).json()
    assert eligible and all(set(u)=={'user_id','username','role'} and u['role']=='technician' for u in eligible)
    response=client.post(f"/api/v1/maintenance-requests/{request['id']}/assign",json=dict(expected_version=1,idempotency_key='assign',assignee_user_id='tech-28'))
    assert response.status_code==200
    assigned=response.json()
    login('tech-9','tech-demo-2')
    assert client.get(f"/api/v1/maintenance-requests/{request['id']}").status_code==404
    assert all(row['id']!=request['id'] for row in client.get('/api/v1/maintenance-requests').json()['items'])
    before={table:repo.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('maintenance_events','maintenance_results','maintenance_command_receipts')}
    assert client.post(f"/api/v1/maintenance-requests/{request['id']}/start",json=dict(expected_version=2,idempotency_key='foreign')).status_code==404
    assert before=={table:repo.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in before}
    login('tech-28','tech-demo')
    assert client.get(f"/api/v1/maintenance-requests/{request['id']}").status_code==200
    assert client.post(f"/api/v1/maintenance-requests/{request['id']}/accept",json=dict(expected_version=2,idempotency_key='denied')).status_code==403
    assert client.get('/api/v1/maintenance-requests/eligible-technicians',params={'object_id':28}).status_code==403
    login('central','central-demo')
    assert client.patch(f"/api/v1/maintenance-requests/{request['id']}",json=dict(status='closed')).status_code==409


def test_list_object_names_preserve_scope_count_pagination(client):
    from backend.app.main import app
    from backend.app.services.reference_data import get_objects_by_id
    repo=app.state.dispatcher_workflow
    record,card,_=repo.history.process_operational(prediction(BASE,object_id=28))
    item=repo.decide(card.card_id,actor_user_id='central',actor_role='central_dispatcher',payload=dict(expected_version=0,idempotency_key='named-inspection',decision='inspection_requested',prediction_identity=record.identity,request=dict(reason='Проверка',priority='normal')))['request']
    assigned=client.post(f"/api/v1/maintenance-requests/{item['id']}/assign",json=dict(expected_version=1,idempotency_key='named-assign',assignee_user_id='tech-28'))
    assert assigned.status_code==200
    for name,password in [('central','central-demo'),('dispatch-9','dispatch-demo'),('tech-28','tech-demo')]:
        token=client.post('/api/v1/auth/login',json=dict(username=name,password=password)).json()['access_token']
        client.headers['Authorization']='Bearer '+token
        data=client.get('/api/v1/maintenance-requests',params=dict(object_id=28,status='assigned',limit=1)).json()
        assert data['total']==1 and len(data['items'])==1
        assert data['items'][0]['id']==item['id']
        assert data['items'][0]['object_name']==get_objects_by_id()[28]['object_name']
        page=client.get('/api/v1/maintenance-requests',params=dict(object_id=28,status='assigned',limit=1,offset=1)).json()
        assert page['total']==1 and page['items']==[]
    token=client.post('/api/v1/auth/login',json=dict(username='tech-9',password='tech-demo-2')).json()['access_token']
    client.headers['Authorization']='Bearer '+token
    assert client.get('/api/v1/maintenance-requests',params={'object_id':28}).json()=={'items':[],'total':0}
