from datetime import datetime
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.schemas import (ChannelListResponse, ForecastCard, Object, ObjectListResponse,
                                  OperationalPredictionHistory, RecommendationResponse,
                                  RiskForecastResponse, RiskLevel)
from backend.app.services.api_serializers import serialize_channel, serialize_object
from backend.app.services.mock_storage import event_history_repository
from backend.app.services.reference_data import get_channels_by_object, get_objects_by_id, get_objects_data
from backend.app.services.risk_state import active_request_counts, channel_risks, object_risks
from backend.app.services.auth import AuthUser, get_current_user, require_object_scope, user_can_access_object
from backend.app.services.audit import record_ml_inference_event, record_recommendation_event
from backend.app.config import as_utc
from backend.app.services.ml_features import is_canonical_snapshot, latest_canonical_snapshot
from backend.app.services.prediction_history import (OutOfOrderPredictionError,
                                                      ProvenanceConflictError)
from backend.app.services.planned_work import query_planned_work
from backend.app.services.telemetry_clock import telemetry_upper_bound

router = APIRouter(prefix="/objects", tags=["Objects"])


def _object_view(obj: dict) -> dict:
    object_id = obj["object_id"]
    return serialize_object(
        obj, object_risks([object_id])[object_id], active_request_counts([object_id])[object_id]
    )


@router.get("", response_model=ObjectListResponse)
def get_objects(search: str | None = None, type: str | None = None,
                risk_level: RiskLevel | None = None, limit: int = Query(100, ge=1, le=1000),
                offset: int = Query(0, ge=0), user: AuthUser = Depends(get_current_user)):
    objects = [x for x in get_objects_data() if x["has_channels"]]
    objects = [x for x in objects if user_can_access_object(user, x["object_id"])]
    if search: objects = [x for x in objects if search.lower() in x["object_name"].lower()]
    if type: objects = [x for x in objects if x["object_type"] == type]
    object_ids = [x["object_id"] for x in objects]
    risks = object_risks(object_ids)
    request_counts = active_request_counts(object_ids)
    views = [serialize_object(x, risks[x["object_id"]], request_counts[x["object_id"]]) for x in objects]
    if risk_level: views = [x for x in views if x["risk_level"] == risk_level]
    return {"items": views[offset:offset + limit], "total": len(views)}


@router.get('/catalog')
def object_catalog(request: Request, search: str = '', attention: str | None = Query(None, pattern='^(new|monitoring|inspection_requested|none|unavailable)$'), user: AuthUser = Depends(get_current_user)):
    from backend.app.api.routes.signals import workflow
    repo=workflow(request)
    metadata=[item for item in get_objects_data() if item['has_channels'] and user_can_access_object(user,item['object_id'])]
    ids=[item['object_id'] for item in metadata]
    if not ids:return {'items':[],'total':0}
    catalog_projection=getattr(repo,'object_catalog_projection',None)
    if callable(catalog_projection):
        current,states=catalog_projection(ids)
        by_object={}
        order={'new':0,'inspection_requested':1,'monitoring':2}
        for row in states:
            if row['state']!='resolved' and order[row['state']]<order.get(by_object.get(row['object_id']),9):
                by_object[row['object_id']]=row['state']
        items=[]
        for item in metadata:
            latest=current.get(item['object_id'],{})
            status=latest.get('prediction_status','unavailable')
            state=by_object.get(item['object_id'])
            view={'id':item['object_id'],'name':item['object_name'],'channels_count':item['channels_count'],
                'object_type':item['object_type'],'operational_status':status,
                'risk_probability':latest.get('risk_probability') if status=='available' else None,
                'updated_at':latest.get('snapshot_ts'),'human_state':state}
            if search and search.casefold() not in (view['name']+' '+str(view['id'])).casefold():continue
            if attention=='none' and state is not None:continue
            if attention=='unavailable' and status=='available':continue
            if attention in order and state!=attention:continue
            items.append(view)
        items.sort(key=lambda item:(order.get(item['human_state'],3 if item['operational_status']=='available' else 4),item['name'],item['id']))
        return {'items':items,'total':len(items)}
    marks=','.join('?' for _ in ids)
    with repo._read():
        current={row['object_id']:dict(row) for row in repo.conn.execute(f'''SELECT * FROM (
          SELECT object_id,prediction_status,risk_probability,snapshot_ts,
            ROW_NUMBER() OVER(PARTITION BY object_id ORDER BY snapshot_ts DESC,identity DESC) n
          FROM prediction_history WHERE object_id IN ({marks})) WHERE n=1''',ids)}
        states=repo.conn.execute(f'''SELECT c.object_id,
          CASE WHEN EXISTS(SELECT 1 FROM maintenance_requests r WHERE r.card_id=c.card_id AND r.origin='signal' AND r.status IN ('new','assigned','in_progress','awaiting_review')) THEN 'inspection_requested'
          ELSE COALESCE(t.state,CASE d.decision WHEN 'monitoring' THEN 'monitoring' WHEN 'false_alarm' THEN 'resolved' ELSE 'new' END) END state
          FROM forecast_cards c LEFT JOIN signal_task_dispositions t ON t.card_id=c.card_id
          LEFT JOIN signal_decisions d ON d.id=(SELECT id FROM signal_decisions WHERE card_id=c.card_id ORDER BY work_version DESC LIMIT 1)
          WHERE c.object_id IN ({marks})''',ids).fetchall()
    by_object={}
    order={'new':0,'inspection_requested':1,'monitoring':2}
    for row in states:
        if row['state']!='resolved' and order[row['state']]<order.get(by_object.get(row['object_id']),9):by_object[row['object_id']]=row['state']
    items=[]
    for item in metadata:
        latest=current.get(item['object_id'],{})
        status=latest.get('prediction_status','unavailable')
        state=by_object.get(item['object_id'])
        view={'id':item['object_id'],'name':item['object_name'],'channels_count':item['channels_count'],'object_type':item['object_type'],
          'operational_status':status,'risk_probability':latest.get('risk_probability') if status=='available' else None,
          'updated_at':latest.get('snapshot_ts'),'human_state':state}
        if search and search.casefold() not in (view['name']+' '+str(view['id'])).casefold():continue
        if attention=='none' and state is not None:continue
        if attention=='unavailable' and status=='available':continue
        if attention in order and state!=attention:continue
        items.append(view)
    items.sort(key=lambda item:(order.get(item['human_state'],3 if item['operational_status']=='available' else 4),item['name'],item['id']))
    return {'items':items,'total':len(items)}


@router.get("/{object_id}", response_model=Object)
def get_object(object_id: int, user: AuthUser = Depends(get_current_user)):
    require_object_scope(user, object_id)
    obj = get_objects_by_id().get(object_id)
    if obj is None: raise HTTPException(404, "Object not found")
    return _object_view(obj)


@router.get("/{object_id}/planned-work")
def get_object_planned_work(
    object_id: int,
    request: Request,
    as_of: datetime | None = Query(default=None),
    upcoming_days: int = Query(default=30, ge=0, le=366),
    recent_days: int = Query(default=30, ge=0, le=366),
    user: AuthUser = Depends(get_current_user),
):
    """Read-only planned-work context; replay reads are bounded by virtual time."""
    require_object_scope(user, object_id)
    if object_id not in get_objects_by_id():
        raise HTTPException(404, "Object not found")
    replay_bound = telemetry_upper_bound(request)
    selected = as_of or replay_bound or datetime.now(timezone.utc)
    if replay_bound is not None and selected > replay_bound:
        selected = replay_bound
    return query_planned_work(object_id, selected, upcoming_days=upcoming_days,
                              recent_days=recent_days)


@router.get("/{object_id}/risk", response_model=RiskForecastResponse)
def get_object_risk(
    object_id: int,
    request: Request,
    snapshot_ts: datetime | None = Query(default=None, description="Canonical feature snapshot timestamp"),
    user: AuthUser = Depends(get_current_user),
):
    require_object_scope(user, object_id)
    if object_id not in get_objects_by_id():
        raise HTTPException(404, "Object not found")
    service = getattr(request.app.state, "risk_inference_service", None)
    if service is None:
        raise HTTPException(503, detail={"code": "model_unavailable", "message": "ML service is not initialized"})
    if snapshot_ts is not None and not is_canonical_snapshot(snapshot_ts):
        raise HTTPException(422, "snapshot_ts must be a canonical completed 6h snapshot")
    selected_snapshot = as_utc(snapshot_ts) if snapshot_ts is not None else latest_canonical_snapshot()
    result = service.predict(object_id, selected_snapshot)
    record_ml_inference_event(user, result)
    return result


def _history_response(record) -> OperationalPredictionHistory:
    return OperationalPredictionHistory(
        identity=record.identity, object_id=record.prediction.object_id,
        snapshot_ts=record.prediction.snapshot_ts, generated_at=record.prediction.generated_at,
        risk_probability=record.prediction.risk_probability, risk_flag=record.prediction.risk_flag,
        threshold=record.prediction.threshold, prediction_status=record.prediction.prediction_status,
        degraded_reason=record.prediction.degraded_reason, risk_score_kind=record.prediction.risk_score_kind,
        model_version=record.prediction.model_version,
        feature_schema_version=record.prediction.feature_schema_version,
        reference_snapshot_id=record.prediction.reference_snapshot_id,
        reference_hash=record.prediction.reference_hash,
        source_freshness_ts=record.prediction.source_freshness_ts, created_at=record.created_at,
    )


def _card_response(card) -> ForecastCard:
    return ForecastCard.model_validate(card.__dict__)


def _recommendation_response(recommendation) -> RecommendationResponse:
    return RecommendationResponse.model_validate({
        **recommendation.__dict__, "basis": list(recommendation.basis),
    })


@router.post("/{object_id}/risk/operational")
def run_operational_risk(
    object_id: int,
    request: Request,
    snapshot_ts: datetime = Query(..., description="Canonical completed 6h snapshot timestamp"),
    user: AuthUser = Depends(get_current_user),
):
    """Write-side canonical inference cycle; GET /risk remains read/replay-safe."""
    require_object_scope(user, object_id)
    if object_id not in get_objects_by_id():
        raise HTTPException(404, "Object not found")
    if not is_canonical_snapshot(snapshot_ts):
        raise HTTPException(422, "snapshot_ts must be a canonical completed 6h snapshot")
    inference = getattr(request.app.state, "risk_inference_service", None)
    forecast = getattr(request.app.state, "operational_forecast_service", None)
    if inference is None or forecast is None:
        raise HTTPException(503, detail={"code": "model_unavailable", "message": "ML service is not initialized"})
    result = inference.predict(object_id, as_utc(snapshot_ts))
    try:
        record, card, inserted = forecast.record(result)
    except OutOfOrderPredictionError as error:
        raise HTTPException(409, detail={"code": "out_of_order_snapshot", "message": str(error)}) from error
    except ProvenanceConflictError as error:
        raise HTTPException(409, detail={"code": "provenance_conflict", "message": str(error)}) from error
    record_ml_inference_event(user, result)
    recommendations = forecast.recommendations(object_id)
    recommendation = next((item for item in recommendations
                           if item.snapshot_ts == record.prediction.snapshot_ts), None)
    if inserted and recommendation is not None:
        record_recommendation_event(user, recommendation)
    return {"prediction": _history_response(record), "card": _card_response(card) if card else None,
            "recommendation": _recommendation_response(recommendation) if recommendation else None,
            "inserted": inserted}


@router.get("/{object_id}/risk/history", response_model=list[OperationalPredictionHistory])
def get_operational_risk_history(
    object_id: int, request: Request, limit: int = Query(100, ge=1, le=1000),
    user: AuthUser = Depends(get_current_user),
):
    require_object_scope(user, object_id)
    if object_id not in get_objects_by_id():
        raise HTTPException(404, "Object not found")
    forecast = getattr(request.app.state, "operational_forecast_service", None)
    if forecast is None:
        raise HTTPException(503, detail={"code": "history_unavailable", "message": "Prediction history is not initialized"})
    return [_history_response(item) for item in forecast.history(object_id, limit)]


@router.get("/{object_id}/risk/cards", response_model=list[ForecastCard])
def get_forecast_cards(
    object_id: int, request: Request, status: str | None = Query(None, pattern="^(active|closed)$"),
    user: AuthUser = Depends(get_current_user),
):
    require_object_scope(user, object_id)
    if object_id not in get_objects_by_id():
        raise HTTPException(404, "Object not found")
    forecast = getattr(request.app.state, "operational_forecast_service", None)
    if forecast is None:
        raise HTTPException(503, detail={"code": "history_unavailable", "message": "Prediction history is not initialized"})
    return [_card_response(item) for item in forecast.cards(object_id, status=status)]


@router.get("/{object_id}/risk/recommendations", response_model=list[RecommendationResponse])
def get_recommendations(
    object_id: int, request: Request, user: AuthUser = Depends(get_current_user),
):
    require_object_scope(user, object_id)
    if object_id not in get_objects_by_id():
        raise HTTPException(404, "Object not found")
    forecast = getattr(request.app.state, "operational_forecast_service", None)
    if forecast is None:
        raise HTTPException(503, detail={"code": "recommendation_unavailable", "message": "Recommendation service is not initialized"})
    return [_recommendation_response(item) for item in forecast.recommendations(object_id)]


@router.get("/{object_id}/channels", response_model=ChannelListResponse)
def get_object_channels(object_id: int, request: Request, user: AuthUser = Depends(get_current_user)):
    require_object_scope(user, object_id)
    if object_id not in get_objects_by_id(): raise HTTPException(404, "Object not found")
    channels = get_channels_by_object().get(object_id, [])
    channel_ids = [x["channel_id"] for x in channels]
    from backend.app.services.event_store import get_latest_channel_events
    from backend.app.services.telemetry_clock import telemetry_upper_bound
    latest_events=get_latest_channel_events(channel_ids,at_or_before=telemetry_upper_bound(request))
    risks = channel_risks(channel_ids)
    return {"items": [
        {**serialize_channel(x, risks[x["channel_id"]], latest_events.get(x["channel_id"],{}).get('timestamp')),
         'latest_value':latest_events.get(x['channel_id'],{}).get('value'),
         'latest_alarm':latest_events.get(x['channel_id'],{}).get('alarm')}
        for x in channels
    ], "total": len(channels)}
