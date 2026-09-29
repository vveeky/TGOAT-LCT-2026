from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.config import as_utc, utc_now
from backend.app.schemas import (
    Prediction, PredictionListResponse, PredictionOutcome, PredictionOutcomeCreate,
    PredictionOutcomeListResponse, PredictionReviewListResponse, PredictionReviewRequest,
    PredictionStatus, RiskLevel, ScopeType, SuccessResponse,
)
from backend.app.services.mock_storage import (
    prediction_outcome_repository, prediction_repository, prediction_review_repository,
)
from backend.app.services.reference_data import get_channels_data
from backend.app.services.auth import AuthUser, accessible_object_ids, get_current_user, require_object_scope, require_role
from backend.app.services.audit import record_audit_event

router = APIRouter(prefix="/predictions", tags=["Predictions"])


@router.get("", response_model=PredictionListResponse)
def get_predictions(
    from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None,
    object_id: int | None = None, channel_id: int | None = None,
    sensor_type: str | None = None, scope_type: ScopeType | None = None,
    incident_type: str | None = None, risk_level: RiskLevel | None = None,
    status: PredictionStatus | None = None, limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0), user: AuthUser = Depends(get_current_user),
):
    if from_ is not None and to is not None and as_utc(to) < as_utc(from_):
        raise HTTPException(422, "to must be greater than or equal to from")
    channel_ids = None
    if sensor_type is not None:
        channel_ids = [x["channel_id"] for x in get_channels_data() if x["sensor_type"] == sensor_type]
    items, total = prediction_repository.filter(
        from_timestamp=from_, to_timestamp=to, object_id=object_id, channel_id=channel_id,
        channel_ids=channel_ids, scope_type=scope_type, incident_type=incident_type,
        risk_level=risk_level, status=status, object_ids=accessible_object_ids(user),
        limit=limit, offset=offset,
    )
    return {"items": items, "total": total}


def _require_prediction(prediction_id: int, user: AuthUser) -> dict:
    item = prediction_repository.get(prediction_id)
    if item is None: raise HTTPException(404, "Prediction not found")
    require_object_scope(user, item["object_id"])
    return item


@router.get("/{prediction_id}", response_model=Prediction)
def get_prediction(prediction_id: int, user: AuthUser = Depends(get_current_user)):
    return _require_prediction(prediction_id, user)


@router.post("/{prediction_id}/close", response_model=Prediction)
def close_prediction(prediction_id: int, user: AuthUser = Depends(get_current_user)):
    require_role(user, "district_dispatcher", "central_dispatcher")
    previous = _require_prediction(prediction_id, user)
    item = prediction_repository.close(prediction_id)
    if item is None:
        raise HTTPException(404, "Prediction not found")
    if previous["status"] == "active" and item["status"] == "closed":
        record_audit_event(user, "prediction.closed", "prediction", prediction_id, item["object_id"])
    return item


@router.get("/{prediction_id}/reviews", response_model=PredictionReviewListResponse)
def get_prediction_reviews(prediction_id: int, user: AuthUser = Depends(get_current_user)):
    _require_prediction(prediction_id, user)
    items = prediction_review_repository.list_for_prediction(prediction_id)
    return {"items": items, "total": len(items)}


@router.patch("/{prediction_id}/review", response_model=SuccessResponse)
def review_prediction(prediction_id: int, body: PredictionReviewRequest,
                      user: AuthUser = Depends(get_current_user)):
    require_role(user, "technician", "district_dispatcher", "central_dispatcher")
    _require_prediction(prediction_id, user)
    prediction_review_repository.add({
        "prediction_id": prediction_id, "decision": body.dispatcher_result,
        "reason_code": body.reason_code, "comment": body.comment,
        "reviewed_by": user.username, "reviewed_at": utc_now().isoformat(),
    })
    prediction_repository.set_latest_review(prediction_id, body.dispatcher_result)
    record_audit_event(user, "prediction.review.created", "prediction", prediction_id,
                       _require_prediction(prediction_id, user)["object_id"])
    return {"success": True}


@router.post("/{prediction_id}/outcomes", response_model=PredictionOutcome, status_code=201)
def create_prediction_outcome(prediction_id: int, body: PredictionOutcomeCreate,
                              user: AuthUser = Depends(get_current_user)):
    require_role(user, "district_dispatcher", "central_dispatcher")
    prediction = _require_prediction(prediction_id, user)
    outcome = prediction_outcome_repository.add({
        "prediction_id": prediction_id, **body.model_dump(),
        "confirmed_by": user.username,
        "confirmed_at": utc_now().isoformat(),
    })
    record_audit_event(user, "prediction.outcome.confirmed", "prediction", prediction_id,
                       prediction["object_id"])
    return outcome


@router.get("/{prediction_id}/outcomes", response_model=PredictionOutcomeListResponse)
def get_prediction_outcomes(prediction_id: int, user: AuthUser = Depends(get_current_user)):
    _require_prediction(prediction_id, user)
    items = prediction_outcome_repository.list_for_prediction(prediction_id)
    return {"items": items, "total": len(items)}
