from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.schemas import Notification, NotificationListResponse, NotificationUpdate
from backend.app.services.mock_storage import notification_repository
from backend.app.services.auth import (
    AuthUser, accessible_object_ids, get_current_user, notification_recipient, user_can_access_object,
)
from backend.app.services.audit import record_audit_event

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _notification_sort_key(item):
    created_at = item["created_at"]
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc), str(item["id"])


@router.get("", response_model=NotificationListResponse)
def get_notifications(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                      user: AuthUser = Depends(get_current_user)):
    operational_repository = getattr(request.app.state, "operational_notification_repository", None)
    if operational_repository is not None:
        items, unread_count = operational_repository.filter_for(
            notification_recipient(user), object_ids=accessible_object_ids(user),
            include_global=user.scope_roots is None, limit=None, offset=0,
        )
    else:
        # Compatibility for isolated legacy/demo tests that invoke the route
        # without constructing an operational runtime. Normal and Replay app
        # lifespans always bind their own notification repository.
        items, unread_count = notification_repository.filter_for(
            notification_recipient(user), object_ids=accessible_object_ids(user),
            include_global=user.scope_roots is None, limit=None, offset=0,
        )
        for item in items:
            item.setdefault("source_kind", "legacy_mock")
    items = sorted(items, key=_notification_sort_key, reverse=True)
    return {"items": items[offset:offset + limit], "unread_count": unread_count}


@router.patch("/{notification_id}", response_model=Notification)
def update_notification(notification_id: int, body: NotificationUpdate, request: Request,
                        user: AuthUser = Depends(get_current_user)):
    recipient = notification_recipient(user)
    operational_repository = getattr(request.app.state, "operational_notification_repository", None)
    source = operational_repository.get(notification_id) if operational_repository is not None else None
    is_operational = source is not None
    if operational_repository is None:
        source = notification_repository.get(notification_id)
    if source is None or (source.get("object_id") is None and user.scope_roots is not None) \
            or (source.get("object_id") is not None and not user_can_access_object(
                user, source["object_id"]
            )):
        raise HTTPException(404, "Notification not found")
    repository = operational_repository if is_operational else notification_repository
    old_state = repository.is_read(notification_id, recipient)
    item = repository.mark_read(notification_id, recipient, body.read)
    if item is None: raise HTTPException(404, "Notification not found")
    item.setdefault("source_kind", "operational_ml" if is_operational else "legacy_mock")
    if old_state is not None and old_state != body.read:
        record_audit_event(user, "notification.read_state_changed", "notification",
                           notification_id, item.get("object_id"))
    return item
