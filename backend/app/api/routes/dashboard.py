from fastapi import APIRouter, Depends

from backend.app.schemas import DashboardResponse
from backend.app.services.mock_storage import maintenance_repository, prediction_repository
from backend.app.services.reference_data import get_objects_by_id, get_reference_meta
from backend.app.services.api_serializers import serialize_object
from backend.app.services.risk_state import active_request_counts, object_risks
from backend.app.services.auth import AuthUser, accessible_object_ids, get_current_user, user_can_access_object

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("", response_model=DashboardResponse)
def get_dashboard(user: AuthUser = Depends(get_current_user)):
    scoped_object_ids = accessible_object_ids(user)
    predictions, _ = prediction_repository.filter(object_ids=scoped_object_ids, limit=None, offset=0)
    predictions.sort(key=lambda x: (x["created_at"], x["id"]), reverse=True)
    distribution = {level: sum(x["status"] == "active" and x["risk_level"] == level
                               for x in predictions)
                    for level in ("low", "medium", "high", "critical")}
    object_ids = [x for x in prediction_repository.active_object_ids()
                  if scoped_object_ids is None or x in scoped_object_ids]
    maintenance_items, _ = maintenance_repository.filter(object_ids=scoped_object_ids,
                                                          limit=None, offset=0)
    active_requests_count = sum(
        item["status"] not in {"closed", "cancelled"}
        for item in maintenance_items
    )
    risks = object_risks(object_ids)
    request_counts = active_request_counts(object_ids)
    top = []
    for object_id in object_ids:
        obj = get_objects_by_id().get(object_id)
        if obj:
            top.append(serialize_object(obj, risks[object_id], request_counts[object_id]))
    level_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    top.sort(key=lambda x: (
        level_rank.get(x["risk_level"], -1),
        x["risk_prediction_id"] if x["risk_prediction_id"] is not None else -1,
        x["id"],
    ), reverse=True)
    return {
        "objects_count": sum(user_can_access_object(user, x["object_id"])
                              for x in get_objects_by_id().values() if x["has_channels"]),
        "critical_risk_count": distribution["critical"],
        "high_risk_count": distribution["high"],
        "active_requests_count": active_requests_count,
        "risk_distribution": distribution, "top_objects": top[:10],
        "recent_predictions": predictions[:10],
    }
