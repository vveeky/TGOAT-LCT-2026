"""Durable request execution; shares the accepted operational transaction boundary."""
import hashlib
import json

from backend.app.config import utc_now
from backend.app.services.auth import user_can_access_object

ACTIVE_TASK_STATUSES = ('new', 'assigned', 'in_progress', 'awaiting_review')


def migrate_execution_schema(conn):
    """SQLite table replacement, preserving original IDs and referring receipts.

    Foreign keys are disabled only on this connection outside the transaction;
    every constraint is checked before committing and restored even on failure.
    An already migrated schema is left intact. No business rows are deleted.
    """
    if conn.in_transaction:
        raise RuntimeError('Migration requires an idle connection')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(maintenance_requests)')}
    foreign_keys = conn.execute('PRAGMA foreign_keys').fetchone()[0]
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        conn.execute('BEGIN IMMEDIATE')
        if 'assignee_user_id' not in columns:
            conn.execute('''CREATE TABLE maintenance_requests_stage3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT, object_id INTEGER NOT NULL,
                card_id TEXT REFERENCES forecast_cards(card_id),
                prediction_identity TEXT REFERENCES prediction_history(identity),
                decision_id INTEGER UNIQUE REFERENCES signal_decisions(id),
                created_by_user_id TEXT NOT NULL, created_by_role TEXT NOT NULL,
                reason TEXT NOT NULL, comment TEXT, priority TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('draft','new','assigned','in_progress','awaiting_review','closed','cancelled')),
                origin TEXT NOT NULL CHECK(origin IN ('manual','signal')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1, command_key TEXT,
                channel_id INTEGER, prediction_id INTEGER,
                assignee_user_id TEXT, assigned_by_user_id TEXT, assigned_at TEXT,
                started_at TEXT, accepted_by_user_id TEXT, accepted_at TEXT,
                CHECK(origin='manual' OR (card_id IS NOT NULL AND prediction_identity IS NOT NULL
                    AND decision_id IS NOT NULL AND status!='draft')),
                CHECK(status NOT IN ('assigned','in_progress','awaiting_review') OR assignee_user_id IS NOT NULL)
            )''')
            names = ','.join(row[1] for row in conn.execute('PRAGMA table_info(maintenance_requests)'))
            conn.execute(f'INSERT INTO maintenance_requests_stage3 ({names}) SELECT {names} FROM maintenance_requests')
            conn.execute('DROP TABLE maintenance_requests')
            conn.execute('ALTER TABLE maintenance_requests_stage3 RENAME TO maintenance_requests')
        conn.execute('DROP INDEX IF EXISTS one_active_inspection_per_card')
        conn.execute("""CREATE UNIQUE INDEX one_active_inspection_per_card ON maintenance_requests(card_id)
            WHERE origin='signal' AND status IN ('new','assigned','in_progress','awaiting_review')""")
        conn.execute('''CREATE TABLE IF NOT EXISTS maintenance_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL REFERENCES maintenance_requests(id),
            technician_user_id TEXT NOT NULL, result_code TEXT NOT NULL
                CHECK(result_code IN ('issue_found','no_issue_found','needs_followup','unable_to_verify')),
            summary TEXT NOT NULL, submitted_at TEXT NOT NULL, revision INTEGER NOT NULL,
            UNIQUE(request_id,revision))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS maintenance_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL REFERENCES maintenance_requests(id),
            event TEXT NOT NULL, actor_user_id TEXT NOT NULL, actor_role TEXT NOT NULL,
            timestamp TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, comment TEXT,
            result_id INTEGER REFERENCES maintenance_results(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS maintenance_command_receipts (
            actor_user_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            request_id INTEGER NOT NULL REFERENCES maintenance_requests(id), payload_hash TEXT NOT NULL,
            result_json TEXT NOT NULL, committed_at TEXT NOT NULL,
            PRIMARY KEY(actor_user_id,idempotency_key))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS signal_task_dispositions (
            card_id TEXT PRIMARY KEY REFERENCES forecast_cards(card_id),
            request_id INTEGER NOT NULL REFERENCES maintenance_requests(id),
            state TEXT NOT NULL CHECK(state IN ('new','resolved')))''')
        # Existing requests get a provenance-preserving baseline event, not a
        # fabricated history of assignment or technician activity.
        conn.execute('''INSERT INTO maintenance_events
            (request_id,event,actor_user_id,actor_role,timestamp,from_status,to_status)
            SELECT r.id,'created',r.created_by_user_id,r.created_by_role,r.created_at,NULL,r.status
            FROM maintenance_requests r WHERE NOT EXISTS
                (SELECT 1 FROM maintenance_events e WHERE e.request_id=r.id)''')
        if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise RuntimeError('Stage 3 migration foreign-key validation failed')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f'PRAGMA foreign_keys={int(foreign_keys)}')


class MaintenanceExecution:
    def request_visible(self, item, user):
        if not item or not user_can_access_object(user, item['object_id']):
            return False
        if item['status'] == 'draft':
            return item['created_by_user_id'] == user.user_id
        if user.has_role('central_dispatcher', 'district_dispatcher') or user.has_capability('management.read'):
            return True
        return user.has_role('technician') and item['assignee_user_id'] == user.user_id

    def event(self, request_id, action, actor_id, actor_role, timestamp, before, after, comment=None, result_id=None):
        self.conn.execute('''INSERT INTO maintenance_events
            (request_id,event,actor_user_id,actor_role,timestamp,from_status,to_status,comment,result_id)
            VALUES (?,?,?,?,?,?,?,?,?)''', (request_id,action,actor_id,actor_role,timestamp,before,after,comment,result_id))

    def request_detail(self, request_id, user=None):
        from backend.app.services.dispatcher_workflow import WorkflowNotFound
        with self._read():
            item = self.get(request_id)
            if not item or user is not None and not self.request_visible(item, user):
                raise WorkflowNotFound(request_id)
            from backend.app.services.reference_data import get_objects_by_id
            metadata=get_objects_by_id().get(item['object_id'],{})
            item['object']={'id':item['object_id'],'name':metadata.get('object_name',f"Объект {item['object_id']}")}
            item['results'] = [dict(row) for row in self.conn.execute(
                'SELECT * FROM maintenance_results WHERE request_id=? ORDER BY revision', (request_id,))]
            item['latest_result'] = item['results'][-1] if item['results'] else None
            item['timeline'] = [dict(row) for row in self.conn.execute(
                'SELECT * FROM maintenance_events WHERE request_id=? ORDER BY id', (request_id,))]
            basis = self.conn.execute('SELECT * FROM prediction_history WHERE identity=?', (item['prediction_identity'],)).fetchone()
            item['historical_basis'] = dict(basis) if basis else None
            current = self.conn.execute('SELECT * FROM prediction_history WHERE object_id=? ORDER BY snapshot_ts DESC,identity DESC LIMIT 1', (item['object_id'],)).fetchone()
            item['object_current'] = dict(current) if current else None
            return item

    def request_command(self, request_id, action, user, payload, directory=(), after_write=None):
        from backend.app.services.dispatcher_workflow import WorkflowConflict, WorkflowNotFound
        key = payload['idempotency_key']
        digest = hashlib.sha256(json.dumps({'request_id':request_id,'action':action,'role':user.role,**payload},
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                item = self.get(request_id)
                if not item or not user_can_access_object(user,item['object_id']):
                    raise WorkflowNotFound(request_id)
                receipt = self.conn.execute('SELECT * FROM maintenance_command_receipts WHERE actor_user_id=? AND idempotency_key=?', (user.user_id,key)).fetchone()
                if receipt:
                    if receipt['payload_hash'] != digest:
                        raise WorkflowConflict('Idempotency key has different content')
                    self.conn.commit()
                    return json.loads(receipt['result_json'])
                if not self.request_visible(item,user):
                    raise WorkflowNotFound(request_id)
                if item['version'] != payload['expected_version']:
                    raise WorkflowConflict('Request was changed by another user')
                dispatcher = user.has_role('central_dispatcher','district_dispatcher')
                technician = user.has_role('technician') and item['assignee_user_id'] == user.user_id
                before = item['status']
                now = utc_now().isoformat()
                fields = {}
                result_id = None
                accepted_result_code = None
                comment = payload.get('comment')
                if action == 'submit' and before == 'draft' and item['created_by_user_id'] == user.user_id:
                    after, event = 'new', 'submitted'
                elif action == 'assign' and before == 'new' and dispatcher:
                    assignee = next((u for u in directory if u.user_id == payload.get('assignee_user_id')), None)
                    if not assignee or not assignee.has_role('technician') or not user_can_access_object(assignee,item['object_id']):
                        raise WorkflowConflict('Technician is not eligible for this object')
                    fields = dict(assignee_user_id=assignee.user_id,assigned_by_user_id=user.user_id,assigned_at=now)
                    after, event = 'assigned', 'assigned'
                elif action == 'start' and before == 'assigned' and technician:
                    fields['started_at'] = now
                    after, event = 'in_progress', 'started'
                elif action == 'result' and before == 'in_progress' and technician:
                    code, summary = payload.get('result_code'), (payload.get('summary') or '').strip()
                    if code not in {'issue_found','no_issue_found','needs_followup','unable_to_verify'} or not summary:
                        raise WorkflowConflict('Result and description are required')
                    revision = self.conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM maintenance_results WHERE request_id=?',(request_id,)).fetchone()[0]
                    result_id = self.conn.execute('''INSERT INTO maintenance_results
                        (request_id,technician_user_id,result_code,summary,submitted_at,revision) VALUES (?,?,?,?,?,?)''',
                        (request_id,user.user_id,code,summary,now,revision)).lastrowid
                    after, event = 'awaiting_review', 'result_submitted'
                elif action in {'accept','return'} and before == 'awaiting_review' and dispatcher:
                    result = self.conn.execute('SELECT id,result_code FROM maintenance_results WHERE request_id=? ORDER BY revision DESC LIMIT 1',(request_id,)).fetchone()
                    if not result or action == 'return' and not (comment or '').strip():
                        raise WorkflowConflict('Submitted result / return reason required')
                    result_id = result[0]
                    accepted_result_code = result['result_code']
                    after,event = ('closed','accepted') if action == 'accept' else ('in_progress','returned')
                    if action == 'accept':
                        fields.update(accepted_by_user_id=user.user_id,accepted_at=now)
                elif action == 'cancel' and ((dispatcher and before in {'new','assigned','in_progress'}) or (before=='draft' and item['created_by_user_id']==user.user_id)):
                    if not (comment or '').strip():
                        raise WorkflowConflict('Cancellation reason required')
                    after,event = 'cancelled','cancelled'
                else:
                    raise WorkflowConflict('Action is not allowed for this role or request state')
                fields.update(status=after,updated_at=now,version=item['version']+1)
                self.conn.execute('UPDATE maintenance_requests SET '+','.join(f'{f}=?' for f in fields)+' WHERE id=?', [*fields.values(),request_id])
                actor_role = 'technician' if action in {'start','result'} else \
                    next((role for role in ('central_dispatcher','district_dispatcher') if user.has_role(role)), user.role)
                self.event(request_id,event,user.user_id,actor_role,now,before,after,comment,result_id)
                if item['card_id'] and action in {'accept','cancel'}:
                    signal_state = 'resolved' if action == 'accept' and accepted_result_code == 'no_issue_found' else 'new'
                    self.conn.execute('INSERT INTO signal_task_dispositions VALUES (?,?,?) ON CONFLICT(card_id) DO UPDATE SET request_id=excluded.request_id,state=excluded.state',
                        (item['card_id'],request_id,signal_state))
                    self.conn.execute('INSERT INTO signal_work VALUES (?,1) ON CONFLICT(card_id) DO UPDATE SET version=version+1',(item['card_id'],))
                if after_write:
                    after_write()
                result = self.request_detail(request_id)
                self.conn.execute('INSERT INTO maintenance_command_receipts VALUES (?,?,?,?,?,?)',(user.user_id,key,request_id,digest,json.dumps(result),now))
                self.conn.commit()
                return result
            except Exception:
                self.conn.rollback()
                raise
