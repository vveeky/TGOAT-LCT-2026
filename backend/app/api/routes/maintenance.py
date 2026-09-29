from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.config import utc_now
from backend.app.schemas import (
    MaintenanceCreate, MaintenanceListResponse, MaintenanceRequest,
    MaintenanceStatus, MaintenanceUpdate,
)
from backend.app.services.mock_storage import prediction_repository
from backend.app.api.routes.signals import workflow
from backend.app.services.dispatcher_workflow import WorkflowConflict, WorkflowNotFound
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from backend.app.services.auth import effective_directory_users, user_can_access_object
from backend.app.services.reference_data import get_channels_by_id, get_objects_by_id
from backend.app.services.auth import AuthUser, accessible_object_ids, get_current_user, require_object_scope, require_role
from backend.app.services.audit import record_audit_event

router = APIRouter(prefix="/maintenance-requests", tags=["Maintenance"])


@router.get("", response_model=MaintenanceListResponse)
def get_maintenance_requests(
    request: Request,
    status: MaintenanceStatus | None = None, priority: str | None = None, object_id: int | None = None,
    channel_id: int | None = None, prediction_id: int | None = None, assignee_user_id: str | None = None, active_only: bool = False,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
    user: AuthUser = Depends(get_current_user),
):
    items, total = workflow(request).filter(
        status=status, priority=priority, object_id=object_id, channel_id=channel_id,
        prediction_id=prediction_id, object_ids=accessible_object_ids(user), viewer=user, assignee_user_id=assignee_user_id, active_only=active_only,
        limit=limit, offset=offset,
    )
    objects=get_objects_by_id()
    return {"items": [{**item,"object_name":objects.get(item['object_id'],{}).get('object_name') or 'Название объекта недоступно'} for item in items], "total": total}


def _validate_references(body: MaintenanceCreate) -> None:
    if body.object_id not in get_objects_by_id(): raise HTTPException(404, "Object not found")
    if body.channel_id is not None:
        channel = get_channels_by_id().get(body.channel_id)
        if channel is None: raise HTTPException(404, "Channel not found")
        if channel["object_id"] != body.object_id:
            raise HTTPException(409, "Channel does not belong to object")
    if body.prediction_id is not None:
        prediction = prediction_repository.get(body.prediction_id)
        if prediction is None: raise HTTPException(404, "Prediction not found")
        if prediction["object_id"] != body.object_id:
            raise HTTPException(409, "Prediction does not belong to object")
        if (prediction["scope_type"] == "channel" and body.channel_id is not None
                and prediction["channel_id"] != body.channel_id):
            raise HTTPException(409, "Prediction does not belong to channel")


@router.post("", response_model=MaintenanceRequest, status_code=201)
def create_maintenance_request(body: MaintenanceCreate, request: Request, user: AuthUser = Depends(get_current_user)):
    require_role(user, "technician", "district_dispatcher", "central_dispatcher")
    require_object_scope(user, body.object_id)
    _validate_references(body)
    timestamp = utc_now().isoformat()
    item = workflow(request).create({
        "created_by_user_id": user.user_id, "created_by_role": user.role,
        **body.model_dump(), "status": "draft", "source": "local",
        "external_request_id": None, "sync_status": "not_synced", "external_status": None,
        "created_at": timestamp, "updated_at": timestamp,
    })
    record_audit_event(user, "maintenance_request.created", "maintenance_request",
                       item["id"], item["object_id"],
                       new_state={"status": item["status"], "version": item["version"]})
    return item


@router.patch("/{request_id}", response_model=MaintenanceRequest)
def update_maintenance_request(request_id: int, body: MaintenanceUpdate,
                               request: Request,
                               user: AuthUser = Depends(get_current_user)):
    require_role(user, "district_dispatcher", "central_dispatcher")
    repository = workflow(request)
    existing = repository.get(request_id)
    if not repository.request_visible(existing,user): raise HTTPException(404, "Maintenance request not found")
    require_object_scope(user, existing["object_id"])
    try:
        item = repository.update(request_id, {**body.model_dump(exclude_unset=True), "updated_at": utc_now().isoformat()})
    except WorkflowConflict as error:
        raise HTTPException(409, str(error)) from error
    record_audit_event(user, "maintenance_request.updated", "maintenance_request",
                       request_id, item["object_id"])
    return item


@router.get('/eligible-technicians')
def eligible_technicians(object_id: int, request: Request, user: AuthUser = Depends(get_current_user)):
    require_role(user,'central_dispatcher','district_dispatcher')
    require_object_scope(user,object_id)
    directory = effective_directory_users(request)
    return [{'user_id':u.user_id,'username':u.username,'role':'technician'}
            for u in directory if u.has_role('technician') and user_can_access_object(u,object_id)]


@router.get("/{request_id}")
def maintenance_detail(request_id: int, request: Request, user: AuthUser = Depends(get_current_user)):
    try:
        return workflow(request).request_detail(request_id,user)
    except WorkflowNotFound as error:
        raise HTTPException(404,'Заявка не найдена') from error


class RequestCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1,max_length=200)
    assignee_user_id: str | None = Field(default=None,max_length=200)
    result_code: Literal['issue_found','no_issue_found','needs_followup','unable_to_verify'] | None = None
    summary: str | None = Field(default=None,max_length=4000)
    comment: str | None = Field(default=None,max_length=4000)


@router.post('/{request_id}/{action}')
def execute_request_command(request_id: int, action: Literal['submit','assign','start','result','accept','return','cancel'],
                            body: RequestCommand, request: Request, user: AuthUser = Depends(get_current_user)):
    try:
        result = workflow(request).request_command(request_id,action,user,body.model_dump(),
                                                   directory=effective_directory_users(request))
        record_audit_event(user,f'maintenance_request.{action}','maintenance_request',request_id,result['object_id'],
                           new_state={"status": result["status"], "version": result["version"],
                                      "assignee_user_id": result.get("assignee_user_id")})
        return result
    except WorkflowNotFound as error:
        raise HTTPException(404,'Заявка не найдена') from error
    except WorkflowConflict as error:
        raise HTTPException(409,{'code':'request_conflict','message':str(error)}) from error
