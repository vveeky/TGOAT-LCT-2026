"""Human workflow, co-located with and serialized by operational history.

Reads never materialize signals. A card without decisions projects as new/v0.
No model update mutates work versions. Command receipts are durable and actor
scoped; one BEGIN IMMEDIATE covers every business write and its receipt.
"""
import hashlib
import json
from contextlib import contextmanager

from backend.app.config import utc_now
from backend.app.services.audit import build_maintenance_request_created_event
from backend.app.services.maintenance_execution import MaintenanceExecution, migrate_execution_schema
from backend.app.services.sqlite_domain_audit import SQLiteDomainAuditRepository


class WorkflowConflict(ValueError):
    pass


class WorkflowNotFound(LookupError):
    pass


MIN_DECISION_REASON_LENGTH = 8


def validate_decision_reason(payload):
    decision = payload.get('decision')
    if decision == 'inspection_requested':
        request = payload.get('request') or {}
        reason = request.get('reason', '').strip()
        if len(reason) < MIN_DECISION_REASON_LENGTH:
            raise WorkflowConflict('Inspection reason must contain at least 8 characters')
    if decision == 'false_alarm' and len((payload.get('comment') or '').strip()) < MIN_DECISION_REASON_LENGTH:
        raise WorkflowConflict('False-alarm explanation must contain at least 8 characters')


class SQLiteDispatcherWorkflow(MaintenanceExecution):
    def __init__(self, history):
        self.history = history
        self.conn = history._connection
        self.lock = history._lock
        with self.lock, self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS signal_work (
                    card_id TEXT PRIMARY KEY REFERENCES forecast_cards(card_id),
                    version INTEGER NOT NULL CHECK(version>=0)
                );
                CREATE TABLE IF NOT EXISTS signal_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id TEXT NOT NULL REFERENCES forecast_cards(card_id),
                    object_id INTEGER NOT NULL,
                    prediction_identity TEXT NOT NULL REFERENCES prediction_history(identity),
                    actor_user_id TEXT NOT NULL, actor_role TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('monitoring','false_alarm','inspection_requested')),
                    reason_code TEXT, comment TEXT, created_at TEXT NOT NULL,
                    work_version INTEGER NOT NULL, command_key TEXT NOT NULL,
                    UNIQUE(card_id,work_version)
                );
                CREATE TABLE IF NOT EXISTS maintenance_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, object_id INTEGER NOT NULL,
                    card_id TEXT REFERENCES forecast_cards(card_id),
                    prediction_identity TEXT REFERENCES prediction_history(identity),
                    decision_id INTEGER UNIQUE REFERENCES signal_decisions(id),
                    created_by_user_id TEXT NOT NULL, created_by_role TEXT NOT NULL,
                    reason TEXT NOT NULL, comment TEXT, priority TEXT NOT NULL,
                    status TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('manual','signal')),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, command_key TEXT,
                    channel_id INTEGER, prediction_id INTEGER,
                    CHECK(origin='manual' OR (card_id IS NOT NULL AND prediction_identity IS NOT NULL
                        AND decision_id IS NOT NULL AND status='new'))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_inspection_per_card
                    ON maintenance_requests(card_id) WHERE origin='signal' AND status='new';
                CREATE TABLE IF NOT EXISTS signal_command_receipts (
                    actor_user_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    card_id TEXT NOT NULL REFERENCES forecast_cards(card_id),
                    payload_hash TEXT NOT NULL, result_json TEXT NOT NULL,
                    work_version INTEGER NOT NULL, decision_id INTEGER NOT NULL REFERENCES signal_decisions(id),
                    request_id INTEGER REFERENCES maintenance_requests(id), committed_at TEXT NOT NULL,
                    PRIMARY KEY(actor_user_id,idempotency_key)
                );
            """)

        self.domain_audit_repository = SQLiteDomainAuditRepository(self.conn, self.lock)

        with self.lock:
            migrate_execution_schema(self.conn)

    def _request(self, row):
        return {**dict(row), 'source': 'local', 'external_request_id': None,
                'sync_status': 'not_synced', 'external_status': None} if row else None

    @contextmanager
    def _read(self):
        """Stable SQLite snapshot across multi-query projections and workers."""
        with self.lock:
            owned = not self.conn.in_transaction
            if owned:
                self.conn.execute('BEGIN')
            try:
                yield
                if owned:
                    self.conn.commit()
            except Exception:
                if owned:
                    self.conn.rollback()
                raise

    def get(self, request_id):
        with self.lock:
            return self._request(self.conn.execute(
                'SELECT * FROM maintenance_requests WHERE id=?', (request_id,)).fetchone())

    def create(self, data):
        """Manual requests only; linked requests cannot use this write path."""
        with self.lock, self.conn:
            if data.get('card_id') or data.get('origin', 'manual') != 'manual':
                raise WorkflowConflict('Linked requests require a signal command')
            fields = ('object_id', 'channel_id', 'prediction_id', 'reason', 'priority',
                      'created_by_user_id', 'created_by_role', 'created_at', 'updated_at')
            cursor = self.conn.execute(
                f"INSERT INTO maintenance_requests ({','.join(fields)},status,origin) "
                f"VALUES ({','.join('?' for _ in fields)},'draft','manual')",
                [data.get(name) for name in fields])
            self.event(cursor.lastrowid, 'created', data['created_by_user_id'], data['created_by_role'], data['created_at'], None, 'draft')
            return self.get(cursor.lastrowid)

    def update(self, request_id, data):
        with self.lock, self.conn:
            item = self.get(request_id)
            if item is None:
                raise WorkflowNotFound(request_id)
            if item['origin'] == 'signal':
                raise WorkflowConflict('Linked requests cannot bypass the inspection workflow')
            raise WorkflowConflict('Use versioned request commands; generic status PATCH is disabled')

    def filter(self, *, limit=100, offset=0, object_ids=None, viewer=None, **filters):
        clauses, params = [], []
        if filters.get('active_only'):
            clauses.append("status NOT IN ('closed','cancelled')")
        if viewer is not None:
            clauses.append("(status!='draft' OR created_by_user_id=?)")
            params.append(viewer.user_id)
            if viewer.has_role('technician') and not viewer.has_role('central_dispatcher', 'district_dispatcher') \
                    and not viewer.has_capability('management.read'):
                clauses.append("((status='draft' AND created_by_user_id=?) OR (status!='draft' AND assignee_user_id=?))")
                params.extend([viewer.user_id,viewer.user_id])
        if object_ids is not None:
            if not object_ids:
                return [], 0
            clauses.append(f"object_id IN ({','.join('?' for _ in object_ids)})")
            params.extend(object_ids)
        for name in ('status', 'priority', 'object_id', 'channel_id', 'prediction_id', 'assignee_user_id'):
            if filters.get(name) is not None:
                clauses.append(f'{name}=?')
                params.append(filters[name])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.lock:
            total = self.conn.execute('SELECT COUNT(*) FROM maintenance_requests' + where, params).fetchone()[0]
            rows = self.conn.execute('''SELECT maintenance_requests.*,
                (SELECT event FROM maintenance_events WHERE request_id=maintenance_requests.id
                 ORDER BY id DESC LIMIT 1) AS latest_event
                FROM maintenance_requests''' + where +
                                     ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                                     [*params, limit, offset]).fetchall()
            return [self._request(row) for row in rows], total

    def signal(self, card_id):
        with self._read():
            card = self.conn.execute('SELECT * FROM forecast_cards WHERE card_id=?', (card_id,)).fetchone()
            if card is None:
                raise WorkflowNotFound(card_id)
            decision = self.conn.execute('SELECT * FROM signal_decisions WHERE card_id=? ORDER BY work_version DESC LIMIT 1',
                                         (card_id,)).fetchone()
            active = self.conn.execute("SELECT * FROM maintenance_requests WHERE card_id=? AND status IN ('new','assigned','in_progress','awaiting_review') AND origin='signal'",
                                       (card_id,)).fetchone()
            version = self.conn.execute('SELECT version FROM signal_work WHERE card_id=?', (card_id,)).fetchone()
            object_latest = self.conn.execute('SELECT * FROM prediction_history WHERE object_id=? ORDER BY snapshot_ts DESC,identity DESC LIMIT 1',
                                       (card['object_id'],)).fetchone()
            calculations = [dict(row) for row in self.conn.execute('''SELECT p.* FROM prediction_history p
                JOIN forecast_card_predictions m ON m.prediction_identity=p.identity WHERE m.card_id=?
                AND p.prediction_status='available' AND p.risk_flag=1
                ORDER BY p.snapshot_ts DESC,p.identity DESC''', (card_id,))]
            latest = calculations[0] if calculations else None
            state = ('inspection_requested' if active else
                     {'monitoring': 'monitoring', 'false_alarm': 'resolved'}.get(decision['decision'], 'new')
                     if decision else 'new')
            disposition = self.conn.execute('SELECT state FROM signal_task_dispositions WHERE card_id=?',(card_id,)).fetchone()
            if not active and disposition:
                state = disposition['state']
            from backend.app.services.reference_data import get_objects_by_id
            metadata = get_objects_by_id().get(card['object_id'], {})
            last_request = self.conn.execute('SELECT id FROM maintenance_requests WHERE card_id=? ORDER BY id DESC LIMIT 1',(card_id,)).fetchone()
            return {'card_id': card_id, 'object_id': card['object_id'],
                    'object_name': metadata.get('object_name', f"Объект {card['object_id']}"),
                    'state': state, 'work_version': version[0] if version else 0,
                    'model_episode_status': card['status'],
                    'first_detected': card['first_positive_snapshot_ts'],
                    'last_model_update': latest['snapshot_ts'] if latest else None,
                    'assessment_status': latest['prediction_status'] if latest else 'unavailable',
                    'risk_probability': latest['risk_probability'] if latest and latest['prediction_status']=='available' else None,
                    'latest_context': dict(latest) if latest else None,
                    'object_current': {'prediction_identity': object_latest['identity'],
                        'assessment_status': object_latest['prediction_status'],
                        'risk_probability': object_latest['risk_probability'] if object_latest['prediction_status']=='available' else None,
                        'updated_at': object_latest['snapshot_ts'],
                        'context': dict(object_latest)} if object_latest else None,
                    'calculations': calculations, 'prediction_identity': calculations[0]['identity'] if calculations else None,
                    'active_request': self._request(active), 'last_decision': dict(decision) if decision else None,
                    'last_request': self.request_detail(last_request[0]) if last_request else None}

    def signals(self, *, object_ids=None, state=None, object_id=None, limit=100, offset=0):
        with self._read():
            ids = [row[0] for row in self.conn.execute('SELECT card_id FROM forecast_cards ORDER BY first_positive_snapshot_ts DESC,card_id')]
            items = []
            for card_id in ids:
                item = self.signal(card_id)
                if object_ids is not None and item['object_id'] not in object_ids:
                    continue
                if object_id is not None and item['object_id'] != object_id:
                    continue
                items.append(item)
            counts = {name: sum(item['state']==name for item in items)
                      for name in ('new', 'monitoring', 'inspection_requested', 'resolved')}
            selected = [item for item in items if state is None or item['state']==state]
            return {'items': selected[offset:offset+limit], 'total': len(selected), 'counts': counts,
                    'limit': limit, 'offset': offset}

    def decide(self, card_id, *, actor_user_id, actor_role, payload, allowed_object_ids=None,
               after_decision=None, audit_context=None):
        if actor_role not in {'central_dispatcher', 'district_dispatcher'}:
            raise PermissionError('Dispatcher role required')
        digest = hashlib.sha256(json.dumps({'card_id': card_id, 'actor_role': actor_role, **payload},
                                          sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        key = payload['idempotency_key']
        with self.lock:
            conn = self.conn
            conn.execute('BEGIN IMMEDIATE')
            try:
                card = conn.execute('SELECT * FROM forecast_cards WHERE card_id=?', (card_id,)).fetchone()
                if not card or allowed_object_ids is not None and card['object_id'] not in allowed_object_ids:
                    raise WorkflowNotFound(card_id)
                prior = conn.execute('SELECT * FROM signal_command_receipts WHERE actor_user_id=? AND idempotency_key=?',
                                     (actor_user_id, key)).fetchone()
                if prior:
                    if prior['payload_hash'] != digest:
                        raise WorkflowConflict('Idempotency key has different content')
                    conn.commit()
                    result = json.loads(prior['result_json'])
                    # Old successful receipts may contain the pre-2.1 mixed read
                    # projection. Normalize only its context, never replay writes
                    # or replace the recorded human outcome/version with live state.
                    signal = result['signal']
                    if 'object_current' not in signal:
                        previous = signal.get('latest_context')
                        signal['object_current'] = {'prediction_identity': previous['identity'],
                            'assessment_status': previous['prediction_status'],
                            'risk_probability': previous['risk_probability'] if previous['prediction_status']=='available' else None,
                            'updated_at': previous['snapshot_ts'], 'context': previous} if previous else None
                        episode = next((p for p in signal['calculations'] if p['prediction_status']=='available' and p['risk_flag']), None)
                        signal.update(prediction_identity=episode['identity'] if episode else None,
                            risk_probability=episode['risk_probability'] if episode else None,
                            assessment_status=episode['prediction_status'] if episode else 'unavailable',
                            last_model_update=episode['snapshot_ts'] if episode else None,
                            latest_context=episode)
                    return result
                member = conn.execute('SELECT 1 FROM forecast_card_predictions WHERE card_id=? AND prediction_identity=?',
                                      (card_id, payload['prediction_identity'])).fetchone()
                if not member:
                    raise WorkflowConflict('Prediction does not belong to this signal')
                current = self.signal(card_id)
                if current['work_version'] != payload['expected_version']:
                    raise WorkflowConflict('Signal was changed by another dispatcher')
                if current['active_request'] or current['state']=='resolved':
                    raise WorkflowConflict('Signal already has an active request or is resolved')
                action = payload['decision']
                if action not in {'inspection_requested', 'monitoring', 'false_alarm'}:
                    raise WorkflowConflict('Unsupported decision')
                validate_decision_reason(payload)
                version = current['work_version']+1
                conn.execute('DELETE FROM signal_task_dispositions WHERE card_id=?',(card_id,))
                now = utc_now().isoformat()
                cursor = conn.execute('''INSERT INTO signal_decisions
                    (card_id,object_id,prediction_identity,actor_user_id,actor_role,decision,reason_code,comment,created_at,work_version,command_key)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (card_id,card['object_id'],payload['prediction_identity'],actor_user_id,
                    actor_role,action,payload.get('reason_code'),payload.get('comment'),now,version,key))
                decision_id = cursor.lastrowid
                if after_decision:
                    after_decision()
                request_id = None
                if action=='inspection_requested':
                    request = payload.get('request') or {}
                    cursor = conn.execute('''INSERT INTO maintenance_requests
                        (object_id,card_id,prediction_identity,decision_id,created_by_user_id,created_by_role,
                         reason,comment,priority,status,origin,created_at,updated_at,command_key)
                        VALUES (?,?,?,?,?,?,?,?,?,'new','signal',?,?,?)''',
                        (card['object_id'],card_id,payload['prediction_identity'],decision_id,actor_user_id,actor_role,
                         request['reason'],payload.get('comment'),request.get('priority','normal'),now,now,key))
                    request_id = cursor.lastrowid
                    self.event(request_id,'created',actor_user_id,actor_role,now,None,'new')
                    audit_event = build_maintenance_request_created_event(
                        actor_user_id=actor_user_id, actor_role=actor_role,
                        request_id=request_id, object_id=card['object_id'], timestamp=now,
                        decision_id=decision_id, prediction_identity=payload['prediction_identity'],
                        card_id=card_id, actor_context=audit_context,
                    )
                    self.domain_audit_repository.append_in_connection(conn, audit_event)
                conn.execute('INSERT INTO signal_work VALUES (?,?) ON CONFLICT(card_id) DO UPDATE SET version=excluded.version',
                             (card_id,version))
                result = {'signal': self.signal(card_id), 'request': self.get(request_id) if request_id else None,
                          'decision_id': decision_id, 'work_version': version}
                conn.execute('INSERT INTO signal_command_receipts VALUES (?,?,?,?,?,?,?,?,?)',
                             (actor_user_id,key,card_id,digest,json.dumps(result),version,decision_id,request_id,now))
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise
