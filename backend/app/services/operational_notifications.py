"""Persistent in-web notifications for actionable operational predictions."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from backend.app.config import utc_now
from backend.app.services.sqlite_support import connect_sqlite


ID_OFFSET = 1_000_000_000


class SQLiteOperationalNotificationRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection = connect_sqlite(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS operational_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_identity TEXT NOT NULL UNIQUE REFERENCES prediction_history(identity),
                object_id INTEGER NOT NULL,
                card_id TEXT,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_operational_notifications_object_time
                ON operational_notifications(object_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS operational_notification_receipts (
                notification_id INTEGER NOT NULL REFERENCES operational_notifications(id) ON DELETE CASCADE,
                recipient_key TEXT NOT NULL,
                read_at TEXT NOT NULL,
                PRIMARY KEY(notification_id, recipient_key)
            );
        """)
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _public_id(row_id: int) -> int:
        return ID_OFFSET + int(row_id)

    @staticmethod
    def _internal_id(notification_id: int) -> int | None:
        value = int(notification_id) - ID_OFFSET
        return value if value > 0 else None

    @staticmethod
    def _row(row: sqlite3.Row, read: bool = False) -> dict:
        return {
            "id": SQLiteOperationalNotificationRepository._public_id(row["id"]),
            "source_kind": "operational_ml",
            "type": "risk",
            "severity": row["severity"],
            "title": row["title"],
            "message": row["message"],
            "prediction_id": row["prediction_identity"],
            "object_id": row["object_id"],
            "channel_id": None,
            "card_id": row["card_id"],
            "read": read,
            "created_at": row["created_at"],
        }

    def create_for_prediction(self, record, card=None) -> dict | None:
        prediction = record.prediction
        if prediction.prediction_status != "available" or prediction.risk_flag is not True:
            return None
        probability = prediction.risk_probability
        severity = "critical" if probability is not None and probability >= 0.8 else "warning"
        title = "Требует внимания: прогноз ML"
        message = (f"Объект {prediction.object_id}: прогноз риска "
                   f"{probability:.1%} на горизонте 24 ч." if probability is not None
                   else f"Объект {prediction.object_id}: доступен положительный прогноз ML.")
        with self._lock, self._connection:
            self._connection.execute("""INSERT OR IGNORE INTO operational_notifications
                (prediction_identity, object_id, card_id, severity, title, message, created_at)
                VALUES (?,?,?,?,?,?,?)""", (
                record.identity, prediction.object_id, card.card_id if card else None,
                severity, title, message, record.created_at.isoformat(),
            ))
            row = self._connection.execute(
                "SELECT * FROM operational_notifications WHERE prediction_identity=?",
                (record.identity,),
            ).fetchone()
        return self._row(row)

    def get(self, notification_id: int) -> dict | None:
        internal = self._internal_id(notification_id)
        if internal is None:
            return None
        with self._lock:
            row = self._connection.execute("SELECT * FROM operational_notifications WHERE id=?", (internal,)).fetchone()
            return self._row(row) if row else None

    def filter_for(self, recipient_key: str, *, object_ids=None, include_global=False,
                   limit=100, offset=0) -> tuple[list[dict], int]:
        with self._lock:
            rows = self._connection.execute("""
                SELECT n.*, r.read_at FROM operational_notifications n
                LEFT JOIN operational_notification_receipts r
                  ON r.notification_id=n.id AND r.recipient_key=?
                ORDER BY n.created_at DESC, n.id DESC
            """, (recipient_key,)).fetchall()
            items = [self._row(row, read=row["read_at"] is not None) for row in rows]
            if object_ids is not None:
                allowed = set(object_ids)
                items = [item for item in items if item["object_id"] in allowed]
            page = items[offset:] if limit is None else items[offset:offset + limit]
            return page, sum(not item["read"] for item in items)

    def mark_read(self, notification_id: int, recipient_key: str, read: bool) -> dict | None:
        internal = self._internal_id(notification_id)
        if internal is None:
            return None
        with self._lock, self._connection:
            exists = self._connection.execute(
                "SELECT id FROM operational_notifications WHERE id=?", (internal,),
            ).fetchone()
            if exists is None:
                return None
            if read:
                self._connection.execute("""INSERT OR IGNORE INTO operational_notification_receipts
                    (notification_id, recipient_key, read_at) VALUES (?,?,?)""",
                    (internal, recipient_key, utc_now().isoformat()))
            else:
                self._connection.execute("DELETE FROM operational_notification_receipts WHERE notification_id=? AND recipient_key=?",
                                         (internal, recipient_key))
        return self.filter_one(internal, recipient_key)

    def filter_one(self, internal_id: int, recipient_key: str) -> dict | None:
        with self._lock:
            row = self._connection.execute("""
                SELECT n.*, r.read_at FROM operational_notifications n
                LEFT JOIN operational_notification_receipts r
                  ON r.notification_id=n.id AND r.recipient_key=? WHERE n.id=?
            """, (recipient_key, internal_id)).fetchone()
            return self._row(row, read=row["read_at"] is not None) if row else None

    def is_read(self, notification_id: int, recipient_key: str) -> bool | None:
        internal = self._internal_id(notification_id)
        if internal is None:
            return None
        with self._lock:
            row = self._connection.execute("""
                SELECT r.read_at FROM operational_notifications n
                LEFT JOIN operational_notification_receipts r
                  ON r.notification_id=n.id AND r.recipient_key=? WHERE n.id=?
            """, (recipient_key, internal)).fetchone()
            return None if row is None else row["read_at"] is not None
