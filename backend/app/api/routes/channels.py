from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from backend.app.services.telemetry_clock import telemetry_upper_bound

from backend.app.schemas import Channel, ChannelHistoryResponse, PredictionListResponse
from backend.app.config import as_utc
from backend.app.services.api_serializers import serialize_channel
from backend.app.services.event_store import HistoryStorageUnavailableError
from backend.app.services.mock_storage import event_history_repository, prediction_repository
from backend.app.services.reference_data import get_channels_by_id
from backend.app.services.risk_state import channel_risks
from backend.app.services.auth import AuthUser, get_current_user, require_object_scope

router = APIRouter(prefix="/channels", tags=["Channels"])


@router.get("/{channel_id}", response_model=Channel)
def get_channel(channel_id: int, request: Request, user: AuthUser = Depends(get_current_user)):
    channel = get_channels_by_id().get(channel_id)
    if channel is None: raise HTTPException(404, "Channel not found")
    if channel.get("object_id") is not None:
        require_object_scope(user, channel["object_id"])
    elif user.scope_roots is not None:
        raise HTTPException(404, "Channel not found")
    from backend.app.services.event_store import get_latest_channel_events
    latest=get_latest_channel_events([channel_id],at_or_before=telemetry_upper_bound(request)).get(channel_id,{})
    return {**serialize_channel(channel, channel_risks([channel_id])[channel_id], latest.get('timestamp')),
            'latest_value':latest.get('value'),'latest_alarm':latest.get('alarm')}


@router.get("/{channel_id}/history", response_model=ChannelHistoryResponse)
def get_channel_history(
    channel_id: int,
    request: Request,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    alarm_only: bool = False,
    offset: int = Query(default=0, ge=0, le=100000),
    user: AuthUser = Depends(get_current_user),
):
    channel = get_channels_by_id().get(channel_id)
    if channel is None: raise HTTPException(404, "Channel not found")
    if channel.get("object_id") is not None:
        require_object_scope(user, channel["object_id"])
    elif user.scope_roots is not None:
        raise HTTPException(404, "Channel not found")
    if from_ is not None and to is not None and as_utc(to) < as_utc(from_):
        raise HTTPException(422, "to must be greater than or equal to from")
    bound=telemetry_upper_bound(request)
    if bound is not None:
        to=min(as_utc(to),bound) if to is not None else bound
        if from_ is not None and as_utc(from_)>to:
            return {'channel_id':channel_id,'events':[]}
    try:
        items = event_history_repository.get_channel_history(channel_id, from_, to, limit, alarm_only=alarm_only, offset=offset)
    except HistoryStorageUnavailableError as error:
        raise HTTPException(503, detail={"code": "event_history_unavailable", "message": str(error)})
    return {"channel_id": channel_id, "events": [
        {"timestamp": x["timestamp"], "alarm": x["is_alarm"], "value": x["value"]}
        for x in items
    ]}


@router.get("/{channel_id}/predictions", response_model=PredictionListResponse)
def get_channel_predictions(channel_id: int, user: AuthUser = Depends(get_current_user)):
    channel = get_channels_by_id().get(channel_id)
    if channel is None: raise HTTPException(404, "Channel not found")
    if channel.get("object_id") is not None:
        require_object_scope(user, channel["object_id"])
    elif user.scope_roots is not None:
        raise HTTPException(404, "Channel not found")
    items, total = prediction_repository.filter(channel_id=channel_id, limit=None, offset=0)
    return {"items": items, "total": total}
