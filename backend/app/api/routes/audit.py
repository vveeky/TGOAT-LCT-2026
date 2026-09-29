from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime

from backend.app.schemas import AuditEventListResponse
from backend.app.services.auth import AuthUser, accessible_object_ids, get_current_user, require_capability
from backend.app.services.mock_storage import audit_repository

router = APIRouter(prefix="/audit-events", tags=["Audit"])


def _sort_key(event):
    timestamp = event["timestamp"]
    if not isinstance(timestamp, datetime):
        timestamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc), int(event["id"])


@router.get("", response_model=AuditEventListResponse)
def get_audit_events(
    request: Request,
    user_id: str | None = None, role: str | None = None, action: str | None = None,
    entity_type: str | None = None, entity_id: str | None = None,
    start: AwareDatetime | None = None, end: AwareDatetime | None = None,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
    user: AuthUser = Depends(get_current_user),
):
    require_capability(user, "audit.read")
    repository = getattr(request.app.state, "audit_repository", None)
    repository = repository or audit_repository
    filters = dict(
        user_id=user_id, viewer_user_id=user.user_id, role=role, action=action, entity_type=entity_type,
        entity_id=entity_id, object_ids=accessible_object_ids(user),
        include_global=user.has_role("central_dispatcher", "system_administrator"),
        start=start, end=end,
    )
    workflow = getattr(request.app.state, "dispatcher_workflow", None)
    domain_repository = getattr(workflow, "domain_audit_repository", None)
    if domain_repository is None:
        items, total = repository.filter(**filters, limit=limit, offset=offset)
    else:
        page_size = offset + limit
        items, total = repository.filter(**filters, limit=page_size, offset=0)
        domain_items, domain_total = domain_repository.filter(**filters, limit=page_size, offset=0)
        items.extend(domain_items)
        items.sort(key=_sort_key, reverse=True)
        items = items[offset:offset + limit]
        total += domain_total
    return {"items": items, "total": total}
