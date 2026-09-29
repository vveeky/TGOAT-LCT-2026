"""PostgreSQL operational prediction review/outcome repository."""

from __future__ import annotations

from psycopg.rows import dict_row

from backend.app.config import as_utc, utc_now
from backend.app.services.operational_actions import (
    OperationalActionIdempotencyConflict,
    OperationalPredictionNotActionable,
    OperationalPredictionNotFound,
    _idempotency_key,
)


def _one(connection, sql, params=()):
    return connection.cursor(row_factory=dict_row).execute(sql, params).fetchone()


def _all(connection, sql, params=()):
    return connection.cursor(row_factory=dict_row).execute(sql, params).fetchall()


class PostgreSQLOperationalActionRepository:
    """Append-only action rows with cross-process actor/idempotency serialization."""

    _TABLES = {"review": "operational_prediction_reviews", "outcome": "operational_prediction_outcomes"}

    def __init__(self, database):
        self.database = database

    def close(self):
        return None

    def prediction(self, identity):
        with self.database.connection() as connection:
            row = _one(connection,"""SELECT identity,object_id,snapshot_ts,model_version,reference_hash,prediction_status
                FROM prediction_history WHERE identity=%s""",(identity,))
        return row

    def _insert(self, *, kind, identity, object_id, actor_user_id, actor_role, payload, idempotency_key):
        table=self._TABLES[kind]
        prediction=self.prediction(identity)
        if prediction is None or int(prediction["object_id"])!=int(object_id):
            raise OperationalPredictionNotFound(identity)
        if prediction["prediction_status"]!="available":
            raise OperationalPredictionNotActionable("dispatcher actions require an available operational prediction")
        key=_idempotency_key(idempotency_key,{"identity":identity,"actor_user_id":actor_user_id,**payload})
        with self.database.unit_of_work() as uow:
            conn=uow.connection
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                         (f"operational-action:{identity}:{actor_user_id}:{key}",))
            prior=_one(conn,f"SELECT * FROM {table} WHERE prediction_identity=%s AND actor_user_id=%s AND idempotency_key=%s",
                       (identity,actor_user_id,key))
            if prior:
                if {name:prior[name] for name in payload}!=payload:
                    raise OperationalActionIdempotencyConflict("idempotency key was already used with different content")
                return prior
            values={"prediction_identity":identity,"object_id":int(prediction["object_id"]),
                "snapshot_ts":prediction["snapshot_ts"],"actor_user_id":actor_user_id,"actor_role":actor_role,
                **payload,"created_at":as_utc(utc_now()),"idempotency_key":key}
            columns=",".join(values)
            placeholders=",".join(["%s"]*len(values))
            row=_one(conn,f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING *",tuple(values.values()))
            return row

    def add_review(self,identity,object_id,*,actor_user_id,actor_role,decision,reason_code,comment,idempotency_key=None):
        return self._insert(kind="review",identity=identity,object_id=object_id,actor_user_id=actor_user_id,
            actor_role=actor_role,payload={"decision":decision,"reason_code":reason_code,"comment":comment},
            idempotency_key=idempotency_key)

    def add_outcome(self,identity,object_id,*,actor_user_id,actor_role,actual_outcome,reason_code,comment,idempotency_key=None):
        return self._insert(kind="outcome",identity=identity,object_id=object_id,actor_user_id=actor_user_id,
            actor_role=actor_role,payload={"actual_outcome":actual_outcome,"reason_code":reason_code,"comment":comment},
            idempotency_key=idempotency_key)

    def _list(self,kind,identity):
        if self.prediction(identity) is None: raise OperationalPredictionNotFound(identity)
        with self.database.connection() as connection:
            return _all(connection,f"SELECT * FROM {self._TABLES[kind]} WHERE prediction_identity=%s ORDER BY id",(identity,))

    def reviews(self,identity): return self._list("review",identity)
    def outcomes(self,identity): return self._list("outcome",identity)

    def summaries(self,identities):
        result={key:{"review_count":0,"latest_review_decision":None,"outcome_count":0,"latest_actual_outcome":None} for key in identities}
        if not identities: return result
        with self.database.connection() as connection:
            reviews=_all(connection,"""SELECT prediction_identity,decision FROM operational_prediction_reviews
                WHERE prediction_identity=ANY(%s) ORDER BY created_at DESC,id DESC""",(identities,))
            outcomes=_all(connection,"""SELECT prediction_identity,actual_outcome FROM operational_prediction_outcomes
                WHERE prediction_identity=ANY(%s) ORDER BY created_at DESC,id DESC""",(identities,))
        seen=set()
        for row in reviews:
            key=row["prediction_identity"]
            result[key]["review_count"]+=1
            if key not in seen: result[key]["latest_review_decision"]=row["decision"]; seen.add(key)
        seen=set()
        for row in outcomes:
            key=row["prediction_identity"]
            result[key]["outcome_count"]+=1
            if key not in seen: result[key]["latest_actual_outcome"]=row["actual_outcome"]; seen.add(key)
        return result
