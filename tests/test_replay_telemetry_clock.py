from datetime import datetime, timezone
from types import SimpleNamespace
import polars as pl
from backend.app.services import event_store


def test_telemetry_clock_latest_history_advance_reset_normal(client,processed_dir):
    path=processed_dir/'events_2026.parquet'
    frame=pl.read_parquet(path).with_columns(
        pl.Series('timestamp',[datetime(2026,5,5,12),datetime(2026,5,6,8),datetime(2026,5,21,12),datetime(2026,5,21,12)]),
        pl.Series('sensor_value',['Норма','Не замкнут','Норма','other']),
        pl.Series('is_alarm',[False,True,False,False]),
    )
    frame.write_parquet(path)
    baseline=datetime(2026,5,6,6,tzinfo=timezone.utc)
    current=[baseline]
    runtime=SimpleNamespace(clock=SimpleNamespace(now=lambda:current[0]),end_at=datetime(2026,5,30,tzinfo=timezone.utc))
    normal_count=client.app.state.dispatcher_workflow.conn.execute('SELECT COUNT(*) FROM prediction_history').fetchone()[0]
    client.app.state.replay_runtime=runtime
    client.app.state.runtime_mode='replay'
    latest=client.get('/api/v1/channels/2866').json()
    assert datetime.fromisoformat(latest['last_event_at'])==datetime(2026,5,6,5,tzinfo=timezone.utc)
    assert latest['latest_value']=='Не замкнут' and latest['latest_alarm'] is True
    objects=client.get('/api/v1/objects/28/channels').json()['items']
    assert next(x for x in objects if x['id']==2866)['last_event_at']==latest['last_event_at']
    for params in ({},{'to':'2026-05-30T00:00:00Z'},{'alarm_only':True,'to':'2026-05-30T00:00:00Z'}):
        events=client.get('/api/v1/channels/2866/history',params=params).json()['events']
        assert events and all(datetime.fromisoformat(x['timestamp'])<=baseline for x in events)
    assert client.get('/api/v1/channels/2866/history',params={'from':'2026-05-21T00:00:00Z'}).json()['events']==[]
    current[0]=datetime(2026,5,22,tzinfo=timezone.utc)
    advanced=client.get('/api/v1/channels/2866').json()
    assert datetime.fromisoformat(advanced['last_event_at'])==datetime(2026,5,21,9,tzinfo=timezone.utc) and advanced['latest_alarm'] is False
    advanced_channels=client.get('/api/v1/objects/28/channels').json()['items']
    assert next(x for x in advanced_channels if x['id']==2866)['last_event_at']==advanced['last_event_at']
    assert any(datetime.fromisoformat(e['timestamp']).day==21 for e in client.get('/api/v1/channels/2866/history').json()['events'])
    current[0]=baseline
    assert client.get('/api/v1/channels/2866').json()==latest
    assert all(datetime.fromisoformat(e['timestamp'])<=baseline for e in client.get('/api/v1/channels/2866/history').json()['events'])
    assert client.app.state.dispatcher_workflow.conn.execute('SELECT COUNT(*) FROM prediction_history').fetchone()[0]==normal_count
    current[0]=datetime(2026,6,2,tzinfo=timezone.utc)
    assert event_store._source_naive(runtime.end_at).year==2026
    del client.app.state.replay_runtime
    client.app.state.runtime_mode='normal'
    assert client.get('/api/v1/channels/2866').json()==advanced
    token=client.post('/api/v1/auth/login',json={'username':'tech-28','password':'tech-demo'}).json()['access_token']
    client.headers['Authorization']='Bearer '+token
    assert client.get('/api/v1/channels/3018/history').status_code==404


def test_replay_clock_missing_fails_closed_and_end_is_clamped(client):
    from backend.app.services.telemetry_clock import telemetry_upper_bound
    from starlette.requests import Request
    end=datetime(2026,5,30,tzinfo=timezone.utc)
    client.app.state.replay_runtime=SimpleNamespace(clock=SimpleNamespace(now=lambda:datetime(2027,1,1,tzinfo=timezone.utc)),end_at=end)
    assert telemetry_upper_bound(Request({'type':'http','app':client.app}))==end
    del client.app.state.replay_runtime
    client.app.state.runtime_mode='replay'
    assert client.get('/api/v1/channels/2866').status_code==503
    assert client.get('/api/v1/channels/2866/history').status_code==503
    assert client.get('/api/v1/objects/28/channels').status_code==503


def test_latest_query_skips_future_annual_partition(client,monkeypatch):
    seen=[]
    original=event_store.duckdb.connect
    class Connection:
        def __enter__(self):self.connection=original();return self
        def __exit__(self,*args):self.connection.close()
        def execute(self,sql,args):seen.append((sql,args));return self.connection.execute(sql,args)
    monkeypatch.setattr(event_store.duckdb,'connect',Connection)
    # UTC boundary maps to Jan 1 in Moscow; use a strictly 2025 local bound.
    bound=datetime(2025,12,31,20,tzinfo=timezone.utc)
    event_store.get_latest_channel_events([2866],at_or_before=bound)
    assert seen and all('2026' not in args[0] for _,args in seen)
    assert all('timestamp <= ?' in sql for sql,_ in seen)
