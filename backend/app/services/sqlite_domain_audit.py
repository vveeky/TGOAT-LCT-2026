"""Transactional SQLite storage for workflow domain audit events."""

from __future__ import annotations

import json
from datetime import datetime, timezone


def _timestamp(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported audit value: {type(value).__name__}")


class SQLiteDomainAuditRepository:
    """Stores selected domain events in the same SQLite transaction as workflow writes."""

    def __init__(self, connection, lock):
        self.connection = connection
        self.lock = lock
        with self.lock, self.connection:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS domain_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    event_json TEXT NOT NULL
                )"""
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS domain_audit_events_timestamp_idx "
                "ON domain_audit_events(timestamp DESC, id DESC)"
            )

    @staticmethod
    def append_in_connection(connection, event: dict) -> dict:
        payload = dict(event)
        timestamp = payload.get("timestamp")
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()
        elif isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()
        payload["timestamp"] = timestamp
        cursor = connection.execute(
            "INSERT INTO domain_audit_events(timestamp, action, event_json) VALUES (?,?,?)",
            (timestamp, payload["action"], json.dumps(payload, ensure_ascii=False, default=_json_default)),
        )
        return {**payload, "id": -int(cursor.lastrowid)}

    def filter(self, *, user_id=None, viewer_user_id=None, role=None, action=None,
               entity_type=None, entity_id=None, object_ids=None, include_global=False,
               start=None, end=None, limit=100, offset=0):
        with self.lock:
            rows = self.connection.execute(
                "SELECT id, event_json FROM domain_audit_events ORDER BY timestamp DESC, id DESC"
            ).fetchall()
        items = []
        for row in rows:
            item = json.loads(row["event_json"])
            if user_id is not None and item.get("user_id") != user_id:
                continue
            if role is not None and item.get("role") != role:
                continue
            if action and item.get("action") != action and not str(item.get("action", "")).startswith(f"{action} "):
                continue
            if entity_type and item.get("entity_type") != entity_type:
                continue
            if entity_id and item.get("entity_id") != entity_id:
                continue
            if object_ids is not None and item.get("object_id") not in object_ids and \
                    item.get("user_id") != viewer_user_id and \
                    not (include_global and item.get("object_id") is None):
                continue
            event_time = _timestamp(item["timestamp"])
            if start is not None and event_time < _timestamp(start):
                continue
            if end is not None and event_time >= _timestamp(end):
                continue
            item["id"] = -int(row["id"])
            items.append(item)
        items.sort(key=lambda item: (_timestamp(item["timestamp"]), item["id"]), reverse=True)
        page = items[offset:] if limit is None else items[offset:offset + limit]
        return page, len(items)
