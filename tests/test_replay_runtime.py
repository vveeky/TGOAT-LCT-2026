from datetime import datetime, timedelta, timezone
import json
import threading
import time

import pytest

from backend.app.services.ml_features import FeatureProviderError, HourlyState
from backend.app.services.replay_runtime import (
    ReplayClock, ReplayFeatureProvider, ReplayRuntime, ReplayTransitionError,
    load_replay_event_source, load_replay_manifest,
)


def wait_for_step(runtime):
    thread = runtime._step_thread
    if thread is not None:
        thread.join(timeout=120)
        assert not thread.is_alive(), "replay step did not finish within 120 seconds"
    return runtime.status()


@pytest.fixture(scope="module")
def replay_package():
    return load_replay_manifest("ml/data/demo/replay/replay_manifest.json")


@pytest.fixture(scope="module")
def replay_events(replay_package):
    manifest, _ = replay_package
    return load_replay_event_source(manifest)


def test_replay_manifest_is_digest_pinned_and_covers_current_reference(replay_package, replay_events):
    manifest, rows = replay_package
    assert manifest["artifact_sha256"] == "223b313953500c54678a495f65bc43e1e6a6f0ada38b8cd5ea267da86910ef76"
    assert manifest["feature_count"] == 87
    assert len(manifest["object_ids"]) == 78
    assert len(manifest["covered_object_ids"]) == 77
    assert len(rows) == manifest["hourly_state_row_count"]
    assert max(row.hour_ts for row in rows) <= datetime.fromisoformat(manifest["replay_end"])
    events = replay_events
    assert len(events) == manifest["event_source_row_count"] == 377770
    assert manifest["event_source_object_ids"] == manifest["object_ids"]
    assert set(manifest["event_source_row_count_by_object"]) == {str(value) for value in manifest["object_ids"]}
    from backend.app.config import as_utc
    assert all(as_utc(datetime.fromisoformat(manifest["replay_start"])) < as_utc(row.timestamp) <=
               as_utc(datetime.fromisoformat(manifest["replay_end"])) for row in events)


def test_stage4_catalog_history_follow_replay_session(client,replay_package,tmp_path):
    manifest,rows=replay_package
    runtime=ReplayRuntime(manifest,rows,session_root=tmp_path/'stage4-replay')
    normal=client.app.state.dispatcher_workflow
    try:
        client.app.state.dispatcher_workflow=runtime.context['workflow']
        before=client.get('/api/v1/operational/history').json()
        catalog=client.get('/api/v1/objects/catalog').json()
        assert len(catalog['items'])==78 and before['total']>0
        old_signal=runtime.context['workflow'].signals()['items'][0]
        old_card=old_signal['card_id']
        runtime.step()
        wait_for_step(runtime)
        after=client.get('/api/v1/operational/history').json()
        assert after['total']>before['total']
        fresh=client.get('/api/v1/objects/catalog').json()
        assert max(x['updated_at'] or '' for x in fresh['items'])>max(x['updated_at'] or '' for x in catalog['items'])
        signal=runtime.context['workflow'].signal(old_card)
        assert signal['card_id']==old_card and signal['work_version']==old_signal['work_version']
        runtime.reset()
        client.app.state.dispatcher_workflow=runtime.context['workflow']
        assert client.get('/api/v1/operational/history').json()['total']==before['total']
    finally:
        client.app.state.dispatcher_workflow=normal
        runtime.close()


def test_dispatcher_workflow_refresh_step_reset_isolated_from_normal(replay_package, tmp_path):
    from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow
    from backend.app.services.prediction_history import SQLitePredictionHistoryRepository
    from tests.test_prediction_history import prediction, BASE
    normal_history = SQLitePredictionHistoryRepository(tmp_path/'normal.sqlite')
    normal = SQLiteDispatcherWorkflow(normal_history)
    record, card, _ = normal_history.process_operational(prediction(BASE, object_id=28))
    normal_result = normal.decide(card.card_id, actor_user_id='normal', actor_role='central_dispatcher',
        payload={'decision':'monitoring','prediction_identity':record.identity,'expected_version':0,'idempotency_key':'normal'})
    manifest, rows = replay_package
    runtime = ReplayRuntime(manifest, rows, session_root=tmp_path/'replay')
    try:
        repo = runtime.context['workflow']
        first = repo.signals()['items'][0]
        payload = {'decision':'monitoring','prediction_identity':first['prediction_identity'],
                   'expected_version':0,'idempotency_key':'replay'}
        result = repo.decide(first['card_id'],actor_user_id='demo',actor_role='central_dispatcher',payload=payload)
        # Fresh HTTP reads use the same repository; never initialize user state on GET.
        assert repo.signal(first['card_id'])==result['signal']
        assert runtime.context['workflow'].signal(first['card_id'])['work_version']==1
        runtime.step()
        wait_for_step(runtime)
        assert repo.signal(first['card_id'])['state']=='monitoring'
        assert repo.signal(first['card_id'])['work_version']==1
        total_cards = repo.conn.execute('SELECT COUNT(*) FROM forecast_cards').fetchone()[0]
        assert repo.signals()['total']==total_cards
        old_path = str(repo.history.path)
        runtime.reset()
        fresh = runtime.context['workflow']
        assert str(fresh.history.path)!=old_path
        assert fresh.signal(first['card_id'])['state']=='new'
        assert fresh.signal(first['card_id'])['work_version']==0
        assert normal.signal(card.card_id)==normal_result['signal']
        assert normal.decide(card.card_id,actor_user_id='normal',actor_role='central_dispatcher',
                             payload={'decision':'monitoring','prediction_identity':record.identity,'expected_version':0,'idempotency_key':'normal'})==normal_result
    finally:
        runtime.close()


def test_historical_replay_runs_real_intermediate_model_predictions_and_preserves_signal(
        replay_package, replay_events, tmp_path):
    manifest, rows = replay_package
    object_id = 5343
    scoped_manifest = {**manifest, "object_ids": [object_id], "covered_object_ids": [object_id]}
    runtime = ReplayRuntime(scoped_manifest,
                            [row for row in rows if row.object_id == object_id],
                            events=[event for event in replay_events if event.object_id == object_id],
                            session_root=tmp_path / "real-replay")
    try:
        before_history = runtime.context["forecast"].history(object_id, limit=100)
        baseline_cards = runtime.context["forecast"].cards(object_id)
        signals = runtime.context["workflow"].signals(object_id=object_id)["items"]
        if signals:
            signal = signals[0]
            decision = runtime.context["workflow"].decide(
                signal["card_id"], actor_user_id="demo", actor_role="central_dispatcher",
                payload={"decision": "inspection_requested", "prediction_identity": signal["prediction_identity"],
                         "expected_version": signal["work_version"], "idempotency_key": "replay-update",
                         "request": {"reason": "Проверка replay persistence", "priority": "normal"}},
            )
        else:
            decision = None

        status = runtime.step()
        status = wait_for_step(runtime)
        after_history = runtime.context["forecast"].history(object_id, limit=1000)
        assert status["processed_scoring_boundaries"] == 71
        assert len(after_history) - len(before_history) == 71
        assert any(row.prediction.snapshot_ts.minute in {5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}
                   and row.prediction.snapshot_ts.second == 0 for row in after_history)
        arrivals = [row for row in runtime._scoring_rows
                    if row["object_id"] == object_id and row["reason"] == "telemetry_and_time"]
        # A known real object has registered telemetry inside this six-hour slice.
        assert arrivals
        current_signals = runtime.context["workflow"].signals(object_id=object_id)["items"]
        cards = runtime.context["forecast"].cards(object_id)
        assert runtime.context["workflow"].signals(object_id=object_id)["total"] == len(cards)
        assert len({item["card_id"] for item in current_signals}) == len(current_signals)
        if decision:
            preserved = runtime.context["workflow"].signal(decision["signal"]["card_id"])
            assert preserved["state"] == "inspection_requested"
            assert preserved["work_version"] == 1
            assert runtime.context["workflow"].request_detail(decision["request"]["id"])["status"] == "new"
            stored_basis = runtime.context["workflow"].conn.execute(
                "SELECT prediction_identity FROM signal_decisions WHERE id=?",
                (decision["decision_id"],),
            ).fetchone()[0]
            assert stored_basis == signal["prediction_identity"]
        assert all(row.prediction.prediction_status in {"available", "degraded"} for row in after_history)
        assert len(cards) <= len(baseline_cards) + 1
    finally:
        runtime.close()


def test_60x_fake_clock_scores_two_real_model_boundaries(monkeypatch, replay_package, replay_events, tmp_path):
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: None)
    manifest, rows = replay_package
    object_id = 5343
    scoped = {
        **manifest, "object_ids": [object_id], "covered_object_ids": [object_id],
        "replay_start": "2026-05-05T12:00:00+03:00",
    }
    runtime = ReplayRuntime(
        scoped, [row for row in rows if row.object_id == object_id],
        events=[event for event in replay_events if event.object_id == object_id],
        session_root=tmp_path / "60x-runtime",
    )
    try:
        history = runtime.context["forecast"].history(object_id, limit=1000)
        wall = [100.0]
        runtime.clock._monotonic = lambda: wall[0]
        runtime.state = "running"
        runtime.clock.configure(speed=60, running=True)
        wall[0] += 10
        target = runtime.clock.target_now()
        runtime.advance_due(target)
        after = runtime.context["forecast"].history(object_id, limit=1000)
        created = [item for item in after if item.identity not in {record.identity for record in history}]
        expected_first = ReplayRuntime._next_scoring_boundary(runtime.start_at)
        assert len(created) == 2
        assert [item.prediction.snapshot_ts for item in reversed(created)] == [
            expected_first, expected_first + timedelta(minutes=5),
        ]
        assert runtime.status()["processed_scoring_boundaries"] == 2
        assert runtime.clock.now() <= target
    finally:
        runtime.close()


def test_technician_execution_replay_reset_isolation(replay_package,tmp_path):
    from tests.test_maintenance_execution import TECH,CENTRAL,act
    from backend.app.services.prediction_history import SQLitePredictionHistoryRepository
    from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow
    from tests.test_prediction_history import BASE,prediction
    normal_history=SQLitePredictionHistoryRepository(tmp_path/'normal.sqlite')
    normal=SQLiteDispatcherWorkflow(normal_history)
    normal_history.process_operational(prediction(BASE,object_id=28))
    before=normal.signals()
    manifest,rows=replay_package
    runtime=ReplayRuntime(manifest,rows,session_root=tmp_path/'replay')
    try:
        repo=runtime.context['workflow']
        signal=repo.signals(object_id=28)['items'][0]
        result=repo.decide(signal['card_id'],actor_user_id='central',actor_role='central_dispatcher',payload=dict(expected_version=0,idempotency_key='inspection',decision='inspection_requested',prediction_identity=signal['prediction_identity'],request=dict(reason='Проверка',priority='normal')))
        item=act(repo,result['request'],'assign',assignee_user_id=TECH.user_id)
        item=act(repo,item,'start',TECH)
        item=act(repo,item,'result',TECH,result_code='no_issue_found',summary='Проверено')
        item=act(repo,item,'accept')
        assert repo.request_detail(item['id'])['status']=='closed'
        assert repo.signal(signal['card_id'])['state']=='resolved'
        assert repo.request_detail(item['id'])==item
        runtime.reset()
        fresh=runtime.context['workflow']
        assert fresh.filter()[1]==0 and fresh.signal(signal['card_id'])['state']=='new'
        assert normal.signals()==before
    finally:
        runtime.close();normal_history.close()


def test_replay_accepting_unable_to_verify_keeps_vita_signal_visible(replay_package,tmp_path):
    from tests.test_maintenance_execution import TECH,CENTRAL,act
    manifest,rows=replay_package
    runtime=ReplayRuntime(manifest,rows,session_root=tmp_path/'replay-vita-unverified')
    try:
        repo=runtime.context['workflow']
        signal=repo.signals(object_id=28)['items'][0]
        decision=repo.decide(signal['card_id'],actor_user_id=CENTRAL.user_id,actor_role=CENTRAL.role,
            payload=dict(expected_version=0,idempotency_key='vita-inspection',decision='inspection_requested',
                prediction_identity=signal['prediction_identity'],
                request=dict(reason='Проверка Виты',priority='normal')))
        request=act(repo,decision['request'],'assign',assignee_user_id=TECH.user_id)
        request=act(repo,request,'start',TECH)
        request=act(repo,request,'result',TECH,result_code='unable_to_verify',
            summary='Фактическое состояние оборудования не установлено')
        awaiting=request
        accepted=act(repo,awaiting,'accept',key='vita-accept-once',
            comment='Фактическое состояние оборудования не установлено')
        repeated=act(repo,awaiting,'accept',key='vita-accept-once',
            comment='Фактическое состояние оборудования не установлено')

        assert repeated==accepted
        assert accepted['status']=='closed'
        assert accepted['latest_result']['result_code']=='unable_to_verify'
        assert accepted['accepted_by_user_id']==CENTRAL.user_id
        assert sum(event['event']=='accepted' for event in accepted['timeline'])==1
        assert repo.signal(signal['card_id'])['state']=='new'
        visible=repo.signals(object_id=28,state='new')
        assert any(item['card_id']==signal['card_id'] for item in visible['items'])
    finally:
        runtime.close()


def test_replay_signal_request_audit_is_visible_correlated_and_idempotent(
    client, replay_package, tmp_path,
):
    from urllib.parse import quote

    from backend.app.main import app
    from backend.app.services.auth import auth_provider

    manifest, rows = replay_package
    runtime = ReplayRuntime(manifest, rows, session_root=tmp_path / 'replay-audit')
    previous_workflow = app.state.dispatcher_workflow
    repo = runtime.context['workflow']
    signal = repo.signals(object_id=28)['items'][0]
    central = auth_provider.authenticate('central', 'central-demo')
    client.headers['Authorization'] = f'Bearer {auth_provider.issue_token(central)}'
    path = f"/api/v1/signals/{quote(signal['card_id'], safe='')}/decision"
    payload = {
        'expected_version': signal['work_version'],
        'idempotency_key': 'replay-audit-vita-once',
        'decision': 'inspection_requested',
        'prediction_identity': signal['prediction_identity'],
        'comment': 'Проверка по FIX 02',
        'request': {'reason': 'Проверка Виты', 'priority': 'normal'},
    }
    try:
        app.state.dispatcher_workflow = repo
        first = client.post(path, json=payload)
        assert first.status_code == 200, first.text
        request_id = first.json()['request']['id']

        created = client.get('/api/v1/audit-events', params={
            'action': 'maintenance_request.created',
        }).json()
        assert created['total'] == 1
        event = created['items'][0]
        assert event['user_id'] == central.user_id
        assert event['entity_type'] == 'maintenance_request'
        assert event['entity_id'] == str(request_id)
        assert event['object_id'] == 28
        assert datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')) == \
            datetime.fromisoformat(first.json()['request']['created_at'].replace('Z', '+00:00'))

        signal_audit = client.get('/api/v1/audit-events', params={
            'action': 'signal.decision_command',
        }).json()
        assert signal_audit['total'] == 1
        assert signal_audit['items'][0]['metadata']['maintenance_request_id'] == request_id

        repeated = client.post(path, json=payload)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()['request']['id'] == request_id
        assert repo.filter()[1] == 1
        assert repo.domain_audit_repository.filter(
            action='maintenance_request.created', limit=None,
        )[1] == 1
        assert client.get('/api/v1/audit-events', params={
            'action': 'maintenance_request.created',
        }).json()['total'] == 1
        repeated_signal_audit = client.get('/api/v1/audit-events', params={
            'action': 'signal.decision_command',
        }).json()
        assert repeated_signal_audit['total'] == 2
        assert all(item['metadata']['maintenance_request_id'] == request_id
                   for item in repeated_signal_audit['items'])
    finally:
        app.state.dispatcher_workflow = previous_workflow
        runtime.close()


def test_replay_clock_virtual_speed_pause_resume():
    start = datetime(2026, 5, 5, 9, 0, 0, tzinfo=timezone.utc)
    clock = ReplayClock(start)
    wall = [100.0]
    clock._monotonic = lambda: wall[0]
    clock.configure(speed=600, running=True)
    wall[0] += 3
    assert clock.now() == start
    assert clock.target_now() == start + timedelta(minutes=30)
    clock.commit(start + timedelta(minutes=5))
    assert clock.now() == start + timedelta(minutes=5)
    clock.configure(running=False)
    paused_at = clock.now()
    wall[0] += 10
    assert clock.now() == paused_at
    clock.configure(speed=3600, running=True)
    wall[0] += 1
    assert clock.target_now() == paused_at + timedelta(hours=1)


def test_replay_clock_60x_commits_two_boundaries_after_ten_wall_seconds():
    start = datetime(2026, 5, 5, 9, 0, 0, tzinfo=timezone.utc)
    clock = ReplayClock(start)
    wall = [100.0]
    clock._monotonic = lambda: wall[0]
    clock.configure(speed=60, running=True)
    wall[0] += 10
    target = clock.target_now()
    assert target == start + timedelta(minutes=10)
    first = ReplayRuntime._next_scoring_boundary(start)
    second = first + timedelta(minutes=5)
    assert first <= target and second <= target
    clock.commit(second)
    assert clock.now() == second
    assert clock.now() <= target


def test_clock_speed_change_preserves_processed_watermark_and_backpressure_lag():
    start = datetime(2026, 5, 5, 9, 0, 0, tzinfo=timezone.utc)
    clock = ReplayClock(start)
    wall = [100.0]
    clock._monotonic = lambda: wall[0]
    clock.configure(speed=600, running=True)
    wall[0] += 1
    clock.commit(start + timedelta(minutes=5))
    wall[0] += 2
    unprocessed_target = clock.target_now()

    clock.configure(speed=60, running=True)

    assert clock.now() == start + timedelta(minutes=5)
    assert clock.target_now() == unprocessed_target
    wall[0] += 1
    assert clock.target_now() == unprocessed_target + timedelta(minutes=1)


def test_continuous_pause_finishes_only_the_inflight_boundary(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: None)

    def held_boundary(self, timestamp, terminal=False):
        calls.append(timestamp)
        entered.set()
        assert release.wait(timeout=5)
        self._last_scored_at = timestamp
        self._processed_boundaries += 1
        self.clock.commit(timestamp)

    monkeypatch.setattr(ReplayRuntime, "_score_boundary", held_boundary)
    start = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
    runtime = ReplayRuntime({
        "replay_id": "pause-continuous", "replay_start": start.isoformat(),
        "replay_end": (start + timedelta(hours=6)).isoformat(),
        "warmup_start": (start - timedelta(hours=168)).isoformat(),
        "object_ids": [5343], "covered_object_ids": [5343], "default_speed": 600,
    }, [], session_root=tmp_path)
    wall = [100.0]
    runtime.clock._monotonic = lambda: wall[0]
    runtime.state = "running"
    runtime.clock.configure(speed=600, running=True)
    wall[0] += 1.5  # three scoring boundaries are due
    target = runtime.clock.target_now()
    advance_thread = threading.Thread(target=lambda: runtime.advance_due(target))
    pause_result = []
    try:
        advance_thread.start()
        assert entered.wait(timeout=5)
        pause_thread = threading.Thread(target=lambda: pause_result.append(runtime.pause()))
        pause_thread.start()
        assert runtime._continuous_pause.wait(timeout=5)
        release.set()
        advance_thread.join(timeout=5)
        pause_thread.join(timeout=5)
        assert not advance_thread.is_alive() and not pause_thread.is_alive()
        first = ReplayRuntime._next_scoring_boundary(start)
        assert calls == [first]
        assert pause_result[0]["state"] == "paused"
        assert pause_result[0]["processed_scoring_boundaries"] == 1
        assert pause_result[0]["virtual_time"] == first.astimezone(timezone.utc).isoformat()
        assert runtime.status()["next_inference_at"] == (
            first + timedelta(minutes=5)
        ).astimezone(timezone.utc).isoformat()
    finally:
        release.set()
        runtime.close()


def test_failed_replay_batch_rolls_back_and_retry_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: None)
    start = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
    object_ids = [5343, 5340]
    runtime = ReplayRuntime({
        "replay_id": "batch-rollback", "replay_start": start.isoformat(),
        "replay_end": (start + timedelta(hours=6)).isoformat(),
        "warmup_start": (start - timedelta(hours=168)).isoformat(),
        "object_ids": object_ids, "covered_object_ids": object_ids, "default_speed": 60,
    }, [], session_root=tmp_path)
    repo = runtime.context["forecast"].repository
    original = repo.process_operational
    first_boundary = ReplayRuntime._next_scoring_boundary(start)

    def batch_predict(requests):
        return [{
            "object_id": object_id, "snapshot_ts": snapshot_ts.isoformat(),
            "generated_at": snapshot_ts.isoformat(), "risk_probability": 0.8,
            "risk_flag": True, "threshold": 0.39877346430366006, "status": "available",
            "risk_score_kind": "ml", "model_version": "risk_baseline_v1_phase1_1_hgb",
            "feature_schema_version": "risk_baseline_v1_phase2",
            "reference_snapshot_id": "replay-reference", "reference_hash": "replay-reference-hash",
        } for object_id, snapshot_ts in requests]

    runtime.context["cycle"].inference_service.batch_predict = batch_predict
    before = repo.snapshot()
    calls = 0

    def fail_second_write(prediction):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected failure after first row was written")
        return original(prediction)

    try:
        monkeypatch.setattr(repo, "process_operational", fail_second_write)
        with pytest.raises(RuntimeError, match="injected failure"):
            runtime._score_boundary(first_boundary)
        assert repo.snapshot() == before
        assert runtime._last_scored_at == start
        assert runtime.status()["next_inference_at"] == first_boundary.isoformat()

        monkeypatch.setattr(repo, "process_operational", original)
        runtime._score_boundary(first_boundary)
        committed = repo.snapshot()
        assert len(committed["history"]) == 2
        assert len(committed["cards"]) == 2

        runtime._score_boundary(first_boundary)
        retried = repo.snapshot()
        assert retried == committed
        assert len(runtime.context["workflow"].signals()["items"]) == 2
    finally:
        runtime.close()


def test_replay_provider_fails_closed_for_uncovered_objects():
    class Delegate:
        def snapshot(self, object_id, snapshot_ts):
            return {"fixture": True}

        def metadata(self):
            return {"available_to": None}

    provider = ReplayFeatureProvider(Delegate(), {5343})
    assert provider.snapshot(5343, datetime.now(timezone.utc)) == {"fixture": True}
    with pytest.raises(FeatureProviderError, match="coverage is incomplete"):
        provider.snapshot(20, datetime.now(timezone.utc))


def test_replay_catch_up_processes_every_five_minute_boundary(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: calls.append((ts, mode)))
    monkeypatch.setattr(ReplayRuntime, "_score_boundary", lambda self, ts, terminal=False: calls.append((ts, "score")))
    start = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
    end = start + timedelta(hours=12)
    row = lambda hour: HourlyState(object_id=5343, channel_id=1, hour_ts=start + timedelta(hours=hour),
                                    system_type="gas", event_count=1)
    runtime = ReplayRuntime({
        "replay_id": "unit", "replay_start": start.isoformat(), "replay_end": end.isoformat(),
        "warmup_start": (start - timedelta(hours=168)).isoformat(), "object_ids": [5343],
        "covered_object_ids": [5343], "default_speed": 1800,
    }, [row(-1), row(0), row(3), row(9)], session_root=tmp_path)
    try:
        store = runtime.context["feature_store"]
        assert all(row.hour_ts <= start for row in store._rows)
        assert runtime.status()["state"] == "ready"
        runtime.state = "running"
        wall = [0.0]
        runtime.clock._monotonic = lambda: wall[0]
        runtime.clock.configure(speed=3600, running=True)
        wall[0] += 17 / 60
        target = runtime.clock.target_now()
        runtime.advance_due(target)
        scored = [ts for ts, mode in calls if mode == "score"]
        assert scored == [start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1, minutes=5),
                          start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1, minutes=10),
                          start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1, minutes=15)]
        assert scored[-1] <= runtime.clock.now() <= target
        assert max(item.hour_ts for item in store._rows) <= scored[-1]
        wall[0] += (end - target).total_seconds() / 3600
        runtime.advance_due(runtime.clock.target_now())
        assert len([mode for _, mode in calls if mode == "score"]) > len(scored)
        assert calls[-1][0] == end
        assert runtime.status()["state"] == "completed"
        assert max(item.hour_ts for item in store._rows) == start + timedelta(hours=9)
    finally:
        runtime.close()


def test_manual_replay_step_runs_all_five_minute_ticks_and_completes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: calls.append((ts, mode)))
    monkeypatch.setattr(ReplayRuntime, "_score_boundary", lambda self, ts, terminal=False: calls.append((ts, "score")))
    start = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
    end = start + timedelta(hours=12)
    rows = [
        HourlyState(object_id=5343, channel_id=1, hour_ts=start + timedelta(hours=offset),
                    system_type="gas", event_count=1)
        for offset in (-1, 0, 3, 9)
    ]
    runtime = ReplayRuntime({
        "replay_id": "unit-step", "replay_start": start.isoformat(), "replay_end": end.isoformat(),
        "warmup_start": (start - timedelta(hours=168)).isoformat(), "object_ids": [5343],
        "covered_object_ids": [5343], "default_speed": 1800,
    }, rows, session_root=tmp_path)
    try:
        store = runtime.context["feature_store"]
        first = runtime.step()
        first = wait_for_step(runtime)
        assert first["state"] == "paused"
        six_hour_mark = start + timedelta(hours=6)
        assert first["virtual_time"] == six_hour_mark.astimezone(timezone.utc).isoformat()
        first_boundary = ReplayRuntime._next_scoring_boundary(start)
        assert first["next_inference_at"] == (first_boundary + timedelta(hours=5, minutes=55)).astimezone(timezone.utc).isoformat()
        assert calls[-1] == (first_boundary + timedelta(hours=5, minutes=50), "score")
        assert max(row.hour_ts for row in store._rows) <= six_hour_mark

        second = runtime.step()
        second = wait_for_step(runtime)
        assert second["state"] == "completed"
        assert second["virtual_time"] == end.astimezone(timezone.utc).isoformat()
        assert second["next_inference_at"] is None
        assert calls[-1] == (end, "score")
        assert len([mode for _, mode in calls if mode == "score"]) == 144

        with pytest.raises(ReplayTransitionError, match="ready or paused"):
            runtime.step()
    finally:
        runtime.close()


def test_six_hour_step_returns_without_waiting_for_catch_up(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: None)

    def slow_score(self, timestamp, terminal=False):
        entered.set()
        release.wait(timeout=5)

    monkeypatch.setattr(ReplayRuntime, "_score_boundary", slow_score)
    start = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
    runtime = ReplayRuntime({
        "replay_id": "async-step", "replay_start": start.isoformat(),
        "replay_end": (start + timedelta(hours=6)).isoformat(),
        "warmup_start": (start - timedelta(hours=168)).isoformat(),
        "object_ids": [5343], "covered_object_ids": [5343], "default_speed": 60,
    }, [], session_root=tmp_path)
    try:
        before = time.perf_counter()
        accepted = runtime.step()
        assert time.perf_counter() - before < 1
        assert accepted["state"] == "running"
        assert accepted["pending_step"] is True
        assert entered.wait(timeout=5)
    finally:
        release.set()
        runtime.close()


def test_pause_finishes_current_boundary_and_stops_before_next(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: None)

    def held_boundary(self, timestamp, terminal=False):
        entered.set()
        release.wait(timeout=5)
        self._last_scored_at = timestamp
        self._processed_boundaries += 1
        self.clock.configure(at=timestamp, running=False)

    monkeypatch.setattr(ReplayRuntime, "_score_boundary", held_boundary)
    start = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
    runtime = ReplayRuntime({
        "replay_id": "pause-step", "replay_start": start.isoformat(),
        "replay_end": (start + timedelta(hours=6)).isoformat(),
        "warmup_start": (start - timedelta(hours=168)).isoformat(),
        "object_ids": [5343], "covered_object_ids": [5343], "default_speed": 60,
    }, [], session_root=tmp_path)
    result = []
    try:
        runtime.step()
        assert entered.wait(timeout=5)
        pause_thread = threading.Thread(target=lambda: result.append(runtime.pause()))
        pause_thread.start()
        for _ in range(100):
            if runtime._step_cancel.is_set():
                break
            threading.Event().wait(0.01)
        assert runtime._step_cancel.is_set()
        release.set()
        pause_thread.join(timeout=5)
        assert not pause_thread.is_alive()
        status = result[0]
        first_boundary = ReplayRuntime._next_scoring_boundary(start)
        assert status["state"] == "paused" and status["pending_step"]
        assert status["processed_scoring_boundaries"] == 1
        assert status["virtual_time"] == first_boundary.astimezone(timezone.utc).isoformat()
        assert runtime.status()["next_inference_at"] == (
            first_boundary + timedelta(minutes=5)
        ).astimezone(timezone.utc).isoformat()
    finally:
        release.set()
        runtime.close()


def test_failed_step_retries_same_boundary_then_finishes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: None)

    def fail_once(self, timestamp, terminal=False):
        calls.append(timestamp)
        if len(calls) == 1:
            raise RuntimeError("injected scoring failure")

    monkeypatch.setattr(ReplayRuntime, "_score_boundary", fail_once)
    start = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
    runtime = ReplayRuntime({
        "replay_id": "retry-step", "replay_start": start.isoformat(),
        "replay_end": (start + timedelta(hours=6)).isoformat(),
        "warmup_start": (start - timedelta(hours=168)).isoformat(),
        "object_ids": [5343], "covered_object_ids": [5343], "default_speed": 60,
    }, [], session_root=tmp_path)
    try:
        runtime.step()
        status = wait_for_step(runtime)
        assert status["state"] == "paused" and status["pending_step"]
        failed_at = calls[0]
        runtime.resume()
        status = wait_for_step(runtime)
        assert status["state"] == "completed" and not status["pending_step"]
        assert calls[:2] == [failed_at, failed_at]
        assert len(calls) == 73
        assert calls[-1] == start + timedelta(hours=6)
        assert runtime.status()["virtual_time"] == (start + timedelta(hours=6)).astimezone(
            timezone.utc
        ).isoformat()
    finally:
        runtime.close()


def test_replay_reset_rotates_only_its_isolated_session(monkeypatch, tmp_path):
    monkeypatch.setattr(ReplayRuntime, "_run_cycle", lambda self, ts, mode: None)
    monkeypatch.setattr(ReplayRuntime, "_score_boundary", lambda self, ts, terminal=False: None)
    start = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
    runtime = ReplayRuntime({
        "replay_id": "unit-reset", "replay_start": start.isoformat(),
        "replay_end": (start + timedelta(hours=6)).isoformat(),
        "warmup_start": (start - timedelta(hours=168)).isoformat(),
        "object_ids": [5343], "covered_object_ids": [5343], "default_speed": 1800,
    }, [], session_root=tmp_path)
    old_id = runtime.session_id
    old_dir = runtime.session_dir
    try:
        runtime.start()
        runtime.pause()
        assert runtime.status()["state"] == "paused"
        assert runtime.set_speed(600)["speed"] == 600
        result = runtime.reset()
        assert runtime.session_id != old_id
        assert result["state"] == "ready"
        assert result["virtual_time"] == start.astimezone(timezone.utc).isoformat()
        assert old_dir.exists()  # detached from all API readers until orderly shutdown
    finally:
        runtime.close()
    assert not old_dir.exists()
