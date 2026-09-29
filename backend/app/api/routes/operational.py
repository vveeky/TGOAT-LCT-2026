from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.schemas import (
    OperationalDashboardResponse,
    OperationalOutcomeRequest,
    OperationalOutcomeResponse,
    OperationalPredictionJournalItem,
    OperationalPredictionJournalResponse,
    OperationalReviewRequest,
    OperationalReviewResponse,
    OperationalRiskStateResponse,
    RecommendationResponse,
)
from backend.app.services.auth import (
    AuthUser,
    accessible_object_ids,
    get_current_user,
    require_object_scope,
    require_role,
    user_can_access_object,
)
from backend.app.services.reference_data import get_objects_by_id
from backend.app.services.operational_actions import (
    OperationalActionIdempotencyConflict,
    OperationalPredictionNotActionable,
    OperationalPredictionNotFound,
)
from backend.app.services.audit import record_audit_event
from backend.app.services.xml_exchange import prefers_xml, xml_response


router = APIRouter(prefix="/operational", tags=["Operational ML"])


@router.get('/history')
def user_history(request: Request, object_id: int | None = None, prediction_identity: str | None = None,
                 from_: str | None = Query(None,alias='from'), to: str | None = None,
                 state: str | None = Query(None,pattern='^(new|monitoring|inspection_requested|resolved)$'),
                 limit: int = Query(50,ge=1,le=100), offset: int = Query(0,ge=0),
                 user: AuthUser = Depends(get_current_user)):
    from datetime import datetime
    from backend.app.config import as_utc
    from backend.app.api.routes.signals import workflow
    if state and not user.has_capability('signals.read'):
        raise HTTPException(403, 'Signal workflow is available to dispatchers only')
    metadata=get_objects_by_id()
    ids=[key for key,value in metadata.items() if value['has_channels'] and user_can_access_object(user,key)]
    if object_id is not None:
        require_object_scope(user,object_id)
        ids=[object_id] if object_id in ids else []
    if not ids:return {'items':[],'total':0}
    try:
        start=as_utc(datetime.fromisoformat(from_)) if from_ else None
        end=as_utc(datetime.fromisoformat(to)) if to else None
    except ValueError as error:raise HTTPException(422,'Invalid period') from error
    if start and end and start>end:raise HTTPException(422,'Invalid period')
    repo=workflow(request)
    history_projection=getattr(repo,'operational_history_projection',None)
    if callable(history_projection):
        rows,total,activity=history_projection(object_ids=ids,prediction_identity=prediction_identity,
            start=start,end=end,state=state,limit=limit,offset=offset,user=user,
            activity_object_id=object_id)
        for item in rows:
            item['object_name']=metadata[item['object_id']]['object_name']
        return {'items':rows,'total':total,'activity':activity}
    marks=','.join('?' for _ in ids)
    filters=[f'p.object_id IN ({marks})'];args=list(ids)
    if prediction_identity:filters.append('p.identity=?');args.append(prediction_identity)
    if start:filters.append('p.snapshot_ts>=?');args.append(start.isoformat())
    if end:filters.append('p.snapshot_ts<=?');args.append(end.isoformat())
    query=f'''SELECT p.identity,p.object_id,p.snapshot_ts,p.prediction_status,p.risk_probability,m.card_id,
      d.decision,
      CASE WHEN m.card_id IS NULL THEN NULL
        WHEN EXISTS(SELECT 1 FROM maintenance_requests a WHERE a.card_id=m.card_id AND a.origin='signal' AND a.status IN ('new','assigned','in_progress','awaiting_review')) THEN 'inspection_requested'
        ELSE COALESCE(t.state,CASE d.decision WHEN 'monitoring' THEN 'monitoring' WHEN 'false_alarm' THEN 'resolved' ELSE 'new' END) END signal_state,
      r.id request_id,r.status request_status
      FROM prediction_history p LEFT JOIN forecast_card_predictions m ON m.prediction_identity=p.identity
      LEFT JOIN signal_decisions d ON d.id=(SELECT id FROM signal_decisions WHERE card_id=m.card_id ORDER BY work_version DESC LIMIT 1)
      LEFT JOIN signal_task_dispositions t ON t.card_id=m.card_id
      LEFT JOIN maintenance_requests r ON r.id=(SELECT id FROM maintenance_requests WHERE card_id=m.card_id ORDER BY id DESC LIMIT 1)
      WHERE {' AND '.join(filters)}'''
    query='SELECT * FROM ('+query+')'
    if state:query+=' WHERE signal_state=?';args.append(state)
    with repo._read():
        total=repo.conn.execute('SELECT COUNT(*) FROM ('+query+')',args).fetchone()[0]
        rows=repo.conn.execute(query+' ORDER BY snapshot_ts DESC,identity DESC LIMIT ? OFFSET ?',[*args,limit,offset]).fetchall()
        # Links follow the accepted task visibility, not just object scope.
        visibility_role = 'technician' if user.has_role('technician') and not user.has_role('central_dispatcher','district_dispatcher') and not user.has_capability('management.read') else 'dispatcher'
        visible_ids={row['id'] for row in repo.conn.execute("SELECT id FROM maintenance_requests WHERE (status!='draft' OR created_by_user_id=?) AND (?!='technician' OR (status='draft' AND created_by_user_id=?) OR (status!='draft' AND assignee_user_id=?))",(user.user_id,visibility_role,user.user_id,user.user_id))}
        activity=[]
        if object_id is not None:
            activity=[dict(row) for row in repo.conn.execute('''
                SELECT e.event,e.timestamp,e.comment,e.to_status,e.request_id
                FROM maintenance_events e JOIN maintenance_requests r ON r.id=e.request_id
                WHERE r.object_id=? AND (r.status!='draft' OR r.created_by_user_id=?)
                AND (?!='technician' OR (r.status='draft' AND r.created_by_user_id=?)
                    OR (r.status!='draft' AND r.assignee_user_id=?))
                ORDER BY e.timestamp DESC,e.id DESC LIMIT 20
            ''',(object_id,user.user_id,visibility_role,user.user_id,user.user_id))]
            if user.has_capability('signals.read'):
                activity.extend(dict(row) for row in repo.conn.execute('''
                    SELECT 'signal_created' event,first_positive_snapshot_ts timestamp,
                        NULL comment,NULL to_status,NULL request_id,card_id
                    FROM forecast_cards WHERE object_id=? ORDER BY first_positive_snapshot_ts DESC LIMIT 20
                ''',(object_id,)))
                activity.extend(dict(row) for row in repo.conn.execute('''
                    SELECT 'decision' event,created_at timestamp,comment,decision to_status,
                        NULL request_id,card_id
                    FROM signal_decisions WHERE object_id=? ORDER BY created_at DESC,id DESC LIMIT 20
                ''',(object_id,)))
            activity=sorted(activity,key=lambda item:item['timestamp'],reverse=True)[:20]
    items=[]
    for row in rows:
        item=dict(row);item['object_name']=metadata[item['object_id']]['object_name']
        if item['request_id'] not in visible_ids:item['request_id']=None;item['request_status']=None
        if not user.has_capability('signals.read'):
            item['card_id']=None;item['decision']=None;item['signal_state']=None
        items.append(item)
    return {'items':items,'total':total,'activity':activity}


def _recommendation(value):
    if value is None:
        return None
    return RecommendationResponse.model_validate({**value.__dict__, "basis": list(value.basis)})


def _state(value):
    return OperationalRiskStateResponse.model_validate({
        **value.__dict__, "card": value.card.__dict__ if value.card else None,
        "recommendation": _recommendation(value.recommendation),
    })


@router.get(
    "/dashboard",
    response_model=OperationalDashboardResponse,
    responses={200: {"content": {"application/xml": {"schema": {"type": "string"}}}}},
)
def get_operational_dashboard(request: Request, user: AuthUser = Depends(get_current_user)):
    service = getattr(request.app.state, "operational_read_service", None)
    if service is None:
        raise HTTPException(503, detail={"code": "operational_read_unavailable", "message": "Operational read model is not initialized"})
    operational_ids = {
        object_id for object_id, item in get_objects_by_id().items() if item["has_channels"]
    }
    scoped = accessible_object_ids(user)
    ids = sorted(operational_ids if scoped is None else operational_ids.intersection(scoped))
    dashboard = service.dashboard(ids)
    payload = {
        "cycle_status": dashboard.cycle_status if dashboard.cycle_status in {"completed", "stale", "failed", "unavailable", "no_cycle"} else "unavailable",
        "stale": dashboard.stale,
        "cycle_id": dashboard.cycle.cycle_id if dashboard.cycle else None,
        "cycle_completed_at": dashboard.cycle.finished_at if dashboard.cycle and dashboard.cycle.status == "completed" else None,
        "latest_attempted_snapshot_ts": dashboard.cycle.snapshot_ts if dashboard.cycle else None,
        "latest_attempted_status": dashboard.cycle.status if dashboard.cycle else None,
        "snapshot_ts": dashboard.snapshot_ts,
        "object_count": dashboard.object_count, "available_count": dashboard.available_count,
        "positive_count": dashboard.positive_count, "negative_count": dashboard.negative_count,
        "degraded_count": dashboard.degraded_count, "no_prediction_count": dashboard.no_prediction_count,
        "items": [_state(item) for item in dashboard.items],
    }
    if prefers_xml(request.headers.get("accept")):
        validated = OperationalDashboardResponse.model_validate(payload)
        return xml_response(validated, "operational_dashboard")
    return payload


@router.get("/predictions", response_model=OperationalPredictionJournalResponse)
def get_operational_prediction_journal(request: Request,
                                      limit: int = Query(100, ge=1, le=500),
                                      offset: int = Query(0, ge=0),
                                      user: AuthUser = Depends(get_current_user)):
    service = getattr(request.app.state, "operational_read_service", None)
    if service is None:
        raise HTTPException(503, detail={"code": "operational_read_unavailable",
                                         "message": "Operational read model is not initialized"})
    all_operational_ids = {
        object_id for object_id, item in get_objects_by_id().items() if item["has_channels"]
    }
    scoped = accessible_object_ids(user)
    object_ids = sorted(all_operational_ids if scoped is None
                        else all_operational_ids.intersection(scoped))
    records, total = service.forecast_service.global_history(
        object_ids=object_ids, limit=limit, offset=offset,
    )
    action_repository = getattr(request.app.state, "operational_action_repository", None)
    summaries = (action_repository.summaries([record.identity for record in records])
                 if action_repository is not None else {})
    items = []
    for record in records:
        prediction = record.prediction
        summary = summaries.get(record.identity, {
            "review_count": 0, "latest_review_decision": None,
            "outcome_count": 0, "latest_actual_outcome": None,
        })
        items.append(OperationalPredictionJournalItem(
            identity=record.identity, object_id=prediction.object_id,
            snapshot_ts=prediction.snapshot_ts, generated_at=prediction.generated_at,
            prediction_status=prediction.prediction_status,
            risk_probability=prediction.risk_probability, risk_flag=prediction.risk_flag,
            risk_score_kind="ml", model_version=prediction.model_version,
            feature_schema_version=prediction.feature_schema_version, **summary,
        ))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/predictions/{identity}", response_model=OperationalPredictionJournalItem)
def get_operational_prediction_journal_item(identity: str, request: Request,
                                            user: AuthUser = Depends(get_current_user)):
    service = getattr(request.app.state, "operational_read_service", None)
    if service is None:
        raise HTTPException(503, detail={"code": "operational_read_unavailable",
                                         "message": "Operational read model is not initialized"})
    action_repository = _actions(request)
    _prediction_for_read(action_repository, identity, user)
    record = service.forecast_service.get(identity)
    if record is None:
        raise HTTPException(404, "Operational prediction not found")
    prediction = record.prediction
    summary = action_repository.summaries([identity])[identity]
    return OperationalPredictionJournalItem(
        identity=record.identity, object_id=prediction.object_id,
        snapshot_ts=prediction.snapshot_ts, generated_at=prediction.generated_at,
        prediction_status=prediction.prediction_status,
        risk_probability=prediction.risk_probability, risk_flag=prediction.risk_flag,
        risk_score_kind="ml", model_version=prediction.model_version,
        feature_schema_version=prediction.feature_schema_version, **summary,
    )


@router.get("/objects/{object_id}", response_model=OperationalRiskStateResponse)
def get_operational_object(object_id: int, request: Request, user: AuthUser = Depends(get_current_user)):
    require_object_scope(user, object_id)
    if object_id not in get_objects_by_id():
        raise HTTPException(404, "Object not found")
    service = getattr(request.app.state, "operational_read_service", None)
    if service is None:
        raise HTTPException(503, detail={"code": "operational_read_unavailable", "message": "Operational read model is not initialized"})
    return _state(service.object_state(object_id))


def _actions(request: Request):
    repository = getattr(request.app.state, "operational_action_repository", None)
    if repository is None:
        raise HTTPException(503, detail={"code": "operational_actions_unavailable",
                                         "message": "Operational decision storage is not initialized"})
    return repository


def _prediction_for_action(repository, identity: str, user: AuthUser) -> dict:
    prediction = repository.prediction(identity)
    if prediction is None:
        raise HTTPException(404, "Operational prediction not found")
    require_object_scope(user, int(prediction["object_id"]))
    if prediction["prediction_status"] != "available":
        raise HTTPException(
            409,
            detail={"code": "prediction_not_actionable",
                    "message": "Only available operational predictions can be reviewed or receive an outcome"},
        )
    return prediction


def _prediction_for_read(repository, identity: str, user: AuthUser) -> dict:
    prediction = repository.prediction(identity)
    if prediction is None:
        raise HTTPException(404, "Operational prediction not found")
    require_object_scope(user, int(prediction["object_id"]))
    return prediction


def _action_error(error: Exception):
    if isinstance(error, OperationalPredictionNotFound):
        raise HTTPException(404, "Operational prediction not found") from error
    if isinstance(error, OperationalPredictionNotActionable):
        raise HTTPException(409, detail={"code": "prediction_not_actionable",
                                        "message": str(error)}) from error
    if isinstance(error, OperationalActionIdempotencyConflict):
        raise HTTPException(409, detail={"code": "idempotency_conflict", "message": str(error)}) from error
    raise error


@router.get("/predictions/{identity}/reviews", response_model=list[OperationalReviewResponse])
def get_operational_reviews(identity: str, request: Request,
                            user: AuthUser = Depends(get_current_user)):
    repository = _actions(request)
    _prediction_for_read(repository, identity, user)
    try:
        return repository.reviews(identity)
    except OperationalPredictionNotFound as error:
        _action_error(error)


@router.post("/predictions/{identity}/review", response_model=OperationalReviewResponse, status_code=201)
def create_operational_review(identity: str, body: OperationalReviewRequest, request: Request,
                              user: AuthUser = Depends(get_current_user)):
    require_role(user, "technician", "district_dispatcher", "central_dispatcher")
    repository = _actions(request)
    prediction = _prediction_for_action(repository, identity, user)
    if body.decision != 'confirmed':
        raise HTTPException(409, detail={"code": "use_signal_command",
            "message": "Сохраните решение через сигнал с проверкой версии и связанной заявкой"})
    try:
        result = repository.add_review(
            identity, int(prediction["object_id"]), actor_user_id=user.user_id,
            actor_role=user.role, decision=body.decision, reason_code=body.reason_code,
            comment=body.comment, idempotency_key=body.idempotency_key,
        )
    except (OperationalPredictionNotFound, OperationalPredictionNotActionable,
            OperationalActionIdempotencyConflict) as error:
        _action_error(error)
    record_audit_event(user, "operational_prediction.reviewed", "operational_prediction",
                       identity, int(prediction["object_id"]))
    return result


@router.get("/predictions/{identity}/outcomes", response_model=list[OperationalOutcomeResponse])
def get_operational_outcomes(identity: str, request: Request,
                             user: AuthUser = Depends(get_current_user)):
    repository = _actions(request)
    _prediction_for_read(repository, identity, user)
    try:
        return repository.outcomes(identity)
    except OperationalPredictionNotFound as error:
        _action_error(error)


@router.post("/predictions/{identity}/outcomes", response_model=OperationalOutcomeResponse, status_code=201)
def create_operational_outcome(identity: str, body: OperationalOutcomeRequest, request: Request,
                               user: AuthUser = Depends(get_current_user)):
    if not user.has_role("district_dispatcher", "central_dispatcher"):
        raise HTTPException(403, "Dispatcher role is required")
    repository = _actions(request)
    prediction = _prediction_for_action(repository, identity, user)
    try:
        result = repository.add_outcome(
            identity, int(prediction["object_id"]), actor_user_id=user.user_id,
            actor_role=user.role, actual_outcome=body.actual_outcome,
            reason_code=body.reason_code, comment=body.comment,
            idempotency_key=body.idempotency_key,
        )
    except (OperationalPredictionNotFound, OperationalPredictionNotActionable,
            OperationalActionIdempotencyConflict) as error:
        _action_error(error)
    record_audit_event(user, "operational_prediction.outcome_recorded", "operational_prediction",
                       identity, int(prediction["object_id"]))
    return result
