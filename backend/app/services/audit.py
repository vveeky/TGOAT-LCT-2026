import logging
import hashlib
from contextvars import ContextVar
from contextlib import contextmanager

from backend.app.config import utc_now
from backend.app.services.auth import AuthUser
from backend.app.services.mock_storage import audit_repository

logger = logging.getLogger(__name__)
_current_database: ContextVar[object | None] = ContextVar("tgoat_audit_database", default=None)


class DurableAuditWriteError(RuntimeError):
    pass


@contextmanager
def bind_audit_database(database):
    token = _current_database.set(database)
    try:
        yield
    finally:
        _current_database.reset(token)


def _store(event: dict) -> dict | None:
    database = _current_database.get()
    if database is not None:
        try:
            from backend.app.services.postgres_audit import PostgreSQLAuditRepository
            return PostgreSQLAuditRepository(database).add(event)
        except Exception as error:
            logger.exception("Durable audit event recording failed")
            raise DurableAuditWriteError("Durable audit storage failed") from error
    try:
        return audit_repository.add({"timestamp": utc_now().isoformat(), **event})
    except Exception:
        logger.exception("Audit event recording failed")
        return None


def audit_actor_context(user: AuthUser) -> dict:
    return {
        "user_id": user.user_id,
        "actor_user_id": user.user_id,
        "role": user.role,
        "actor_roles": list(user.roles),
        "actor_scope": _scope(user),
    }


def build_maintenance_request_created_event(*, actor_user_id: str, actor_role: str,
                                            request_id: int, object_id: int,
                                            timestamp, decision_id: int,
                                            prediction_identity: str, card_id: str,
                                            actor_context: dict | None = None) -> dict:
    actor = dict(actor_context or {})
    actor.update(user_id=actor_user_id, actor_user_id=actor_user_id, role=actor_role)
    actor.setdefault("actor_roles", [actor_role])
    if hasattr(timestamp, "isoformat"):
        timestamp = timestamp.isoformat()
    return {
        **actor,
        "timestamp": timestamp,
        "action": "maintenance_request.created",
        "entity_type": "maintenance_request",
        "entity_id": str(request_id),
        "object_id": object_id,
        "outcome": "success",
        "new_state": {"status": "new", "version": 1},
        "metadata": {
            "decision_id": decision_id,
            "prediction_identity": prediction_identity,
            "signal_card_id": card_id,
        },
    }


def record_audit_event(user: AuthUser, action: str, entity_type: str,
                       entity_id: int | str, object_id: int | None = None, *,
                       previous_state=None, new_state=None, outcome="success", metadata=None) -> dict | None:
    return _store({
        "user_id": user.user_id, "actor_user_id": user.user_id, "role": user.role,
        "actor_roles": list(user.roles), "actor_scope": _scope(user),
        "action": action, "entity_type": entity_type, "entity_id": str(entity_id),
        "object_id": object_id, "previous_state": previous_state, "new_state": new_state,
        "outcome": outcome, "metadata": metadata or {},
    })


def _scope(user):
    return {"groups": list(user.groups),
            "scope_roots": list(user.scope_roots) if user.scope_roots is not None else None,
            "global": user.scope_roots is None and user.has_role("central_dispatcher")}


def record_ml_inference_event(user: AuthUser, result: dict) -> dict | None:
    """Record model provenance; human review remains a separate event."""
    return _store({
        "user_id": user.user_id, "actor_user_id": user.user_id, "role": user.role,
        "actor_roles": list(user.roles), "actor_scope": _scope(user),
        "action": "ml.inference", "entity_type": "object",
        "entity_id": str(result.get("object_id")), "object_id": result.get("object_id"),
        "snapshot_ts": result.get("snapshot_ts"), "generated_at": result.get("generated_at"),
        "risk_probability": result.get("risk_probability"), "risk_flag": result.get("risk_flag"),
        "threshold": result.get("threshold"), "model_version": result.get("model_version"),
        "feature_schema_version": result.get("feature_schema_version"),
        "reference_snapshot_id": result.get("reference_snapshot_id"),
        "reference_hash": result.get("reference_hash"),
        "prediction_status": result.get("status"),
        "degraded_reason": result.get("degraded_reason"),
    })


def record_recommendation_event(user: AuthUser, recommendation, action: str = "recommendation.generated") -> dict | None:
    """Recommendation provenance is separate from ML inference and human review."""
    return _store({
        "user_id": user.user_id, "actor_user_id": user.user_id, "role": user.role,
        "actor_roles": list(user.roles), "actor_scope": _scope(user), "action": action,
        "entity_type": "recommendation", "entity_id": recommendation.recommendation_id,
        "object_id": recommendation.object_id, "snapshot_ts": recommendation.snapshot_ts.isoformat(),
        "card_id": recommendation.card_id, "recommendation_status": recommendation.status,
        "recommendation_code": recommendation.code, "recommendation_rule_version": recommendation.rule_version,
        "recommendation_basis": list(recommendation.basis),
        "generated_at": recommendation.generated_at.isoformat(),
    })


def record_request_event(*, user: AuthUser | None, method: str, path: str,
                         action: str, entity_type: str, entity_id: int | str | None,
                         object_id: int | None, result: str, status_code: int,
                         request_id: str) -> dict | None:
    return _store({
        "user_id": user.user_id if user else None,
        "actor_user_id": user.user_id if user else None,
        "role": user.role if user else None,
        "actor_roles": list(user.roles) if user else [],
        "actor_scope": _scope(user) if user else {},
        "action": action,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id is not None else None,
        "object_id": object_id,
        "http_method": method,
        "path": path,
        "source_mode": "http_api",
        "outcome": result,
        "failure_reason": "permission_denied" if result == "forbidden" else
                          "authentication_required" if result == "unauthorized" else None,
        "result": result,
        "status_code": status_code,
        "request_id": request_id,
    })


def record_login_event(*, username: str, user: AuthUser | None, success: bool,
                       request_id: str | None = None) -> None:
    result = "success" if success else "unauthorized"
    status_code = 200 if success else 401
    action = "login.succeeded" if success else "login.failed"
    _store({
        "user_id": user.user_id if user else None,
        "actor_user_id": user.user_id if user else None,
        "role": user.role if user else None,
        "actor_roles": list(user.roles) if user else [],
        "actor_scope": _scope(user) if user else {},
        "action": action,
        "entity_type": "user",
        "entity_id": user.user_id if user else None,
        "object_id": None,
        "http_method": "POST",
        "path": "/api/v1/auth/login",
        "result": result,
        "outcome": result,
        "status_code": status_code,
        "request_id": request_id,
        "source_mode": "http_api",
        "metadata": {"username_fingerprint": hashlib.sha256(username.encode("utf-8")).hexdigest()},
    })


def resolve_request_entity(path: str, query_params) -> tuple[str, str | None, int | None]:
    """Infer path identifiers and query identifiers declared by their route."""
    from backend.app.services.reference_data import get_channels_by_id

    parts = path.strip("/").split("/")
    if len(parts) < 3 or parts[:2] != ["api", "v1"]:
        return "api_request", None, None
    section = parts[2]
    if len(parts) >= 3 and parts[:2] == ["api", "v1"]:
        section = parts[2]
        if section == "objects":
            if len(parts) > 3 and parts[3].isdigit():
                return "object", parts[3], int(parts[3])
            return "object_collection", None, None
        if section == "channels":
            if len(parts) > 3 and parts[3].isdigit():
                channel_id = int(parts[3])
                channel = get_channels_by_id().get(channel_id)
                object_id = channel.get("object_id") if channel else None
                return "channel", str(channel_id), object_id
            return "channel_collection", None, None
        if section == "predictions":
            if len(parts) > 3 and parts[3].isdigit():
                prediction_id = int(parts[3])
                from backend.app.services.mock_storage import prediction_repository
                prediction = prediction_repository.get(prediction_id)
                object_id = prediction.get("object_id") if prediction else None
                return "prediction", str(prediction_id), object_id
            if len(parts) == 3:
                query_object = query_params.get("object_id")
                if query_object and query_object.isdigit():
                    return "prediction_collection", None, int(query_object)
                query_channel = query_params.get("channel_id")
                if query_channel and query_channel.isdigit():
                    channel = get_channels_by_id().get(int(query_channel))
                    return "prediction_collection", None, channel.get("object_id") if channel else None
            return "prediction_collection", None, None
        if section == "maintenance-requests":
            if len(parts) > 3 and parts[3].isdigit():
                request_id = int(parts[3])
                from backend.app.services.mock_storage import maintenance_repository
                item = maintenance_repository.get(request_id)
                object_id = item.get("object_id") if item else None
                return "maintenance_request", str(request_id), object_id
            if len(parts) == 3:
                query_object = query_params.get("object_id")
                if query_object and query_object.isdigit():
                    return "maintenance_collection", None, int(query_object)
                query_channel = query_params.get("channel_id")
                if query_channel and query_channel.isdigit():
                    channel = get_channels_by_id().get(int(query_channel))
                    if channel:
                        return "maintenance_collection", None, channel.get("object_id")
                query_prediction = query_params.get("prediction_id")
                if query_prediction and query_prediction.isdigit():
                    from backend.app.services.mock_storage import prediction_repository
                    prediction = prediction_repository.get(int(query_prediction))
                    return "maintenance_collection", None, prediction.get("object_id") if prediction else None
            return "maintenance_collection", None, None
        if section == "notifications":
            if len(parts) > 3 and parts[3].isdigit():
                notification_id = int(parts[3])
                from backend.app.services.mock_storage import notification_repository
                item = notification_repository.get(notification_id)
                object_id = item.get("object_id") if item else None
                return "notification", str(notification_id), object_id
            return "notification_collection", None, None
        if section == "audit-events":
            return "audit_journal", None, None
        if section == "dashboard":
            return "dashboard", None, None
        if section == "auth":
            return "identity", parts[3] if len(parts) > 3 else None, None
        return section, None, None
    return section, None, None
