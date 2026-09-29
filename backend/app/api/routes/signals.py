from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.services.auth import AuthUser, accessible_object_ids, get_current_user, require_object_scope, require_role
from backend.app.services.dispatcher_workflow import WorkflowConflict, WorkflowNotFound
from backend.app.services.reference_data import get_objects_by_id
from backend.app.services.audit import audit_actor_context, record_audit_event

router = APIRouter(prefix='/signals', tags=['Dispatcher workflow'])


class InspectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=8, max_length=2000)
    priority: Literal['low', 'normal', 'high', 'urgent'] = 'normal'

    @field_validator('reason')
    @classmethod
    def reason_must_be_meaningful(cls, value):
        if len(value.strip()) < 8:
            raise ValueError('Inspection reason must contain at least 8 characters')
        return value


class SignalDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    decision: Literal['monitoring', 'inspection_requested', 'false_alarm']
    prediction_identity: str = Field(min_length=1, max_length=500)
    reason_code: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=4000)
    request: InspectionRequest | None = None

    @model_validator(mode='after')
    def false_alarm_requires_explanation(self):
        if self.decision == 'false_alarm' and len((self.comment or '').strip()) < 8:
            raise ValueError('False-alarm explanation must contain at least 8 characters')
        return self


def workflow(request: Request):
    repository = getattr(request.app.state, 'dispatcher_workflow', None)
    if repository is None:
        raise HTTPException(503, 'Хранилище решений временно недоступно')
    return repository


@router.get('')
def list_signals(request: Request, state: Literal['new', 'monitoring', 'inspection_requested', 'resolved'] | None = None,
                 object_id: int | None = None, limit: int = Query(100, ge=1, le=500),
                 offset: int = Query(0, ge=0), user: AuthUser = Depends(get_current_user)):
    require_role(user, 'central_dispatcher', 'district_dispatcher')
    return workflow(request).signals(object_ids=accessible_object_ids(user), state=state,
                                     object_id=object_id, limit=limit, offset=offset)


@router.get('/monitoring')
def monitoring(request: Request, user: AuthUser = Depends(get_current_user)):
    require_role(user, 'central_dispatcher', 'district_dispatcher')
    repo = workflow(request)
    allowed = accessible_object_ids(user)
    projected = getattr(repo, 'monitoring_projection', None)
    if callable(projected):
        return projected(allowed)
    with repo.lock:
        # A single read transaction provides one coherent business population.
        repo.conn.execute('BEGIN')
        try:
            total = repo.conn.execute('SELECT COUNT(*) FROM forecast_cards').fetchone()[0]
            projection = repo.signals(object_ids=allowed, limit=total)
            objects = []
            for object_id, item in get_objects_by_id().items():
                if not item['has_channels'] or allowed is not None and object_id not in allowed:
                    continue
                latest = repo.conn.execute('SELECT * FROM prediction_history WHERE object_id=? ORDER BY snapshot_ts DESC LIMIT 1',
                                           (object_id,)).fetchone()
                objects.append({'id': object_id, 'name': item['object_name'],
                    'operational_status': latest['prediction_status'] if latest else 'unavailable',
                    'risk_probability': latest['risk_probability'] if latest else None,
                    'risk_flag': bool(latest['risk_flag']) if latest and latest['risk_flag'] is not None else None,
                    'updated_at': latest['snapshot_ts'] if latest else None})
            repo.conn.commit()
            return {**projection, 'objects': objects}
        except Exception:
            repo.conn.rollback()
            raise


@router.get('/{card_id}')
def signal_detail(card_id: str, request: Request, user: AuthUser = Depends(get_current_user)):
    require_role(user, 'central_dispatcher', 'district_dispatcher')
    try:
        item = workflow(request).signal(card_id)
    except WorkflowNotFound as error:
        raise HTTPException(404, 'Сигнал не найден') from error
    require_object_scope(user, item['object_id'])
    return item


@router.post('/{card_id}/decision')
def decide_signal(card_id: str, body: SignalDecision, request: Request,
                  user: AuthUser = Depends(get_current_user)):
    require_role(user, 'central_dispatcher', 'district_dispatcher')
    try:
        result = workflow(request).decide(
            card_id, actor_user_id=user.user_id, actor_role=user.role,
            payload=body.model_dump(), allowed_object_ids=accessible_object_ids(user),
            audit_context=audit_actor_context(user),
        )
        signal = result['signal']
        request_item = result.get('request')
        metadata = {'decision_id': result['decision_id'],
                    'prediction_identity': body.prediction_identity}
        if request_item is not None:
            metadata['maintenance_request_id'] = request_item['id']
        record_audit_event(user, 'signal.decision_command', 'signal', card_id, signal['object_id'],
                           new_state={'state': signal['state'], 'decision': body.decision,
                                      'work_version': result['work_version']},
                           metadata=metadata)
        return result
    except WorkflowNotFound as error:
        raise HTTPException(404, 'Сигнал не найден') from error
    except WorkflowConflict as error:
        raise HTTPException(409, {'code': 'workflow_conflict', 'message': str(error)}) from error
