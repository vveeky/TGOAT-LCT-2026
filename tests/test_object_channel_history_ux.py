from datetime import timedelta
import pytest
from tests.test_prediction_history import BASE,prediction


def test_catalog_scoped_current_and_human_state_without_n_plus_one(client):
    repo=client.app.state.dispatcher_workflow
    record,card,_=repo.history.process_operational(prediction(BASE,object_id=28,probability=.63))
    trace=[];repo.conn.set_trace_callback(trace.append)
    try:
        response=client.get('/api/v1/objects/catalog',params={'search':'Вита','attention':'new'})
    finally:repo.conn.set_trace_callback(None)
    assert response.status_code==200
    rows=response.json()['items'];selected=next(x for x in rows if x['id']==28)
    assert selected['risk_probability']==.63 and selected['human_state']=='new'
    assert len([q for q in trace if q.lstrip().upper().startswith('SELECT')])==2
    unavailable=client.get('/api/v1/objects/catalog',params={'attention':'unavailable'}).json()['items']
    assert all(x['risk_probability'] is None for x in unavailable)
    token=client.post('/api/v1/auth/login',json={'username':'tech-28','password':'tech-demo'}).json()['access_token']
    client.headers['Authorization']='Bearer '+token
    scoped=client.get('/api/v1/objects/catalog').json()['items']
    assert scoped and all(x['id']==28 for x in scoped)


@pytest.mark.parametrize('days',[1,7,30])
def test_event_range_alarm_pagination_and_latest(client,days):
    from backend.app.services.reference_data import get_channels_by_object
    channel=get_channels_by_object()[28][0]['channel_id']
    end='2026-01-01T01:00:00+00:00'
    from datetime import datetime
    start=(datetime.fromisoformat(end)-timedelta(days=days)).isoformat()
    params={'from':start,'to':end,'alarm_only':True,'limit':1}
    first=client.get(f'/api/v1/channels/{channel}/history',params=params)
    assert first.status_code==200
    assert all(e['alarm'] is True for e in first.json()['events'])
    second=client.get(f'/api/v1/channels/{channel}/history',params={**params,'offset':1})
    assert second.status_code==200
    assert first.json()['events']!=second.json()['events']
    channels=client.get('/api/v1/objects/28/channels').json()['items']
    assert 'latest_value' in channels[0] and 'latest_alarm' in channels[0]


def test_invalid_period_and_foreign_channel(client):
    from backend.app.services.reference_data import get_channels_by_object
    channel=get_channels_by_object()[28][0]['channel_id']
    assert client.get(f'/api/v1/channels/{channel}/history',params={'from':'2026-01-02T00:00:00Z','to':'2026-01-01T00:00:00Z'}).status_code==422
    assert client.get('/api/v1/operational/history',params={'from':'bad'}).status_code==422
    foreign=next(items[0]['channel_id'] for oid,items in get_channels_by_object().items() if oid!=28 and items)
    token=client.post('/api/v1/auth/login',json={'username':'tech-28','password':'tech-demo'}).json()['access_token']
    client.headers['Authorization']='Bearer '+token
    assert client.get(f'/api/v1/channels/{foreign}/history').status_code==404
    assert client.get('/api/v1/operational/history',params={'state':'new'}).status_code==403


def test_history_named_bounded_and_episode_specific(client):
    repo=client.app.state.dispatcher_workflow
    a,card,_=repo.history.process_operational(prediction(BASE,object_id=28,probability=.63))
    repo.history.process_operational(prediction(BASE+timedelta(hours=6),object_id=28,probability=.1))
    b,_,_=repo.history.process_operational(prediction(BASE+timedelta(hours=24),object_id=28,probability=.95))
    data=client.get('/api/v1/operational/history',params={'object_id':28,'limit':1}).json()
    assert data['total']==3 and len(data['items'])==1
    assert data['items'][0]['identity']==b.identity
    assert data['items'][0]['object_name']
    old=client.get('/api/v1/operational/history',params={'object_id':28,'offset':2,'limit':1}).json()['items'][0]
    assert old['identity']==a.identity and old['risk_probability']==.63 and old['card_id']==card.card_id
    exact=client.get('/api/v1/operational/history',params={'prediction_identity':a.identity}).json()
    assert exact['total']==1 and exact['items'][0]['identity']==a.identity


def test_operational_activity_scoped_and_chronological(client):
    from tests.test_dispatcher_core import command,decide
    from tests.test_maintenance_execution import TECH,act
    repo=client.app.state.dispatcher_workflow
    record,card,_=repo.history.process_operational(prediction(BASE,object_id=28,probability=.63))
    request=decide(repo,card,command(record))['request']
    request=act(repo,request,'assign',assignee_user_id=TECH.user_id)
    request=act(repo,request,'start',TECH)
    activity=client.get('/api/v1/operational/history',params={'object_id':28}).json()['activity']
    assert {'signal_created','decision','created','assigned','started'} <= {item['event'] for item in activity}
    assert [item['timestamp'] for item in activity]==sorted([item['timestamp'] for item in activity],reverse=True)
    token=client.post('/api/v1/auth/login',json={'username':'tech-28','password':'tech-demo'}).json()['access_token']
    client.headers['Authorization']='Bearer '+token
    technician=client.get('/api/v1/operational/history',params={'object_id':28}).json()
    assert all(item['event'] not in {'decision','signal_created'} for item in technician['activity'])
    assert all(item['card_id'] is None for item in technician['items'])
