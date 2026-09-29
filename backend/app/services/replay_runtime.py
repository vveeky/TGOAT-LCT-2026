"""Digest-pinned historical replay using the canonical operational stack."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4

from backend.app.config import as_utc, utc_now
from backend.app.services.ml_features import (
    FeatureProviderError, HourlyState, InMemoryStateStore, RawObservation, StateFeatureProvider,
    is_canonical_snapshot,
)
from backend.app.services.operational_actions import SQLiteOperationalActionRepository
from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow
from backend.app.services.operational_cycle import (
    OperationalForecastCycleService, SQLiteOperationalCycleRepository,
)
from backend.app.services.operational_notifications import SQLiteOperationalNotificationRepository
from backend.app.services.operational_read_model import OperationalRiskReadService
from backend.app.services.prediction_history import OperationalForecastService, SQLitePredictionHistoryRepository
from backend.app.services.recommendations import RecommendationService, SQLiteRecommendationRepository
from backend.app.services.reference_data import get_channels_data, get_objects_data, get_reference_meta
from backend.app.services.risk_inference import RiskInferenceService
from ml.inference.load_risk_model import load_frozen_model


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "ml/data/demo/replay/replay_manifest.json"


class ReplayConfigurationError(ValueError):
    """Replay inputs do not satisfy the frozen runtime contract."""


class ReplayTransitionError(RuntimeError):
    """A manual replay transition is invalid for the current state."""


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ReplayConfigurationError(f"invalid replay timestamp: {value!r}") from error


class ReplayClock:
    def __init__(self, start: datetime):
        self._lock = threading.RLock()
        self._monotonic = time.monotonic
        self._virtual = as_utc(start)
        self._wall_anchor = self._monotonic()
        self._speed = 1800
        self._running = False
        self._end: datetime | None = None

    def now(self) -> datetime:
        with self._lock:
            return min(self._virtual, self._end) if self._end else self._virtual

    def target_now(self) -> datetime:
        """Unprocessed clock target; API reads must use committed ``now``."""
        with self._lock:
            value = self._virtual
            if self._running:
                value += timedelta(seconds=(self._monotonic() - self._wall_anchor) * self._speed)
            return min(value, self._end) if self._end else value

    def commit(self, at: datetime) -> None:
        """Advance authoritative replay time only after work through it is done."""
        with self._lock:
            wall_now = self._monotonic()
            target = self._virtual
            if self._running:
                target += timedelta(seconds=(wall_now - self._wall_anchor) * self._speed)
            selected = min(max(self._virtual, as_utc(at)), target)
            self._virtual = selected
            if self._end:
                self._virtual = min(self._virtual, self._end)
            remaining = max(0.0, (target - self._virtual).total_seconds())
            self._wall_anchor = wall_now - (remaining / self._speed if self._running else 0.0)

    def configure(self, *, at: datetime | None = None, speed: int | None = None,
                  running: bool | None = None, end: datetime | None = None) -> None:
        with self._lock:
            wall_now = self._monotonic()
            old_target = self._virtual
            if self._running:
                old_target += timedelta(seconds=(wall_now - self._wall_anchor) * self._speed)
                if self._end:
                    old_target = min(old_target, self._end)
            if at is not None:
                current = as_utc(at)
            else:
                # _virtual is the processed-through watermark. Reconfiguring a
                # running clock (notably changing speed) must never promote an
                # unprocessed target to authoritative replay time.
                current = self._virtual
            self._virtual = current
            if speed is not None:
                if not 1 <= speed <= 3600:
                    raise ValueError("speed must be an integer from 1 through 3600")
                self._speed = speed
            if end is not None:
                self._end = as_utc(end)
            if running is not None:
                self._running = running
            self._wall_anchor = wall_now
            if self._running and at is None and old_target > current:
                # Preserve accumulated backpressure lag while applying the new
                # rate only to future wall time.
                lag_seconds = (old_target - current).total_seconds()
                self._wall_anchor -= lag_seconds / self._speed

    @property
    def speed(self) -> int:
        with self._lock:
            return self._speed

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running


class ReplayFeatureProvider:
    """Fail closed for objects not declared covered by the bounded source."""

    def __init__(self, delegate: StateFeatureProvider, covered_ids: set[int]):
        self.delegate = delegate
        self.covered_ids = covered_ids

    def snapshot(self, object_id: int, snapshot_ts: datetime):
        if object_id not in self.covered_ids:
            raise FeatureProviderError("replay object coverage is incomplete")
        return self.delegate.snapshot(object_id, snapshot_ts)

    def metadata(self):
        return self.delegate.metadata()


def load_replay_manifest(path: str | Path) -> tuple[dict, list[HourlyState]]:
    manifest_path = Path(path).expanduser().resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReplayConfigurationError("cannot read replay manifest") from error
    required = {"replay_id", "source_file", "source_sha256", "reference_snapshot_id",
                "reference_sha256", "artifact_sha256", "object_ids", "covered_object_ids",
                "warmup_start", "replay_start", "replay_end", "source_timezone",
                "default_speed", "source_row_count", "hourly_state_row_count"}
    if not required <= manifest.keys():
        raise ReplayConfigurationError(f"manifest missing: {sorted(required - manifest.keys())}")
    source_path = (manifest_path.parent / manifest["source_file"]).resolve()
    try:
        source_digest = hashlib.sha256()
        with source_path.open("rb") as digest_stream:
            for block in iter(lambda: digest_stream.read(1024 * 1024), b""):
                source_digest.update(block)
        digest = source_digest.hexdigest()
    except OSError as error:
        raise ReplayConfigurationError("replay state source is unavailable") from error
    if digest != manifest["source_sha256"]:
        raise ReplayConfigurationError("replay state source digest mismatch")
    reference = get_reference_meta()
    if manifest["reference_snapshot_id"] != f"reference-v{reference['schema_version']}" \
            or manifest["reference_sha256"] != reference["source_sha256"]:
        raise ReplayConfigurationError("replay reference snapshot/hash mismatch")
    model = load_frozen_model()
    model_manifest = json.loads((ROOT / "ml/artifacts/risk_baseline_v1/phase2_manifest.json").read_text())
    if manifest["artifact_sha256"] != model_manifest["sha256"] or len(model.features) != 87:
        raise ReplayConfigurationError("replay frozen-model identity mismatch")
    operational_ids = sorted(int(item["object_id"]) for item in get_objects_data() if item["has_channels"])
    if sorted(map(int, manifest["object_ids"])) != operational_ids:
        raise ReplayConfigurationError("replay operational population differs from current reference")
    warmup = as_utc(_parse_datetime(manifest["warmup_start"]))
    start = as_utc(_parse_datetime(manifest["replay_start"]))
    end = as_utc(_parse_datetime(manifest["replay_end"]))
    if warmup > start - timedelta(hours=168) or end <= start:
        raise ReplayConfigurationError("replay warmup/end does not satisfy the time contract")
    if not is_canonical_snapshot(start) or not is_canonical_snapshot(end):
        raise ReplayConfigurationError("replay bounds must be canonical 6-hour snapshots")
    if not 1 <= int(manifest["default_speed"]) <= 3600:
        raise ReplayConfigurationError("unsupported default replay speed")
    rows: list[HourlyState] = []
    try:
        with gzip.open(source_path, "rt", encoding="utf-8") as source:
            for line in source:
                payload = json.loads(line)
                for field in ("hour_ts", "last_event_ts", "last_alarm_ts", "last_onset_ts",
                              "last_malfunction_text_ts"):
                    if payload.get(field):
                        payload[field] = _parse_datetime(payload[field])
                rows.append(HourlyState(**payload))
    except (OSError, json.JSONDecodeError, TypeError) as error:
        raise ReplayConfigurationError("invalid replay state source") from error
    if len(rows) != int(manifest["hourly_state_row_count"]):
        raise ReplayConfigurationError("replay state row count mismatch")
    source_rows = len(rows)
    if source_rows == 0 or any(as_utc(row.hour_ts) > end for row in rows):
        raise ReplayConfigurationError("empty replay data or future rows exceed replay boundary")
    covered_ids = set(map(int, manifest["covered_object_ids"]))
    if not covered_ids <= set(operational_ids):
        raise ReplayConfigurationError("covered object set contains unknown operational IDs")
    return manifest, rows


def load_replay_event_source(manifest: dict, manifest_path: str | Path = DEFAULT_MANIFEST) -> list[RawObservation]:
    """Load the independently pinned, replay-window-only registered events."""
    required = {"event_source_file", "event_source_sha256", "event_source_row_count",
                "event_source_upstream_sha256"}
    if not required <= manifest.keys():
        raise ReplayConfigurationError("replay event-level source is missing")
    manifest_file = Path(manifest_path).expanduser().resolve()
    path = (manifest_file.parent / manifest["event_source_file"]).resolve()
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != manifest["event_source_sha256"]:
            raise ReplayConfigurationError("replay event source digest mismatch")
        if manifest["event_source_upstream_sha256"] != manifest["upstream_source_sha256"]:
            raise ReplayConfigurationError("replay event source upstream identity mismatch")
        start = as_utc(_parse_datetime(manifest["replay_start"]))
        end = as_utc(_parse_datetime(manifest["replay_end"]))
        channel_map = {int(item["channel_id"]): item for item in get_channels_data()
                       if int(item["object_id"]) in set(map(int, manifest["object_ids"]))}
        events: list[RawObservation] = []
        previous: datetime | None = None
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line in source:
                payload = json.loads(line)
                payload["timestamp"] = _parse_datetime(payload["timestamp"])
                timestamp = as_utc(payload["timestamp"])
                channel = channel_map.get(int(payload["channel_id"]))
                if (channel is None or int(channel["object_id"]) != int(payload["object_id"])
                        or not start < timestamp <= end or (previous and timestamp < previous)):
                    raise ReplayConfigurationError("replay event source violates scope/order contract")
                previous = timestamp
                events.append(RawObservation(**payload))
    except ReplayConfigurationError:
        raise
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ReplayConfigurationError("invalid replay event source") from error
    if len(events) != int(manifest["event_source_row_count"]):
        raise ReplayConfigurationError("replay event source row count mismatch")
    return events


class ReplayRuntime:
    """Owns a replay-only SQLite session and advances canonical cycles."""

    def __init__(self, manifest: dict, rows: list[HourlyState], *, events: list[RawObservation] | None = None,
                 auto_start: bool = False,
                 session_root: str | Path | None = None):
        self.manifest = manifest
        self.rows = sorted(rows, key=lambda row: (row.hour_ts, row.object_id, row.channel_id))
        self.events = sorted(events or [], key=lambda row: (as_utc(row.timestamp), row.object_id, row.channel_id))
        self.start_at = as_utc(_parse_datetime(manifest["replay_start"]))
        self.end_at = as_utc(_parse_datetime(manifest["replay_end"]))
        self.warmup_start = as_utc(_parse_datetime(manifest["warmup_start"]))
        self.object_ids = sorted(map(int, manifest["object_ids"]))
        self.covered_ids = set(map(int, manifest["covered_object_ids"]))
        self.clock = ReplayClock(self.start_at)
        self.clock.configure(speed=int(manifest["default_speed"]), end=self.end_at)
        self.auto_start = auto_start
        self.session_root = Path(session_root) if session_root else None
        self.lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._step_thread: threading.Thread | None = None
        self._step_cancel = threading.Event()
        self._continuous_pause = threading.Event()
        self._stop = threading.Event()
        self._next_inference = self._next_scoring_boundary(self.start_at)
        self._event_index = 0
        self._open_hour_events: list[RawObservation] = []
        self._events_since_score: list[RawObservation] = []
        self._scoring_rows: list[dict] = []
        self._processed_boundaries = 0
        self._last_scored_at = self.start_at
        self._pending_step_target: datetime | None = None
        self._time_driven_object_count = 0
        self._last_boundary_duration_seconds = 0.0
        self._max_boundary_duration_seconds = 0.0
        self._hour_row_index = 0
        while (self._hour_row_index < len(self.rows)
               and as_utc(self.rows[self._hour_row_index].hour_ts) <= self.start_at):
            self._hour_row_index += 1
        self.state = "ready"
        self.context: dict = {}
        self._context_callback: Callable[[dict], None] | None = None
        self._retired_sessions: list[tuple[dict, Path]] = []
        self._new_session()
        self._run_cycle(self.start_at, "replay_prepare")

    @staticmethod
    def _next_scoring_boundary(after: datetime) -> datetime:
        value = as_utc(after)
        # Canonical hh:59:59 is the initial baseline: wait a full five virtual
        # minutes instead of producing a redundant hh+1:00:00 inference.
        alignment = value + timedelta(minutes=5) if is_canonical_snapshot(value) else value
        minute = (alignment.minute // 5) * 5
        boundary = alignment.replace(minute=minute, second=0, microsecond=0)
        if boundary < alignment:
            boundary += timedelta(minutes=5)
        if boundary <= value:
            boundary += timedelta(minutes=5)
        return boundary

    def _new_session(self) -> None:
        if self.session_root:
            self.session_root.mkdir(parents=True, exist_ok=True)
            self.session_dir = Path(tempfile.mkdtemp(prefix="session-", dir=self.session_root))
        else:
            self.session_dir = Path(tempfile.mkdtemp(prefix="tgoat-replay-session-"))
        self.session_id = f"replay:{self.manifest['replay_id']}:{uuid4().hex}"
        reference = get_reference_meta()
        initial = [row for row in self.rows if as_utc(row.hour_ts) <= self.start_at]
        store = InMemoryStateStore(
            initial, self._reference_counts(), self._system_reference_counts(),
            available_from=self.start_at - timedelta(hours=168), available_to=self.start_at,
            reference_snapshot_id=f"reference-v{reference['schema_version']}",
            reference_hash=reference["source_sha256"],
            raw_observations=[], event_coverage_from=self.start_at, event_coverage_to=self.start_at,
        )
        provider = ReplayFeatureProvider(StateFeatureProvider(
            store, expected_reference_snapshot_id=f"reference-v{reference['schema_version']}",
            expected_reference_hash=reference["source_sha256"], require_complete_coverage=True,
        ), self.covered_ids)
        db_path = self.session_dir / "operational.sqlite"
        history_repo = SQLitePredictionHistoryRepository(db_path)
        notifications = SQLiteOperationalNotificationRepository(db_path)
        recommendation_repo = SQLiteRecommendationRepository(db_path)
        forecast = OperationalForecastService(
            history_repo, RecommendationService(recommendation_repo), notifications,
        )
        cycle_repo = SQLiteOperationalCycleRepository(db_path)
        cycle = OperationalForecastCycleService(
            RiskInferenceService(provider, clock=self.clock.now), forecast, cycle_repo,
            object_source=lambda: self.object_ids, expected_object_count=len(self.object_ids),
        )
        actions = SQLiteOperationalActionRepository(db_path)
        read_model = OperationalRiskReadService(forecast, cycle_repo,
                                                lambda: self.object_ids, clock=self.clock.now,
                                                latest_snapshot_provider=lambda: self._last_scored_at,
                                                replay_mode=True)
        self.context = {
            "feature_store": store, "provider": provider, "cycle": cycle,
            "cycle_repository": cycle_repo, "forecast": forecast,
            "notifications": notifications, "actions": actions, "read_model": read_model,
            "workflow": SQLiteDispatcherWorkflow(history_repo),
            "db_path": str(db_path), "session_dir": str(self.session_dir),
        }
        if self._context_callback:
            self._context_callback(self.context)

    def _reference_counts(self):
        from backend.app.services.reference_data import get_channels_by_object
        return {object_id: len(channels) for object_id, channels in get_channels_by_object().items()
                if object_id in self.object_ids}

    def _system_reference_counts(self):
        from backend.app.services.ml_features import SYSTEM_NAMES
        from backend.app.services.reference_data import get_channels_by_object
        counts = {}
        for object_id, channels in get_channels_by_object().items():
            if object_id not in self.object_ids:
                continue
            for channel in channels:
                name = SYSTEM_NAMES.get(channel["system_type"], channel["system_type"])
                key = (object_id, name)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def bind_context(self, callback: Callable[[dict], None]) -> None:
        self._context_callback = callback
        callback(self.context)

    def _run_cycle(self, snapshot: datetime, mode: str) -> None:
        self.context["cycle"].run_once(snapshot_ts=snapshot, run_mode=mode)

    def _reveal(self, through: datetime) -> None:
        store = self.context["feature_store"]
        through = as_utc(through)
        rows = []
        while self._hour_row_index < len(self.rows):
            row = self.rows[self._hour_row_index]
            if as_utc(row.hour_ts) + timedelta(hours=1, seconds=-1) > through:
                break
            rows.append(row)
            self._hour_row_index += 1
        if rows:
            store.append(rows)
        while self._event_index < len(self.events) and as_utc(self.events[self._event_index].timestamp) <= through:
            event = self.events[self._event_index]
            if as_utc(event.timestamp) > self.start_at:
                self._events_since_score.append(event)
                event_hour = as_utc(event.timestamp).replace(minute=0, second=0, microsecond=0)
                if self._open_hour_events and as_utc(self._open_hour_events[-1].timestamp).replace(
                        minute=0, second=0, microsecond=0) != event_hour:
                    self._open_hour_events = []
                self._open_hour_events.append(event)
            self._event_index += 1
        bucket_start = through.replace(minute=0, second=0, microsecond=0)
        hour_events = [event for event in self._open_hour_events
                       if bucket_start <= as_utc(event.timestamp) <= through]
        coverage_from = max(self.start_at, bucket_start)
        store.replace_event_window(hour_events, available_from=coverage_from, available_to=through)
        store.available_to = through

    def _score_boundary(self, boundary: datetime, *, terminal: bool = False) -> None:
        started = time.perf_counter()
        self._reveal(boundary)
        # Four frozen features are recency values; they change as as_of advances
        # even with no new events. Thus every covered object is time-dirty at
        # each boundary; telemetry arrivals are recorded separately as reasons.
        recent_events = [event for event in self._events_since_score
                         if self._last_scored_at < as_utc(event.timestamp) <= boundary]
        events_by_object: dict[int, int] = {}
        for event in recent_events:
            events_by_object[event.object_id] = events_by_object.get(event.object_id, 0) + 1
        object_ids = sorted(self.covered_ids)
        results = self.context["cycle"].inference_service.batch_predict(
            [(object_id, boundary) for object_id in object_ids]
        )
        if len(results) != len(object_ids):
            raise ReplayConfigurationError("replay inference batch cardinality mismatch")
        # Workflow/read endpoints share this repository RLock. Locking the
        # whole batch hides partial boundary writes from concurrent API reads.
        with self.context["workflow"].lock:
            for result in results:
                result["generated_at"] = boundary.isoformat()
            written = self.context["forecast"].record_batch(results)
            for record, card, inserted in written:
                self._scoring_rows.append({
                    "object_id": record.prediction.object_id,
                    "snapshot_ts": record.prediction.snapshot_ts.isoformat(),
                    "risk_probability": record.prediction.risk_probability,
                    "risk_flag": record.prediction.risk_flag,
                    "status": record.prediction.prediction_status,
                    "reason": "telemetry_and_time" if events_by_object.get(record.prediction.object_id)
                              else "time_driven_recency",
                    "event_count_since_last_score": events_by_object.get(record.prediction.object_id, 0),
                    "prediction_identity": record.identity,
                    "forecast_card_id": card.card_id if card else None,
                    "forecast_card_status": card.status if card else None,
                    "inserted": inserted,
                    "terminal": terminal,
                })
            self._time_driven_object_count = len(object_ids)
            self._last_scored_at = as_utc(boundary)
            self._processed_boundaries += 1
            duration = time.perf_counter() - started
            self._last_boundary_duration_seconds = duration
            self._max_boundary_duration_seconds = max(self._max_boundary_duration_seconds, duration)
            self._events_since_score = [event for event in self._events_since_score
                                        if as_utc(event.timestamp) > boundary]
            if self.clock.running:
                self.clock.commit(boundary)
            else:
                self.clock.configure(at=boundary, running=False)

    def _pump(self) -> None:
        while not self._stop.wait(0.2):
            self.advance_due(self.clock.target_now())

    def advance_due(self, now: datetime) -> None:
        """Catch up every 5-minute boundary chronologically before exposing time."""
        with self.lock:
            if self.state != "running":
                return
            now = min(as_utc(now), self.end_at)
            while self._next_inference <= now:
                if self._continuous_pause.is_set():
                    self.state = "paused"
                    self.clock.configure(running=False)
                    return
                snapshot = self._next_inference
                try:
                    self._score_boundary(snapshot)
                except Exception:
                    self.state = "paused"
                    self.last_error = "Replay boundary failed; resume retries the same prediction identities."
                    self.clock.configure(running=False)
                    break
                self.clock.commit(snapshot)
                self._next_inference += timedelta(minutes=5)
                if self._continuous_pause.is_set():
                    self.state = "paused"
                    self.clock.configure(running=False)
                    return
            if self._continuous_pause.is_set():
                self.state = "paused"
                self.clock.configure(running=False)
                return
            if now >= self.end_at and self.state == "running":
                if self._last_scored_at < self.end_at:
                    try:
                        self._score_boundary(self.end_at, terminal=True)
                    except Exception:
                        self.state = "paused"
                        self.last_error = "Replay final boundary failed; resume retries it safely."
                        self.clock.configure(running=False)
                        return
                self._reveal(self.end_at)
                self.clock.commit(self.end_at)
                self.state = "completed"
                self.clock.configure(at=self.end_at, running=False)
            elif self.state == "running":
                self._reveal(now)
                self.clock.commit(now)

    def start_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._pump, name="tgoat-replay-clock", daemon=True)
            self._thread.start()

    def start(self) -> dict:
        with self.lock:
            if self.state in {"ready", "paused"}:
                if self._pending_step_target is not None:
                    self._step_cancel.clear()
                    self.state = "running"
                    self.last_error = None
                    self._start_step_worker_locked()
                    return self.status()
                self._continuous_pause.clear()
                self.state = "running"
                self.clock.configure(running=True)
        self.start_worker()
        return self.status()

    def pause(self) -> dict:
        step_thread = None
        if self._pending_step_target is not None:
            self._step_cancel.set()
        elif self.state == "running":
            # Set before acquiring the runtime lock: advance_due holds that
            # lock while scoring and will stop after its in-flight boundary.
            self._continuous_pause.set()
        with self.lock:
            if self.state == "running":
                if self._pending_step_target is not None:
                    self._step_cancel.set()
                    step_thread = self._step_thread
                else:
                    self.clock.configure(running=False)
                    self.state = "paused"
        if step_thread and step_thread is not threading.current_thread():
            step_thread.join()
        return self.status()

    def resume(self) -> dict:
        return self.start()

    def _start_step_worker_locked(self) -> None:
        if self._step_thread is None or not self._step_thread.is_alive():
            self._step_thread = threading.Thread(
                target=self._run_step_target, name="tgoat-replay-step", daemon=True,
            )
            self._step_thread.start()

    def _run_step_target(self) -> None:
        with self.lock:
            target = self._pending_step_target
        if target is None:
            return
        try:
            while True:
                with self.lock:
                    if self._step_cancel.is_set():
                        self.state = "paused"
                        self.clock.configure(running=False)
                        return
                    if self._next_inference > target:
                        if target >= self.end_at and self._last_scored_at < self.end_at:
                            self._score_boundary(self.end_at, terminal=True)
                        self._reveal(target)
                        self.clock.configure(at=target, running=False)
                        self._pending_step_target = None
                        self.state = "completed" if target >= self.end_at else "paused"
                        return
                    boundary = self._next_inference
                    self._score_boundary(boundary)
                    self._next_inference += timedelta(minutes=5)
        except Exception:
            with self.lock:
                self.state = "paused"
                self.clock.configure(running=False)
                self.last_error = "Replay step paused; resume retries the same six-hour target safely."

    def step(self) -> dict:
        """Queue a six-hour virtual step; each five-minute boundary commits atomically."""
        with self.lock:
            if self.state not in {"ready", "paused"}:
                raise ReplayTransitionError("replay step requires ready or paused state")
            if self._next_inference > self.end_at:
                self.state = "completed"
                self.clock.configure(at=self.end_at, running=False)
                return self.status()
            self._pending_step_target = self._pending_step_target or min(
                self.clock.now() + timedelta(hours=6), self.end_at,
            )
            self._step_cancel.clear()
            self.last_error = None
            self.state = "running"
            self.clock.configure(running=False)
            self._start_step_worker_locked()
            return self.status()

    def set_speed(self, speed: int) -> dict:
        with self.lock:
            if self.state == "running" and self._pending_step_target is None:
                self.advance_due(self.clock.target_now())
            self.clock.configure(speed=speed, running=self.state == "running" and self._pending_step_target is None)
        return self.status()

    def reset(self) -> dict:
        if self._pending_step_target is not None:
            self._step_cancel.set()
        with self.lock:
            if self._step_thread and self._step_thread.is_alive():
                self._step_cancel.set()
                step_thread = self._step_thread
            else:
                step_thread = None
        if step_thread and step_thread is not threading.current_thread():
            step_thread.join()
        with self.lock:
            self.clock.configure(at=self.start_at, running=False)
            self._continuous_pause.clear()
            old_context = self.context
            old_dir = self.session_dir
            self.state = "ready"
            self._next_inference = self._next_scoring_boundary(self.start_at)
            self._event_index = 0
            self._open_hour_events = []
            self._events_since_score = []
            self._scoring_rows = []
            self._processed_boundaries = 0
            self._last_scored_at = self.start_at
            self._pending_step_target = None
            self._time_driven_object_count = 0
            self.last_error = None
            self._hour_row_index = 0
            while (self._hour_row_index < len(self.rows)
                   and as_utc(self.rows[self._hour_row_index].hour_ts) <= self.start_at):
                self._hour_row_index += 1
            self._new_session()
            self._run_cycle(self.start_at, "replay_prepare")
            # Keep the old, now-detached session isolated until shutdown so an
            # in-flight read cannot race a closed SQLite connection during reset.
            self._retired_sessions.append((old_context, old_dir))
        return self.status()

    @staticmethod
    def _close_context(context: dict) -> None:
        for key in ("actions", "cycle_repository", "forecast"):
            value = context.get(key)
            if key == "forecast":
                for nested in (getattr(value, "repository", None),
                               getattr(getattr(value, "recommendation_service", None), "repository", None),
                               getattr(value, "notification_repository", None)):
                    if hasattr(nested, "close"):
                        nested.close()
            elif hasattr(value, "close"):
                value.close()

    def status(self) -> dict:
        current = min(self.clock.now(), self.end_at)
        return {
            "mode": "replay", "state": self.state, "replay_id": self.manifest["replay_id"],
            "session_id": self.session_id, "virtual_time": current.isoformat(),
            "start_time": self.start_at.isoformat(), "end_time": self.end_at.isoformat(),
            "speed": self.clock.speed, "progress": max(0.0, min(1.0,
                (current - self.start_at).total_seconds() /
                max(1.0, (self.end_at - self.start_at).total_seconds()))),
            "next_inference_at": self._next_inference.isoformat() if self._next_inference <= self.end_at else None,
            "scoring_interval_seconds": 300,
            "processed_scoring_boundaries": self._processed_boundaries,
            "pending_step": self._pending_step_target is not None,
            "pending_step_target": (self._pending_step_target.isoformat()
                                    if self._pending_step_target is not None else None),
            "last_scored_at": self._last_scored_at.isoformat(),
            "time_driven_object_count": self._time_driven_object_count,
            "last_boundary_duration_seconds": self._last_boundary_duration_seconds,
            "max_boundary_duration_seconds": self._max_boundary_duration_seconds,
            "latest_scoring_rows": self._scoring_rows[-100:],
            "last_error": getattr(self, "last_error", None),
            "covered_object_count": len(self.covered_ids),
            "operational_object_count": len(self.object_ids),
        }

    def close(self) -> None:
        self._stop.set()
        self._step_cancel.set()
        if self._step_thread and self._step_thread.is_alive():
            self._step_thread.join()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._close_context(self.context)
        for context, directory in self._retired_sessions:
            self._close_context(context)
            shutil.rmtree(directory, ignore_errors=True)
        shutil.rmtree(self.session_dir, ignore_errors=True)


def replay_runtime_from_environment() -> ReplayRuntime:
    manifest_path = Path(os.environ.get("TGOAT_REPLAY_MANIFEST", DEFAULT_MANIFEST))
    manifest, rows = load_replay_manifest(manifest_path)
    events = load_replay_event_source(manifest, manifest_path)
    speed = int(os.environ.get("TGOAT_REPLAY_SPEED", manifest["default_speed"]))
    if not 1 <= speed <= 3600:
        raise ReplayConfigurationError("TGOAT_REPLAY_SPEED must be between 1 and 3600")
    manifest = {**manifest, "default_speed": speed}
    auto_start = os.environ.get("TGOAT_REPLAY_AUTO_START", "false").strip().lower() in {"1", "true", "yes"}
    session_root = os.environ.get("TGOAT_REPLAY_SESSION_ROOT")
    return ReplayRuntime(manifest, rows, events=events, auto_start=auto_start, session_root=session_root)
