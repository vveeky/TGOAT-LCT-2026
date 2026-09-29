"""Operational PostgreSQL repositories for deterministic projections/receipts."""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.config import utc_now
from backend.app.services.operational_notifications import ID_OFFSET
from backend.app.services.recommendations import Recommendation
from backend.app.services.operational_cycle import OperationalCycleResult, CycleAlreadyRunning
from backend.app.config import as_utc
from datetime import timedelta


def _one(connection, sql, params=()):
    return connection.cursor(row_factory=dict_row).execute(sql, params).fetchone()


def _all(connection, sql, params=()):
    return connection.cursor(row_factory=dict_row).execute(sql, params).fetchall()


class PostgreSQLRecommendationRepository:
    def __init__(self, database):
        self.database = database

    def close(self):
        # Connection lifecycle belongs to the shared pool.
        return None

    def save(self, recommendation: Recommendation):
        with self.database.connection() as connection:
            if not self.database.in_unit_of_work:
                with connection.transaction():
                    return self._save(connection, recommendation)
            return self._save(connection, recommendation)

    @staticmethod
    def _save(connection, item):
        connection.execute("""INSERT INTO recommendations
            (recommendation_id,object_id,snapshot_ts,card_id,status,code,action,basis_json,
             rule_version,generated_at,model_version,reference_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING""",
            (item.recommendation_id,item.object_id,item.snapshot_ts,item.card_id,item.status,item.code,
             item.action,Jsonb(list(item.basis)),item.rule_version,item.generated_at,item.model_version,item.reference_hash))
        row = _one(connection,"SELECT * FROM recommendations WHERE recommendation_id=%s",(item.recommendation_id,))
        if row is not None:
            # generated_at is intentionally excluded: retrying generation for
            # the same deterministic identity naturally happens at a later
            # wall-clock time. All recommendation meaning/provenance must match.
            expected = (item.object_id, item.snapshot_ts, item.card_id, item.status,
                        item.code, item.action, list(item.basis), item.rule_version,
                        item.model_version, item.reference_hash)
            actual = (row["object_id"], row["snapshot_ts"], row["card_id"], row["status"],
                      row["code"], row["action"], row["basis_json"], row["rule_version"],
                      row["model_version"], row["reference_hash"])
            if actual != expected:
                raise RuntimeError("recommendation identity conflicts with incompatible content")
        else:
            # A different identity collided with the natural unique key. This
            # is not an idempotent retry and must fail closed, including when
            # card_id is NULL (where ordinary UNIQUE semantics allow NULLs).
            natural = _one(connection, """SELECT recommendation_id FROM recommendations
                WHERE object_id=%s AND snapshot_ts=%s AND card_id IS NOT DISTINCT FROM %s
                  AND rule_version=%s""",
                (item.object_id,item.snapshot_ts,item.card_id,item.rule_version))
            if natural is not None:
                raise RuntimeError("recommendation natural identity conflicts with an existing row")
            raise RuntimeError("recommendation insert conflicted without a compatible existing row")
        return Recommendation(row["recommendation_id"],row["object_id"],row["snapshot_ts"],row["card_id"],
            row["status"],row["code"],row["action"],tuple(row["basis_json"]),row["rule_version"],
            row["generated_at"],row["model_version"],row["reference_hash"])

    def list_for_object(self, object_id: int):
        with self.database.connection() as connection:
            rows = _all(connection,"SELECT * FROM recommendations WHERE object_id=%s ORDER BY snapshot_ts DESC",(object_id,))
        return [Recommendation(row["recommendation_id"],row["object_id"],row["snapshot_ts"],row["card_id"],
            row["status"],row["code"],row["action"],tuple(row["basis_json"]),row["rule_version"],
            row["generated_at"],row["model_version"],row["reference_hash"]) for row in rows]


class PostgreSQLOperationalNotificationRepository:
    def __init__(self, database):
        self.database = database

    def close(self):
        return None

    @staticmethod
    def _public_id(row_id: int) -> int:
        return ID_OFFSET + int(row_id)

    @staticmethod
    def _internal_id(notification_id: int):
        value = int(notification_id) - ID_OFFSET
        return value if value > 0 else None

    @staticmethod
    def _row(row, read=False):
        if row is None:
            return None
        return {"id": ID_OFFSET + int(row["id"]), "source_kind": "operational_ml", "type": "risk",
            "severity": row["severity"], "title": row["title"], "message": row["message"],
            "prediction_id": row["prediction_identity"], "object_id": row["object_id"],
            "channel_id": None, "card_id": row["card_id"], "read": read, "created_at": row["created_at"]}

    def create_for_prediction(self, record, card=None):
        prediction = record.prediction
        if prediction.prediction_status != "available" or prediction.risk_flag is not True:
            return None
        probability = prediction.risk_probability
        severity = "critical" if probability is not None and probability >= 0.8 else "warning"
        title = "Требует внимания: прогноз ML"
        message = (f"Объект {prediction.object_id}: прогноз риска {probability:.1%} на горизонте 24 ч."
                   if probability is not None else f"Объект {prediction.object_id}: доступен положительный прогноз ML.")
        with self.database.connection() as connection:
            if not self.database.in_unit_of_work:
                with connection.transaction():
                    return self._create(connection, record, card, severity, title, message)
            return self._create(connection, record, card, severity, title, message)

    @staticmethod
    def _create(connection, record, card, severity, title, message):
        connection.execute("""INSERT INTO operational_notifications
            (prediction_identity,object_id,card_id,severity,title,message,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(prediction_identity) DO NOTHING""",
            (record.identity,record.prediction.object_id,card.card_id if card else None,
             severity,title,message,record.created_at))
        return PostgreSQLOperationalNotificationRepository._row(
            _one(connection,"SELECT * FROM operational_notifications WHERE prediction_identity=%s",(record.identity,)))

    def filter_for(self, recipient_key: str, *, object_ids=None, include_global=False, limit=100, offset=0):
        with self.database.connection() as connection:
            rows = _all(connection,"""SELECT n.*,r.read_at FROM operational_notifications n
                LEFT JOIN operational_notification_receipts r ON r.notification_id=n.id AND r.recipient_key=%s
                ORDER BY n.created_at DESC,n.id DESC""",(recipient_key,))
        items = [self._row(row, row["read_at"] is not None) for row in rows]
        if object_ids is not None:
            allowed = set(object_ids)
            items = [item for item in items if item["object_id"] in allowed]
        return (items[offset:] if limit is None else items[offset:offset+limit],sum(not item["read"] for item in items))

    def get(self, notification_id: int):
        internal = self._internal_id(notification_id)
        if internal is None:
            return None
        with self.database.connection() as connection:
            return self._row(_one(connection,"SELECT * FROM operational_notifications WHERE id=%s",(internal,)))

    def filter_one(self, internal_id: int, recipient_key: str):
        with self.database.connection() as connection:
            row = _one(connection,"""SELECT n.*,r.read_at FROM operational_notifications n
                LEFT JOIN operational_notification_receipts r ON r.notification_id=n.id AND r.recipient_key=%s
                WHERE n.id=%s""",(recipient_key,internal_id))
        return self._row(row,row["read_at"] is not None) if row else None

    def mark_read(self, notification_id: int, recipient_key: str, read: bool):
        internal = self._internal_id(notification_id)
        if internal is None:
            return None
        with self.database.unit_of_work() as uow:
            exists = _one(uow.connection,"SELECT id FROM operational_notifications WHERE id=%s FOR UPDATE",(internal,))
            if not exists:
                return None
            if read:
                uow.connection.execute("""INSERT INTO operational_notification_receipts(notification_id,recipient_key,read_at)
                    VALUES (%s,%s,%s) ON CONFLICT(notification_id,recipient_key) DO NOTHING""",(internal,recipient_key,utc_now()))
            else:
                uow.connection.execute("DELETE FROM operational_notification_receipts WHERE notification_id=%s AND recipient_key=%s",(internal,recipient_key))
        return self.filter_one(internal,recipient_key)

    def is_read(self, notification_id: int, recipient_key: str):
        internal = self._internal_id(notification_id)
        if internal is None:
            return None
        with self.database.connection() as connection:
            row = _one(connection,"""SELECT r.read_at FROM operational_notifications n LEFT JOIN
                operational_notification_receipts r ON r.notification_id=n.id AND r.recipient_key=%s WHERE n.id=%s""",
                (recipient_key,internal))
        return None if row is None else row["read_at"] is not None


class PostgreSQLOperationalCycleRepository:
    """Persistent cycle lease/status; unique snapshot is the cross-process gate."""

    def __init__(self, database, lease_seconds: int = 1800):
        self.database = database
        self.lease_seconds = lease_seconds

    def close(self):
        return None

    @staticmethod
    def _row(row):
        return OperationalCycleResult(
            cycle_id=row["cycle_id"], snapshot_ts=row["snapshot_ts"], status=row["status"],
            object_count=row["object_count"], available_count=row["available_count"],
            degraded_count=row["degraded_count"], positive_count=row["positive_count"],
            started_at=row["started_at"], finished_at=row["finished_at"],
            error=row["error"], run_mode=row["run_mode"],
        )

    def begin(self, snapshot_ts: datetime, object_count: int, run_mode: str = "normal"):
        snapshot_ts = as_utc(snapshot_ts)
        cycle_id = f"canonical:{snapshot_ts.isoformat()}"
        now = utc_now()
        with self.database.unit_of_work() as uow:
            inserted=uow.connection.execute("""INSERT INTO operational_cycles
                (cycle_id,snapshot_ts,status,object_count,started_at,run_mode)
                VALUES (%s,%s,'running',%s,%s,%s) ON CONFLICT(snapshot_ts) DO NOTHING RETURNING cycle_id""",
                (cycle_id,snapshot_ts,object_count,now,run_mode)).fetchone()
            row = _one(uow.connection,"SELECT * FROM operational_cycles WHERE snapshot_ts=%s FOR UPDATE",(snapshot_ts,))
            if inserted:
                return self._row(row)
            if row["status"] == "completed":
                return self._row(row)
            if row["status"] == "running" and now - row["started_at"] < timedelta(seconds=self.lease_seconds):
                raise CycleAlreadyRunning(row["cycle_id"])
            uow.connection.execute("""UPDATE operational_cycles SET status='running',object_count=%s,
                started_at=%s,finished_at=NULL,error=NULL,run_mode=%s WHERE cycle_id=%s""",
                (object_count,now,run_mode,row["cycle_id"]))
            return self._row(_one(uow.connection,"SELECT * FROM operational_cycles WHERE cycle_id=%s",(row["cycle_id"],)))

    def latest_completed_snapshot(self):
        with self.database.connection() as connection:
            row = _one(connection,"SELECT snapshot_ts FROM operational_cycles WHERE status='completed' ORDER BY snapshot_ts DESC LIMIT 1")
        return row["snapshot_ts"] if row else None

    def latest(self):
        with self.database.connection() as connection:
            row = _one(connection,"SELECT * FROM operational_cycles ORDER BY started_at DESC LIMIT 1")
        return self._row(row) if row else None

    def get(self, snapshot_ts: datetime):
        with self.database.connection() as connection:
            row = _one(connection,"SELECT * FROM operational_cycles WHERE snapshot_ts=%s",(as_utc(snapshot_ts),))
        return self._row(row) if row else None

    def _transition(self, cycle_id, sql, params):
        with self.database.unit_of_work() as uow:
            uow.connection.execute(sql,params)
            row = _one(uow.connection,"SELECT * FROM operational_cycles WHERE cycle_id=%s",(cycle_id,))
            if row is None:
                raise KeyError(cycle_id)
            return self._row(row)

    def complete(self, cycle_id: str, *, available: int, degraded: int, positive: int):
        return self._transition(cycle_id,"""UPDATE operational_cycles SET status='completed',available_count=%s,
            degraded_count=%s,positive_count=%s,finished_at=%s,error=NULL WHERE cycle_id=%s""",
            (available,degraded,positive,utc_now(),cycle_id))

    def fail(self, cycle_id: str, error: str):
        return self._transition(cycle_id,"UPDATE operational_cycles SET status='failed',finished_at=%s,error=%s WHERE cycle_id=%s",
                                (utc_now(),error[:2000],cycle_id))

    def record_unavailable(self, snapshot_ts: datetime, *, run_mode: str, error: str):
        snapshot_ts=as_utc(snapshot_ts)
        cycle_id=f"canonical:{snapshot_ts.isoformat()}"
        now=utc_now()
        with self.database.unit_of_work() as uow:
            uow.connection.execute("""INSERT INTO operational_cycles
                (cycle_id,snapshot_ts,status,object_count,started_at,finished_at,error,run_mode)
                VALUES (%s,%s,'unavailable',0,%s,%s,%s,%s)
                ON CONFLICT(snapshot_ts) DO UPDATE SET status='unavailable',finished_at=excluded.finished_at,
                    error=excluded.error,run_mode=excluded.run_mode""",
                (cycle_id,snapshot_ts,now,now,error[:2000],run_mode))
            return self._row(_one(uow.connection,"SELECT * FROM operational_cycles WHERE snapshot_ts=%s",(snapshot_ts,)))
