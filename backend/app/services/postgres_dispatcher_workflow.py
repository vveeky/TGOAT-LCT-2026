"""PostgreSQL implementation of the accepted dispatcher/request workflows."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.config import utc_now
from backend.app.services.audit import build_maintenance_request_created_event
from backend.app.services.auth import user_can_access_object
from backend.app.services.dispatcher_workflow import WorkflowConflict, WorkflowNotFound, validate_decision_reason


def _one(connection, sql, params=()):
    return connection.cursor(row_factory=dict_row).execute(sql, params).fetchone()


def _all(connection, sql, params=()):
    return connection.cursor(row_factory=dict_row).execute(sql, params).fetchall()


def _json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


class PostgreSQLDispatcherWorkflow:
    """Signal, decision, Request, technician lifecycle and receipts on one PG DB."""

    def __init__(self, database, forecast_repository):
        self.database = database
        self.forecast = forecast_repository

    @property
    def lock(self):
        # Compatibility for routes during incremental runtime migration. New
        # routes should call repository projection methods, never lock locally.
        return self

    @property
    def conn(self):
        # Avoid exposing pooled connection state as a fake SQLite connection.
        raise RuntimeError("PostgreSQL workflow does not expose a shared raw connection")

    def _request(self, row):
        return ({**row, "source": "local", "external_request_id": None,
                 "sync_status": "not_synced", "external_status": None} if row else None)

    def get(self, request_id):
        with self.database.connection() as connection:
            row = _one(connection,"SELECT * FROM maintenance_requests WHERE id=%s",(request_id,))
        return self._request(row)

    def create(self, data):
        if data.get("card_id") or data.get("origin", "manual") != "manual":
            raise WorkflowConflict("Linked requests require a signal command")
        with self.database.unit_of_work() as uow:
            row = _one(uow.connection,"""INSERT INTO maintenance_requests
                (object_id,channel_id,prediction_id,reason,priority,created_by_user_id,created_by_role,
                 created_at,updated_at,status,origin)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft','manual') RETURNING *""",
                (data.get("object_id"),data.get("channel_id"),data.get("prediction_id"),data["reason"],
                 data["priority"],data["created_by_user_id"],data["created_by_role"],
                 data["created_at"],data["updated_at"]))
            self._event(uow.connection,row["id"],"created",data["created_by_user_id"],
                        data["created_by_role"],data["created_at"],None,"draft")
            return self._request(row)

    def update(self, request_id, data):
        item = self.get(request_id)
        if item is None:
            raise WorkflowNotFound(request_id)
        if item["origin"] == "signal":
            raise WorkflowConflict("Linked requests cannot bypass the inspection workflow")
        raise WorkflowConflict("Use versioned request commands; generic status PATCH is disabled")

    def filter(self, *, limit=100, offset=0, object_ids=None, viewer=None, **filters):
        clauses, params = [], []
        if filters.get("active_only"):
            clauses.append("status NOT IN ('closed','cancelled')")
        if viewer is not None:
            clauses.append("(status!='draft' OR created_by_user_id=%s)")
            params.append(viewer.user_id)
            if viewer.has_role("technician") and not viewer.has_role("central_dispatcher", "district_dispatcher") \
                    and not viewer.has_capability("management.read"):
                clauses.append("((status='draft' AND created_by_user_id=%s) OR (status!='draft' AND assignee_user_id=%s))")
                params.extend([viewer.user_id,viewer.user_id])
        if object_ids is not None:
            if not object_ids:
                return [],0
            clauses.append("object_id = ANY(%s)")
            params.append(object_ids)
        allowed_filters={"status","priority","object_id","channel_id","prediction_id","assignee_user_id"}
        for name in allowed_filters:
            if filters.get(name) is not None:
                clauses.append(f"{name}=%s")
                params.append(filters[name])
        where=" WHERE "+" AND ".join(clauses) if clauses else ""
        with self.database.connection() as connection:
            total=_one(connection,"SELECT count(*) AS n FROM maintenance_requests"+where,params)["n"]
            rows=_all(connection,"""SELECT maintenance_requests.*,
                      (SELECT event FROM maintenance_events WHERE request_id=maintenance_requests.id
                       ORDER BY id DESC LIMIT 1) AS latest_event
                      FROM maintenance_requests"""+where+
                      " ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s",[*params,limit,offset])
        return [self._request(row) for row in rows],total

    def request_visible(self,item,user):
        if not item or not user_can_access_object(user,item["object_id"]):
            return False
        if item["status"]=="draft":
            return item["created_by_user_id"]==user.user_id
        if user.has_role("central_dispatcher", "district_dispatcher") or user.has_capability("management.read"):
            return True
        return user.has_role("technician") and item["assignee_user_id"]==user.user_id

    def _event(self, connection, request_id, event, actor_id, actor_role, at, before, after, comment=None, result_id=None):
        connection.execute("""INSERT INTO maintenance_events
            (request_id,event,actor_user_id,actor_role,"timestamp",from_status,to_status,comment,result_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (request_id,event,actor_id,actor_role,at,before,after,comment,result_id))

    def request_detail(self,request_id,user=None):
        from backend.app.services.reference_data import get_objects_by_id
        with self.database.connection() as connection:
            item=_one(connection,"SELECT * FROM maintenance_requests WHERE id=%s",(request_id,))
            if not item or user is not None and not self.request_visible(item,user):
                raise WorkflowNotFound(request_id)
            results=_all(connection,"SELECT * FROM maintenance_results WHERE request_id=%s ORDER BY revision",(request_id,))
            timeline=_all(connection,'SELECT * FROM maintenance_events WHERE request_id=%s ORDER BY id',(request_id,))
            basis=_one(connection,"SELECT * FROM prediction_history WHERE identity=%s",(item["prediction_identity"],)) if item["prediction_identity"] else None
            current=_one(connection,"SELECT * FROM prediction_history WHERE object_id=%s ORDER BY snapshot_ts DESC,identity DESC LIMIT 1",(item["object_id"],))
        result=self._request(item)
        meta=get_objects_by_id().get(item["object_id"],{})
        result["object"]={"id":item["object_id"],"name":meta.get("object_name",f"Объект {item['object_id']}")}
        result["results"]=results
        result["latest_result"]=results[-1] if results else None
        result["timeline"]=timeline
        result["historical_basis"]=basis
        result["object_current"]=current
        return result

    def _signal(self,connection,card_id):
        from backend.app.services.reference_data import get_objects_by_id
        card=_one(connection,"SELECT * FROM forecast_cards WHERE card_id=%s",(card_id,))
        if card is None:
            raise WorkflowNotFound(card_id)
        decision=_one(connection,"SELECT * FROM signal_decisions WHERE card_id=%s ORDER BY work_version DESC LIMIT 1",(card_id,))
        active=_one(connection,"SELECT * FROM maintenance_requests WHERE card_id=%s AND status=ANY(%s) AND origin='signal' LIMIT 1",
                     (card_id,["new","assigned","in_progress","awaiting_review"]))
        version=_one(connection,"SELECT version FROM signal_work WHERE card_id=%s",(card_id,))
        object_latest=_one(connection,"SELECT * FROM prediction_history WHERE object_id=%s ORDER BY snapshot_ts DESC,identity DESC LIMIT 1",(card["object_id"],))
        calculations=_all(connection,"""SELECT p.* FROM prediction_history p JOIN forecast_card_predictions m
            ON m.prediction_identity=p.identity WHERE m.card_id=%s AND p.prediction_status='available' AND p.risk_flag
            ORDER BY p.snapshot_ts DESC,p.identity DESC""",(card_id,))
        latest=calculations[0] if calculations else None
        state=("inspection_requested" if active else
               {"monitoring":"monitoring","false_alarm":"resolved"}.get(decision["decision"],"new") if decision else "new")
        disposition=_one(connection,"SELECT state FROM signal_task_dispositions WHERE card_id=%s",(card_id,))
        if not active and disposition:
            state=disposition["state"]
        metadata=get_objects_by_id().get(card["object_id"],{})
        last_request=_one(connection,"SELECT id FROM maintenance_requests WHERE card_id=%s ORDER BY id DESC LIMIT 1",(card_id,))
        return {"card_id":card_id,"object_id":card["object_id"],
            "object_name":metadata.get("object_name",f"Объект {card['object_id']}"),"state":state,
            "work_version":version["version"] if version else 0,"model_episode_status":card["status"],
            "first_detected":card["first_positive_snapshot_ts"],"last_model_update":latest["snapshot_ts"] if latest else None,
            "assessment_status":latest["prediction_status"] if latest else "unavailable",
            "risk_probability":latest["risk_probability"] if latest else None,"latest_context":latest,
            "object_current":{"prediction_identity":object_latest["identity"],
                "assessment_status":object_latest["prediction_status"],
                "risk_probability":object_latest["risk_probability"] if object_latest["prediction_status"]=="available" else None,
                "updated_at":object_latest["snapshot_ts"],"context":object_latest} if object_latest else None,
            "calculations":calculations,"prediction_identity":latest["identity"] if latest else None,
            "active_request":self._request(active),"last_decision":decision,
            "last_request":self.request_detail(last_request["id"]) if last_request else None}

    def signal(self,card_id):
        with self.database.connection() as connection:
            return self._signal(connection,card_id)

    def signals(self,*,object_ids=None,state=None,object_id=None,limit=100,offset=0):
        from backend.app.services.reference_data import get_objects_by_id
        with self.database.connection() as connection:
            ids=[row["card_id"] for row in _all(connection,"SELECT card_id FROM forecast_cards ORDER BY first_positive_snapshot_ts DESC,card_id")]
            items=[]
            for identifier in ids:
                item=self._signal(connection,identifier)
                if object_ids is not None and item["object_id"] not in object_ids: continue
                if object_id is not None and item["object_id"]!=object_id: continue
                items.append(item)
        counts={name:sum(item["state"]==name for item in items) for name in ("new","monitoring","inspection_requested","resolved")}
        selected=[item for item in items if state is None or item["state"]==state]
        return {"items":selected[offset:offset+limit],"total":len(selected),"counts":counts,"limit":limit,"offset":offset}

    def monitoring_projection(self,object_ids):
        from backend.app.services.reference_data import get_objects_by_id
        with self.database.connection() as connection:
            projection=self.signals(object_ids=object_ids,limit=100000)
            objects=[]
            for object_id,item in get_objects_by_id().items():
                if not item["has_channels"] or object_ids is not None and object_id not in object_ids: continue
                latest=_one(connection,"SELECT * FROM prediction_history WHERE object_id=%s ORDER BY snapshot_ts DESC LIMIT 1",(object_id,))
                objects.append({"id":object_id,"name":item["object_name"],
                    "operational_status":latest["prediction_status"] if latest else "unavailable",
                    "risk_probability":latest["risk_probability"] if latest else None,
                    "risk_flag":latest["risk_flag"] if latest else None,
                    "updated_at":latest["snapshot_ts"] if latest else None})
        return {**projection,"objects":objects}

    def object_catalog_projection(self,object_ids):
        if not object_ids: return {},[]
        with self.database.connection() as connection:
            latest=_all(connection,"""SELECT object_id,prediction_status,risk_probability,snapshot_ts FROM (
                SELECT object_id,prediction_status,risk_probability,snapshot_ts,
                    ROW_NUMBER() OVER(PARTITION BY object_id ORDER BY snapshot_ts DESC,identity DESC) AS n
                FROM prediction_history WHERE object_id=ANY(%s)) ranked WHERE n=1""",(object_ids,))
            states=_all(connection,"""SELECT c.object_id,CASE WHEN EXISTS(
                SELECT 1 FROM maintenance_requests r WHERE r.card_id=c.card_id AND r.origin='signal'
                    AND r.status=ANY(%s)) THEN 'inspection_requested'
                ELSE COALESCE(t.state,CASE d.decision WHEN 'monitoring' THEN 'monitoring'
                    WHEN 'false_alarm' THEN 'resolved' ELSE 'new' END) END AS state
                FROM forecast_cards c LEFT JOIN signal_task_dispositions t ON t.card_id=c.card_id
                LEFT JOIN signal_decisions d ON d.id=(SELECT id FROM signal_decisions
                    WHERE card_id=c.card_id ORDER BY work_version DESC LIMIT 1)
                WHERE c.object_id=ANY(%s)""",
                (["new","assigned","in_progress","awaiting_review"],object_ids))
        return ({row["object_id"]:row for row in latest},states)

    def operational_history_projection(self,*,object_ids,prediction_identity=None,start=None,end=None,
                                        state=None,limit=50,offset=0,user=None,activity_object_id=None):
        clauses=["p.object_id=ANY(%s)"]
        params=[object_ids]
        if prediction_identity is not None: clauses.append("p.identity=%s");params.append(prediction_identity)
        if start is not None: clauses.append("p.snapshot_ts >= %s");params.append(start)
        if end is not None: clauses.append("p.snapshot_ts <= %s");params.append(end)
        visibility="(r.status!='draft' OR r.created_by_user_id=%s) AND (%s!='technician' OR (r.status='draft' AND r.created_by_user_id=%s) OR (r.status!='draft' AND r.assignee_user_id=%s))"
        technician_only = user and user.has_role("technician") and not user.has_role(
            "central_dispatcher", "district_dispatcher") and not user.has_capability("management.read")
        user_params=(user.user_id,"technician" if technician_only else "dispatcher",user.user_id,user.user_id) \
            if user else ("","dispatcher","","")
        query=f"""SELECT p.identity,p.object_id,p.snapshot_ts,p.prediction_status,p.risk_probability,m.card_id,d.decision,
            CASE WHEN m.card_id IS NULL THEN NULL
                WHEN EXISTS(SELECT 1 FROM maintenance_requests a WHERE a.card_id=m.card_id AND a.origin='signal' AND a.status=ANY(%s)) THEN 'inspection_requested'
                ELSE COALESCE(t.state,CASE d.decision WHEN 'monitoring' THEN 'monitoring' WHEN 'false_alarm' THEN 'resolved' ELSE 'new' END) END AS signal_state,
            r.id AS request_id,r.status AS request_status
            FROM prediction_history p LEFT JOIN forecast_card_predictions m ON m.prediction_identity=p.identity
            LEFT JOIN signal_decisions d ON d.id=(SELECT id FROM signal_decisions WHERE card_id=m.card_id ORDER BY work_version DESC LIMIT 1)
            LEFT JOIN signal_task_dispositions t ON t.card_id=m.card_id
            LEFT JOIN maintenance_requests r ON r.id=(SELECT id FROM maintenance_requests WHERE card_id=m.card_id ORDER BY id DESC LIMIT 1)
            WHERE {' AND '.join(clauses)}"""
        full_params=[["new","assigned","in_progress","awaiting_review"],*params]
        if state:
            query=f"SELECT * FROM ({query}) history_rows WHERE signal_state=%s"
            full_params.append(state)
        with self.database.connection() as connection:
            total=_one(connection,f"SELECT count(*) AS n FROM ({query}) filtered",full_params)["n"]
            rows=_all(connection,query+" ORDER BY snapshot_ts DESC,identity DESC LIMIT %s OFFSET %s",
                      [*full_params,limit,offset])
            visible={row["id"] for row in _all(connection,
                f"SELECT id FROM maintenance_requests r WHERE {visibility}",user_params)}
            activity=[]
            if activity_object_id is not None:
                activity=_all(connection,f"""SELECT e.event,e."timestamp" AS timestamp,e.comment,e.to_status,e.request_id
                    FROM maintenance_events e JOIN maintenance_requests r ON r.id=e.request_id
                    WHERE r.object_id=%s AND {visibility} ORDER BY e."timestamp" DESC,e.id DESC LIMIT 20""",
                    [activity_object_id,*user_params])
                if user is None or user.has_capability("signals.read"):
                    activity.extend(_all(connection,"""SELECT 'signal_created' AS event,first_positive_snapshot_ts AS timestamp,
                        NULL::text AS comment,NULL::text AS to_status,NULL::bigint AS request_id,card_id
                        FROM forecast_cards WHERE object_id=%s ORDER BY first_positive_snapshot_ts DESC LIMIT 20""",(activity_object_id,)))
                    activity.extend(_all(connection,"""SELECT 'decision' AS event,created_at AS timestamp,comment,
                        decision AS to_status,NULL::bigint AS request_id,card_id FROM signal_decisions
                        WHERE object_id=%s ORDER BY created_at DESC,id DESC LIMIT 20""",(activity_object_id,)))
                activity=sorted(activity,key=lambda row:row["timestamp"],reverse=True)[:20]
        result=[]
        for row in rows:
            item=dict(row)
            if item["request_id"] not in visible: item["request_id"]=None;item["request_status"]=None
            if user and not user.has_capability("signals.read"):
                item.update(card_id=None,decision=None,signal_state=None)
            result.append(item)
        return result,total,activity

    @staticmethod
    def _digest(payload):
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

    def decide(self,card_id,*,actor_user_id,actor_role,payload,allowed_object_ids=None,
               after_decision=None,audit_context=None):
        if actor_role not in {"central_dispatcher","district_dispatcher"}:
            raise PermissionError("Dispatcher role required")
        digest=self._digest({"card_id":card_id,"actor_role":actor_role,**payload})
        key=payload["idempotency_key"]
        with self.database.unit_of_work() as uow:
            conn=uow.connection
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"signal:{actor_user_id}:{key}",))
            prior=_one(conn,"SELECT * FROM signal_command_receipts WHERE actor_user_id=%s AND idempotency_key=%s",(actor_user_id,key))
            if prior:
                if prior["payload_hash"]!=digest: raise WorkflowConflict("Idempotency key has different content")
                return prior["result_json"]
            card=_one(conn,"SELECT * FROM forecast_cards WHERE card_id=%s",(card_id,))
            if not card or allowed_object_ids is not None and card["object_id"] not in allowed_object_ids:
                raise WorkflowNotFound(card_id)
            uow.lock_object(card["object_id"])
            member=_one(conn,"SELECT 1 AS yes FROM forecast_card_predictions WHERE card_id=%s AND prediction_identity=%s",
                        (card_id,payload["prediction_identity"]))
            if not member: raise WorkflowConflict("Prediction does not belong to this signal")
            current=self._signal(conn,card_id)
            if current["work_version"]!=payload["expected_version"]: raise WorkflowConflict("Signal was changed by another dispatcher")
            if current["active_request"] or current["state"]=="resolved": raise WorkflowConflict("Signal already has an active request or is resolved")
            action=payload["decision"]
            if action not in {"inspection_requested","monitoring","false_alarm"}: raise WorkflowConflict("Unsupported decision")
            validate_decision_reason(payload)
            expected=payload["expected_version"]
            conn.execute("INSERT INTO signal_work(card_id,version) VALUES (%s,0) ON CONFLICT(card_id) DO NOTHING",(card_id,))
            advanced=_one(conn,"UPDATE signal_work SET version=version+1 WHERE card_id=%s AND version=%s RETURNING version",
                          (card_id,expected))
            if advanced is None: raise WorkflowConflict("Signal was changed by another dispatcher")
            version=advanced["version"]
            conn.execute("DELETE FROM signal_task_dispositions WHERE card_id=%s",(card_id,))
            now=utc_now()
            decision=_one(conn,"""INSERT INTO signal_decisions(card_id,object_id,prediction_identity,actor_user_id,actor_role,
                decision,reason_code,comment,created_at,work_version,command_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (card_id,card["object_id"],payload["prediction_identity"],actor_user_id,actor_role,action,
                 payload.get("reason_code"),payload.get("comment"),now,version,key))
            decision_id=decision["id"]
            if after_decision: after_decision()
            request_id=None
            if action=="inspection_requested":
                request=payload.get("request") or {}
                inserted=_one(conn,"""INSERT INTO maintenance_requests(object_id,card_id,prediction_identity,decision_id,
                    created_by_user_id,created_by_role,reason,comment,priority,status,origin,created_at,updated_at,command_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'new','signal',%s,%s,%s) RETURNING id""",
                    (card["object_id"],card_id,payload["prediction_identity"],decision_id,actor_user_id,actor_role,
                     request["reason"],payload.get("comment"),request.get("priority","normal"),now,now,key))
                request_id=inserted["id"]
                self._event(conn,request_id,"created",actor_user_id,actor_role,now,None,"new")
                from backend.app.services.postgres_audit import PostgreSQLAuditRepository
                audit_event=build_maintenance_request_created_event(
                    actor_user_id=actor_user_id,actor_role=actor_role,
                    request_id=request_id,object_id=card["object_id"],timestamp=now,
                    decision_id=decision_id,prediction_identity=payload["prediction_identity"],
                    card_id=card_id,actor_context=audit_context,
                )
                PostgreSQLAuditRepository.append_in_connection(conn,audit_event)
            result={"signal":self._signal(conn,card_id),"request":self.get(request_id) if request_id else None,
                "decision_id":decision_id,"work_version":version}
            result=_json_safe(result)
            conn.execute("""INSERT INTO signal_command_receipts(actor_user_id,idempotency_key,card_id,payload_hash,
                result_json,work_version,decision_id,request_id,committed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (actor_user_id,key,card_id,digest,Jsonb(_json_safe(result)),version,decision_id,request_id,now))
            return result

    def request_command(self,request_id,action,user,payload,directory=(),after_write=None):
        key=payload["idempotency_key"]
        digest=self._digest({"request_id":request_id,"action":action,"role":user.role,**payload})
        with self.database.unit_of_work() as uow:
            conn=uow.connection
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"request:{user.user_id}:{key}",))
            receipt=_one(conn,"SELECT * FROM maintenance_command_receipts WHERE actor_user_id=%s AND idempotency_key=%s",(user.user_id,key))
            if receipt:
                if receipt["payload_hash"]!=digest: raise WorkflowConflict("Idempotency key has different content")
                return receipt["result_json"]
            item=_one(conn,"SELECT * FROM maintenance_requests WHERE id=%s FOR UPDATE",(request_id,))
            if not item or not user_can_access_object(user,item["object_id"]): raise WorkflowNotFound(request_id)
            if not self.request_visible(item,user): raise WorkflowNotFound(request_id)
            if item["version"]!=payload["expected_version"]: raise WorkflowConflict("Request was changed by another user")
            dispatcher=user.has_role("central_dispatcher","district_dispatcher")
            technician=user.has_role("technician") and item["assignee_user_id"]==user.user_id
            before=item["status"]; now=utc_now(); fields={}; result_id=None; accepted_result_code=None; comment=payload.get("comment")
            if action=="submit" and before=="draft" and item["created_by_user_id"]==user.user_id: after,event="new","submitted"
            elif action=="assign" and before=="new" and dispatcher:
                assignee=next((entry for entry in directory if entry.user_id==payload.get("assignee_user_id")),None)
                if not assignee or not assignee.has_role("technician") or not user_can_access_object(assignee,item["object_id"]):
                    raise WorkflowConflict("Technician is not eligible for this object")
                fields.update(assignee_user_id=assignee.user_id,assigned_by_user_id=user.user_id,assigned_at=now)
                after,event="assigned","assigned"
            elif action=="start" and before=="assigned" and technician:
                fields["started_at"]=now; after,event="in_progress","started"
            elif action=="result" and before=="in_progress" and technician:
                code,summary=payload.get("result_code"),(payload.get("summary") or "").strip()
                if code not in {"issue_found","no_issue_found","needs_followup","unable_to_verify"} or not summary:
                    raise WorkflowConflict("Result and description are required")
                revision=_one(conn,"SELECT COALESCE(MAX(revision),0)+1 AS revision FROM maintenance_results WHERE request_id=%s",(request_id,))["revision"]
                result_id=_one(conn,"""INSERT INTO maintenance_results(request_id,technician_user_id,result_code,summary,submitted_at,revision)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",(request_id,user.user_id,code,summary,now,revision))["id"]
                after,event="awaiting_review","result_submitted"
            elif action in {"accept","return"} and before=="awaiting_review" and dispatcher:
                result=_one(conn,"SELECT id,result_code FROM maintenance_results WHERE request_id=%s ORDER BY revision DESC LIMIT 1",(request_id,))
                if not result or action=="return" and not (comment or "").strip(): raise WorkflowConflict("Submitted result / return reason required")
                result_id=result["id"]
                accepted_result_code=result["result_code"]
                after,event=("closed","accepted") if action=="accept" else ("in_progress","returned")
                if action=="accept": fields.update(accepted_by_user_id=user.user_id,accepted_at=now)
            elif action=="cancel" and ((dispatcher and before in {"new","assigned","in_progress"}) or (before=="draft" and item["created_by_user_id"]==user.user_id)):
                if not (comment or "").strip(): raise WorkflowConflict("Cancellation reason required")
                after,event="cancelled","cancelled"
            else: raise WorkflowConflict("Action is not allowed for this role or request state")
            fields.update(status=after,updated_at=now,version=item["version"]+1)
            assignments=",".join(f"{field}=%s" for field in fields)
            conn.execute("UPDATE maintenance_requests SET "+assignments+" WHERE id=%s",[*fields.values(),request_id])
            actor_role = "technician" if action in {"start", "result"} else \
                next((role for role in ("central_dispatcher", "district_dispatcher") if user.has_role(role)), user.role)
            self._event(conn,request_id,event,user.user_id,actor_role,now,before,after,comment,result_id)
            if item["card_id"] and action in {"accept","cancel"}:
                new_state="resolved" if action=="accept" and accepted_result_code=="no_issue_found" else "new"
                conn.execute("""INSERT INTO signal_task_dispositions(card_id,request_id,state) VALUES (%s,%s,%s)
                    ON CONFLICT(card_id) DO UPDATE SET request_id=excluded.request_id,state=excluded.state""",
                    (item["card_id"],request_id,new_state))
                conn.execute("""INSERT INTO signal_work(card_id,version) VALUES (%s,1)
                    ON CONFLICT(card_id) DO UPDATE SET version=signal_work.version+1""",(item["card_id"],))
            if after_write: after_write()
            result=self.request_detail(request_id)
            result=_json_safe(result)
            conn.execute("""INSERT INTO maintenance_command_receipts(actor_user_id,idempotency_key,request_id,
                payload_hash,result_json,committed_at) VALUES (%s,%s,%s,%s,%s,%s)""",
                (user.user_id,key,request_id,digest,Jsonb(_json_safe(result)),now))
            return result
