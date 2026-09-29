"""Durable simulated-source -> frozen 5-minute inference -> normal PostgreSQL workflow.

This is explicitly an historical event feed pretending to arrive over time, NOT a
connection to customer SMVU. The immutable digest-pinned event source supplies
complete coverage; no model scores are fabricated. The PostgreSQL progress row
is the committed watermark and is updated atomically with raw event ingestion,
prediction/card/recommendation/notification writes. On crash the in-memory
feature state is reconstructed only through that committed watermark.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
import threading
import time

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.app.config import as_utc
from backend.app.services.ml_features import InMemoryStateStore, StateFeatureProvider
from backend.app.services.risk_inference import RiskInferenceService
from backend.app.services.reference_data import get_channels_by_object, get_reference_meta
from backend.app.services.replay_runtime import (
    DEFAULT_MANIFEST, ReplayFeatureProvider, ReplayConfigurationError,
    ReplayRuntime, load_replay_manifest, load_replay_event_source,
)


class SimulatedLiveSourceError(RuntimeError):
    """Reject unpinned source, out-of-order progress or unavailable scoring."""


def _iso(value: datetime) -> str:
    return as_utc(value).isoformat()


class SimulatedLiveOperationalService:
    """Single 5-minute boundary in one PG transaction; safe across two workers."""

    def __init__(self, database, operational_runtime: dict, *, manifest_path: str | Path | None = DEFAULT_MANIFEST, interval_seconds: float = 300.0):
        self.database = database
        self.runtime = operational_runtime
        self.manifest_path = Path(manifest_path or DEFAULT_MANIFEST)
        self.manifest, source_rows = load_replay_manifest(self.manifest_path)
        self.rows = sorted(source_rows, key=lambda x: (as_utc(x.hour_ts), x.object_id, x.channel_id))
        self.events = load_replay_event_source(self.manifest, self.manifest_path)
        self.events.sort(key=lambda x: (as_utc(x.timestamp), x.object_id, x.channel_id))
        self.event_times = [as_utc(x.timestamp) for x in self.events]
        self.start_at = as_utc(datetime.fromisoformat(self.manifest['replay_start']))
        self.end_at = as_utc(datetime.fromisoformat(self.manifest['replay_end']))
        self.source_id = 'simulated-live:' + self.manifest['replay_id']
        self.digest = self.manifest['event_source_sha256']
        self.covered_ids = sorted(map(int, self.manifest['covered_object_ids']))
        self._loaded_to: datetime | None = None
        self._state = None
        self._provider = None
        self._inference: RiskInferenceService | None = None
        self._reference_counts = {int(k): len(items) for k, items in get_channels_by_object().items()}
        from backend.app.services.ml_features import SYSTEM_NAMES
        self._system_counts: dict[tuple[int, str], int] = {}
        self.interval_seconds = max(0.1, float(interval_seconds))
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_error: str | None = None
        for oid, channels in get_channels_by_object().items():
            for channel in channels:
                key = (int(oid), SYSTEM_NAMES.get(channel['system_type'], channel['system_type']))
                self._system_counts[key] = self._system_counts.get(key, 0) + 1

    def _source_state(self, through: datetime) -> None:
        """Recreate all ephemeral state from immutable fixture through COMMITTED watermark.

        Never expose full future buckets from fixture: only buckets actually sealed
        at ``through``. The currently open hour uses event-level observations.
        """
        through = as_utc(through)
        included_rows = [row for row in self.rows
                         if as_utc(row.hour_ts) + timedelta(hours=1, seconds=-1) <= through]
        upper = bisect_right(self.event_times, through)
        bucket_start = through.replace(minute=0, second=0, microsecond=0)
        left = bisect_right(self.event_times, bucket_start - timedelta(microseconds=1))
        open_events = self.events[left:upper]
        # The first canonical boundary is 11:59:59; historical event coverage
        # starts after that baseline. No current-hour scoring at the baseline.
        coverage_from = max(self.start_at, bucket_start)
        if coverage_from > through:
            coverage_from = through
        reference = get_reference_meta()
        self._state = InMemoryStateStore(
            included_rows, self._reference_counts, self._system_counts,
            available_from=self.start_at - timedelta(hours=168), available_to=through,
            reference_snapshot_id=f"reference-v{reference['schema_version']}",
            reference_hash=reference['source_sha256'],
            raw_observations=open_events, event_coverage_from=coverage_from,
            event_coverage_to=through,
        )
        self._provider = ReplayFeatureProvider(StateFeatureProvider(
            self._state, expected_reference_snapshot_id=f"reference-v{reference['schema_version']}",
            expected_reference_hash=reference['source_sha256'], require_complete_coverage=True,
        ), set(self.covered_ids))
        if self._inference is None:
            self._inference = RiskInferenceService(self._provider)
        else:
            self._inference.provider = self._provider
        self._loaded_to = through

    def _locked_progress(self, connection):
        connection.execute('''INSERT INTO simulated_live_progress
            (source_id,source_digest,baseline_at,processed_at,end_at)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT(source_id) DO NOTHING''',
            (self.source_id, self.digest, self.start_at, self.start_at, self.end_at))
        row = connection.cursor(row_factory=dict_row).execute('''
            SELECT * FROM simulated_live_progress WHERE source_id=%s FOR UPDATE''',
            (self.source_id,)).fetchone()
        if (row is None or row['source_digest'] != self.digest or as_utc(row['baseline_at']) != self.start_at
                or as_utc(row['end_at']) != self.end_at):
            raise SimulatedLiveSourceError('source provenance or immutable bounds have changed')
        return row

    def status(self) -> dict:
        with self.database.connection() as connection:
            row = connection.cursor(row_factory=dict_row).execute('''SELECT * FROM
                simulated_live_progress WHERE source_id=%s''', (self.source_id,)).fetchone()
            count = connection.execute('SELECT count(*) FROM simulated_live_events WHERE source_id=%s',
                                       (self.source_id,)).fetchone()[0]
        processed_at = as_utc(row['processed_at']) if row else self.start_at
        return {'mode': 'simulated_live', 'source_id': self.source_id,
                'source_kind': 'historical_registered_events_revealed_as_stream',
                'source_sha256': self.digest, 'processed_at': processed_at.isoformat(),
                'end_at': self.end_at.isoformat(), 'covered_objects': len(self.covered_ids),
                'uncovered_objects': sorted(set(self.manifest['object_ids']) - set(self.covered_ids)),
                'persisted_ingested_events': count,
                'processed_boundaries': row['processed_boundaries'] if row else 0,
                'finished': processed_at >= self.end_at,
                'worker_running': self._run_event.is_set() and not self._stop_event.is_set(),
                'interval_seconds': self.interval_seconds,
                'last_error': self._last_error}

    def process_one(self) -> dict:
        """One complete next tick or no-op. No clock advance without PG commit."""
        start_clock = perf_counter()
        with self.database.unit_of_work() as uow:
            connection = uow.connection
            progress = self._locked_progress(connection)
            previous = as_utc(progress['processed_at'])
            if previous >= self.end_at:
                return {'status': 'completed', 'processed_at': _iso(previous), 'ingested': 0}
            # Align after hh:59:59 baseline at hh+1:05:00 (never hh+1:00:00).
            boundary = ReplayRuntime._next_scoring_boundary(previous)
            if boundary > self.end_at:
                boundary = self.end_at
            left = bisect_right(self.event_times, previous)
            right = bisect_right(self.event_times, boundary)
            new_events = self.events[left:right]
            if self._loaded_to != boundary:
                self._source_state(boundary)
            # This service is an explicit historical simulator. Do not mutate
            # the canonical production-cycle scheduler or feature contract.
            if self._inference is None:
                raise SimulatedLiveSourceError('simulated live inference is unavailable')
            predictions = self._inference.batch_predict([(oid, boundary) for oid in self.covered_ids])
            if (len(predictions) != len(self.covered_ids)
                    or any(result.get('status') != 'available' for result in predictions)):
                self._loaded_to = None
                raise SimulatedLiveSourceError('inference/provider coverage not complete: refusing watermark')
            for result in predictions:
                result['generated_at'] = boundary.isoformat()
            # Persist raw source events and all 77 model predictions in ONE
            # existing UoW. Nested PG repository batches use savepoints.
            for seq in range(left, right):
                item = self.events[seq]
                payload = asdict(item)
                payload['timestamp'] = _iso(item.timestamp)
                connection.execute('''INSERT INTO simulated_live_events
                    (source_id,source_sequence,timestamp,object_id,channel_id,payload)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(source_id,source_sequence) DO NOTHING''',
                    (self.source_id, seq, as_utc(item.timestamp), item.object_id, item.channel_id, Jsonb(payload)))
            written = self.runtime['cycle'].forecast_service.record_batch(predictions)
            if len(written) != len(self.covered_ids):
                self._loaded_to = None
                raise SimulatedLiveSourceError('prediction batch cardinality mismatch')
            connection.execute('''UPDATE simulated_live_progress
                SET processed_at=%s, processed_boundaries=processed_boundaries+1,
                    updated_at=CURRENT_TIMESTAMP WHERE source_id=%s AND processed_at=%s''',
                (boundary, self.source_id, previous))
        return {'status': 'processed', 'processed_at': _iso(boundary),
                'ingested': len(new_events), 'scored_objects': len(written),
                'positive': sum(item[0].prediction.risk_flag is True for item in written),
                'elapsed_seconds': round(perf_counter() - start_clock, 4)}

    def process_until(self, target: datetime, *, max_boundaries: int = 100) -> list[dict]:
        if not 1 <= max_boundaries <= 1000:
            raise ValueError('max_boundaries out of range')
        target = min(as_utc(target), self.end_at)
        completed = []
        for _ in range(max_boundaries):
            status = self.status()
            if datetime.fromisoformat(status['processed_at']) >= target or status['finished']:
                break
            next_at = ReplayRuntime._next_scoring_boundary(datetime.fromisoformat(status['processed_at']))
            if next_at > target:
                break
            completed.append(self.process_one())
        return completed


    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._run_event.wait(timeout=0.25):
                continue
            try:
                result = self.process_one()
                self._last_error = None
                if result.get('status') == 'completed':
                    self._run_event.clear()
            except Exception as error:
                # Fail closed: never advance the committed watermark after a
                # failed transaction. Operator may inspect status and resume.
                self._last_error = f'{type(error).__name__}: {error}'[:1000]
                self._run_event.clear()
                continue
            self._stop_event.wait(self.interval_seconds)

    def start_worker(self, *, running: bool = False) -> dict:
        if self._worker is None or not self._worker.is_alive():
            self._stop_event.clear()
            self._worker = threading.Thread(target=self._worker_loop,
                                            name='tgoat-simulated-live', daemon=True)
            self._worker.start()
        if running:
            self._run_event.set()
        return self.status()

    def resume(self) -> dict:
        self.start_worker()
        if not self.status()['finished']:
            self._run_event.set()
        return self.status()

    def pause(self) -> dict:
        self._run_event.clear()
        return self.status()

    def close(self) -> None:
        self._run_event.clear()
        self._stop_event.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=max(1.0, min(self.interval_seconds, 3.0)))
