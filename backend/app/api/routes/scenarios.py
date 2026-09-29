"""Read-only evidence-based fire, intrusion and flooding scenarios."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.config import as_utc
from backend.app.services.auth import AuthUser, get_current_user, require_capability, require_object_scope
from backend.app.services.incident_scenarios import SCENARIO_TYPES, analyze_scenario
from backend.app.services.planned_work import query_planned_work
from backend.app.services.reference_data import get_channels_by_object, get_objects_by_id
from backend.app.services.telemetry_clock import telemetry_upper_bound
from backend.app.services.weather_context import load_weather_context

router = APIRouter(prefix='/scenarios', tags=['Incident scenarios'])


def _registered_observations(request: Request, channel_ids: set[int], as_of: datetime):
    """Source only recorded observations <= authoritative replay/normal time."""
    runtime = getattr(request.app.state, 'replay_runtime', None)
    if runtime is not None:
        # The replay index advances only after a completed scoring boundary.
        # Even if this reader races with a worker, a further as_of filter
        # prevents observations from an uncommitted/future boundary leaking.
        with runtime.lock:
            events = tuple(runtime.events[:runtime._event_index])
        cutoff = as_of - timedelta(days=7)
        relevant = [dict(channel_id=row.channel_id, timestamp=row.timestamp, alarm=row.is_alarm,
                         value=row.sensor_value, source='replay_registered_event')
                    for row in events if row.channel_id in channel_ids and cutoff <= as_utc(row.timestamp) <= as_of]
        return relevant, 'historical_replay'
    if os.environ.get('TGOAT_SIMULATED_LIVE_MODE', '0') == '1':
        database = getattr(request.app.state, 'operational_database', None)
        if database is None:
            return [], 'unavailable'
        from psycopg.rows import dict_row
        from backend.app.services.live_status import current_simulated_watermark
        progress = current_simulated_watermark(database)
        if not progress or not channel_ids:
            return [], 'unavailable'
        bound = min(as_of, datetime.fromisoformat(progress['processed_at']))
        with database.connection() as connection:
            rows = connection.cursor(row_factory=dict_row).execute('''
                SELECT channel_id,timestamp,payload FROM simulated_live_events
                WHERE source_id=%s AND channel_id=ANY(%s)
                  AND timestamp >= %s AND timestamp <= %s
                ORDER BY timestamp,source_sequence LIMIT 20000''',
                (progress['source_id'], sorted(channel_ids), bound-timedelta(days=7), bound)).fetchall()
        return [dict(channel_id=r['channel_id'], timestamp=r['timestamp'],
                     alarm=r['payload'].get('is_alarm'), value=r['payload'].get('sensor_value'),
                     source='simulated_live_ingested_event') for r in rows], 'simulated_live'
    from backend.app.services.event_store import get_history_storage_health, get_latest_channel_events
    if get_history_storage_health()['status'] != 'available':
        return [], 'unavailable'
    latest = get_latest_channel_events(sorted(channel_ids), at_or_before=as_of)
    return [dict(channel_id=cid, timestamp=entry['timestamp'], alarm=entry['alarm'],
                 value=entry['value'], source='registered_event_history')
            for cid, entry in latest.items()], 'annual_event_history_latest_only'


@router.get('/{scenario_type}/objects/{object_id}')
def get_scenario(scenario_type: str, object_id: int, request: Request,
                 as_of: datetime | None = Query(default=None),
                 user: AuthUser = Depends(get_current_user)):
    require_capability(user, 'evidence.read')
    require_object_scope(user, object_id)
    if scenario_type not in SCENARIO_TYPES:
        raise HTTPException(404, 'Unknown scenario type')
    if object_id not in get_objects_by_id():
        raise HTTPException(404, 'Object not found')
    bound = telemetry_upper_bound(request)
    if as_of is not None and as_of.tzinfo is None:
        raise HTTPException(422, 'as_of requires timezone')
    selected = as_utc(as_of) if as_of else bound or datetime.now(timezone.utc)
    selected = min(selected, as_utc(bound) if bound is not None else datetime.now(timezone.utc))
    channels = get_channels_by_object().get(object_id, ())
    events, source = _registered_observations(request, {int(x['channel_id']) for x in channels}, selected)
    read_model = getattr(request.app.state, 'operational_read_service', None)
    proxy = None
    if read_model is not None:
        state = read_model.object_state(object_id)
        # Do not expose a later snapshot when requesting earlier scenario context.
        if state.snapshot_ts and as_utc(state.snapshot_ts) <= selected and state.status == 'available':
            proxy = {'status': state.status, 'probability': state.risk_probability,
                     'snapshot_ts': state.snapshot_ts.isoformat(), 'kind': 'generic_proxy_alarm_24h'}
    planned = query_planned_work(object_id, selected, upcoming_days=30, recent_days=7)
    weather = load_weather_context(os.environ.get("TGOAT_WEATHER_EVENTS_FILE"), selected)
    return analyze_scenario(scenario_type=scenario_type, object_id=object_id, as_of=selected,
                            channels=channels, observations=events, history_source=source,
                            planned_work=planned, generic_risk=proxy, weather=weather)
