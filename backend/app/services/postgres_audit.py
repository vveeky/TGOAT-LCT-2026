"""Durable append-only PostgreSQL audit repository."""

from __future__ import annotations

from datetime import datetime, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


_STRUCTURAL = {
    "timestamp", "user_id", "actor_user_id", "role", "actor_roles", "actor_scope",
    "request_id", "correlation_id", "action", "entity_type", "entity_id", "resource_type",
    "resource_id", "object_id", "previous_state", "new_state", "outcome", "result",
    "failure_reason", "source_mode", "metadata", "http_method", "path", "status_code",
}
_SAFE_EXTRA_METADATA = {
    "snapshot_ts", "generated_at", "risk_probability", "risk_flag", "threshold",
    "model_version", "feature_schema_version", "reference_snapshot_id", "reference_hash",
    "prediction_status", "degraded_reason", "recommendation_status",
    "recommendation_code", "recommendation_rule_version", "recommendation_basis", "card_id",
}
_SENSITIVE_KEY_PARTS = ("password", "token", "secret", "authorization", "credential", "body")


def _safe_json(value):
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()
                if not any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, datetime):
        return _timestamp(value).isoformat()
    return value


def _timestamp(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _event_values(event):
    roles = list(event.get("actor_roles") or ())
    if not roles and event.get("role"):
        roles = [event["role"]]
    scope = event.get("actor_scope") or {
        "groups": list(event.get("groups") or ()),
        "scope_roots": list(event["scope_roots"]) if event.get("scope_roots") is not None else None,
        "global": event.get("scope_roots") is None and "central_dispatcher" in roles,
    }
    metadata = _safe_json(dict(event.get("metadata") or {}))
    metadata.update(_safe_json({key: value for key, value in event.items()
                                if key in _SAFE_EXTRA_METADATA and key not in _STRUCTURAL}))
    return {
        "timestamp": _timestamp(event.get("timestamp")),
        "actor_user_id": event.get("actor_user_id", event.get("user_id")),
        "actor_roles": roles,
        "actor_scope": Jsonb(_safe_json(scope)),
        "request_id": event.get("request_id"),
        "correlation_id": event.get("correlation_id", event.get("request_id")),
        "action": str(event.get("action") or "unknown"),
        "resource_type": str(event.get("resource_type", event.get("entity_type", "api_request")) or "api_request"),
        "resource_id": str(event.get("resource_id", event.get("entity_id")))
            if event.get("resource_id", event.get("entity_id")) is not None else None,
        "object_id": event.get("object_id"),
        "previous_state": Jsonb(_safe_json(event["previous_state"])) if event.get("previous_state") is not None else None,
        "new_state": Jsonb(_safe_json(event["new_state"])) if event.get("new_state") is not None else None,
        "outcome": str(event.get("outcome", event.get("result", "success")) or "success"),
        "failure_reason": event.get("failure_reason"),
        "source_mode": str(event.get("source_mode") or "api"),
        "metadata": Jsonb(metadata),
        "http_method": event.get("http_method"),
        "path": event.get("path"),
        "status_code": event.get("status_code"),
        "role": event.get("role") or (roles[0] if roles else None),
    }


class PostgreSQLAuditRepository:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def append_in_connection(connection, event):
        values = _event_values(event)
        columns = ",".join(values)
        placeholders = ",".join(["%s"] * len(values))
        row = connection.cursor(row_factory=dict_row).execute(
            f"INSERT INTO audit_events ({columns}) VALUES ({placeholders}) RETURNING *",
            tuple(values.values()),
        ).fetchone()
        return PostgreSQLAuditRepository._public(row)

    def add(self, event):
        with self.database.unit_of_work() as uow:
            return self.append_in_connection(uow.connection, event)

    @staticmethod
    def _public(row):
        roles = list(row["actor_roles"] or ())
        event = {
            "id": row["id"], "timestamp": row["timestamp"], "user_id": row["actor_user_id"],
            "actor_user_id": row["actor_user_id"], "actor_roles": roles,
            "actor_scope": row["actor_scope"], "request_id": row["request_id"],
            "correlation_id": row["correlation_id"], "action": row["action"],
            "entity_type": row["resource_type"], "resource_type": row["resource_type"],
            "entity_id": row["resource_id"], "resource_id": row["resource_id"],
            "object_id": row["object_id"], "previous_state": row["previous_state"],
            "new_state": row["new_state"], "outcome": row["outcome"],
            "result": row["outcome"], "failure_reason": row["failure_reason"],
            "source_mode": row["source_mode"], "metadata": row["metadata"] or {},
            "http_method": row["http_method"], "path": row["path"],
            "status_code": row["status_code"], "role": row["role"] or (roles[0] if roles else None),
        }
        # Preserve legacy read consumers without duplicating the durable schema.
        event.update({key: value for key, value in event["metadata"].items() if key not in event})
        return event

    def filter(self, *, user_id=None, viewer_user_id=None, role=None, action=None, entity_type=None,
               entity_id=None, object_ids=None, include_global=False, start=None, end=None,
               limit=100, offset=0):
        clauses, params = [], []
        if user_id is not None:
            clauses.append("actor_user_id=%s")
            params.append(user_id)
        if role is not None:
            clauses.append("%s = ANY(actor_roles)")
            params.append(role)
        if action:
            clauses.append("action ILIKE %s")
            params.append(f"%{action}%")
        if entity_type:
            clauses.append("resource_type=%s")
            params.append(entity_type)
        if entity_id:
            clauses.append("resource_id=%s")
            params.append(str(entity_id))
        if start is not None:
            clauses.append("timestamp >= %s")
            params.append(start)
        if end is not None:
            clauses.append("timestamp < %s")
            params.append(end)
        if object_ids is not None and not include_global:
            if not object_ids:
                clauses.append("actor_user_id=%s")
                params.append(viewer_user_id or "")
            else:
                clauses.append("(object_id = ANY(%s) OR actor_user_id=%s)")
                params.extend([object_ids, viewer_user_id or ""])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.database.connection() as connection:
            total = connection.execute("SELECT count(*) FROM audit_events" + where, params).fetchone()[0]
            rows = connection.cursor(row_factory=dict_row).execute(
                "SELECT * FROM audit_events" + where + " ORDER BY timestamp DESC,id DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        return [self._public(row) for row in rows], total
