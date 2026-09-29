"""Runtime feature-serving primitives for the frozen risk contract.

The provider consumes bounded, chronological hourly state records and, for an
open hour, an optional complete event-level slice. It does not read the
training dataset in an HTTP request and never invents values when source state
is unavailable. A mapping store remains test-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import sqlite3
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Iterable, Protocol

from backend.app.config import EVENT_SOURCE_TIMEZONE, as_utc, utc_now


ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "ml/artifacts/risk_baseline_v1/phase2_manifest.json"
WHOLE_DAY_NO_RECORDS = {"2024-04-07", "2024-04-08", "2026-06-01"}
SYSTEM_SLUGS = ("gas", "security", "dispatch", "fire", "temperature", "diagnostic")
SYSTEM_NAMES = {
    "Газовая охрана": "gas",
    "Охранная подсистема": "security",
    "Диспетчерский контроль": "dispatch",
    "Пожарная охрана": "fire",
    "Температурная подсистема": "temperature",
    "Диагностическая подсистема": "diagnostic",
}
WINDOWS = ((1, "1h"), (6, "6h"), (24, "24h"), (168, "7d"))
CANONICAL_SNAPSHOT_HOURS = (5, 11, 17, 23)


def is_canonical_snapshot(value: datetime) -> bool:
    local = as_utc(value).astimezone(EVENT_SOURCE_TIMEZONE)
    return (local.hour in CANONICAL_SNAPSHOT_HOURS and local.minute == 59
            and local.second == 59 and local.microsecond == 0)


def latest_canonical_snapshot(now: datetime | None = None,
                              source_latest: datetime | None = None) -> datetime:
    """Return the latest completed training-compatible snapshot in UTC."""
    cutoff = as_utc(source_latest or now or utc_now()).astimezone(EVENT_SOURCE_TIMEZONE)
    candidates = [cutoff.replace(hour=hour, minute=59, second=59, microsecond=0)
                  for hour in CANONICAL_SNAPSHOT_HOURS]
    eligible = [candidate for candidate in candidates if candidate <= cutoff]
    if not eligible:
        previous_day = cutoff - timedelta(days=1)
        return as_utc(previous_day.replace(hour=23, minute=59, second=59, microsecond=0))
    return as_utc(max(eligible))


def frozen_feature_names() -> tuple[str, ...]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    names = tuple(payload["features"])
    if len(names) != 87 or len(set(names)) != len(names):
        raise RuntimeError("frozen feature manifest is not an exact 87-feature schema")
    return names


FEATURE_NAMES = frozen_feature_names()


class FeatureProviderError(RuntimeError):
    """A feature vector cannot be produced without fabricating state."""


class FeatureSnapshotStore(Protocol):
    def snapshot(self, object_id: int, snapshot_ts: datetime) -> dict[str, object]: ...


@dataclass(frozen=True)
class RawObservation:
    object_id: int
    channel_id: int
    timestamp: datetime
    system_type: str
    is_alarm: bool
    sensor_value: str | None = None
    invalid_alarm: bool = False
    malfunction_text: bool = False
    special_code: int | None = None


@dataclass(frozen=True)
class _CanonicalTick:
    observation: RawObservation
    event_count: int
    alarm_count: int
    invalid_alarm_count: int
    numeric_value_count: int
    raw_value_count: int
    malfunction_text_count: int
    malfunction_text_channels: int
    special_code_counts: tuple[int, int, int, int, int]
    onset: bool
    previous_timestamp: datetime | None


@dataclass(frozen=True)
class HourlyState:
    object_id: int
    channel_id: int
    hour_ts: datetime
    system_type: str
    event_count: int = 0
    alarm_count: int = 0
    onset_count: int = 0
    active_channels: int = 0
    alarm_channels: int = 0
    invalid_alarm_count: int = 0
    numeric_value_count: int = 0
    raw_value_count: int = 0
    observed: bool = True
    last_event_ts: datetime | None = None
    malfunction_text_count: int = 0
    malfunction_text_channels: int = 0
    special_code_minus_100_count: int = 0
    special_code_255_count: int = 0
    special_code_minus_127_count: int = 0
    special_code_minus_3276_count: int = 0
    special_code_channels: int = 0
    last_alarm_ts: datetime | None = None
    last_onset_ts: datetime | None = None
    last_malfunction_text_ts: datetime | None = None
    missing_reference: bool = False
    # Preserves channel transition state for accurate open-hour hydration.
    last_alarm_state: bool | None = None


def _numeric(value: str | None) -> bool:
    if value is None:
        return False
    try:
        float(value.replace(",", "."))
        return True
    except (AttributeError, TypeError, ValueError):
        return False


def hydrate_hourly_state(
    observations: Iterable[RawObservation],
    *,
    initial_channel_state: dict[int, tuple[bool, datetime | None]] | None = None,
) -> list[HourlyState]:
    """Build bounded hourly state while carrying channel state across years.

    The caller supplies the chronological source slice plus enough pre-window
    history for each channel.  Onset reconstruction intentionally happens
    before any snapshot window is applied, so 31-Dec → 1-Jan and the 2021
    transition are not treated as resets.
    """
    grouped: dict[int, list[RawObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.channel_id, []).append(observation)
    ticks: list[_CanonicalTick] = []
    for channel_rows in grouped.values():
        by_timestamp: dict[datetime, list[RawObservation]] = {}
        for observation in channel_rows:
            by_timestamp.setdefault(as_utc(observation.timestamp), []).append(observation)
        previous_alarm, previous_timestamp = (initial_channel_state or {}).get(
            channel_rows[0].channel_id, (False, None)
        )
        for timestamp in sorted(by_timestamp):
            same_timestamp = by_timestamp[timestamp]
            observation = same_timestamp[0]
            current = any(row.is_alarm for row in same_timestamp)
            onset = current and not previous_alarm and previous_timestamp is not None and (
                timestamp <= as_utc(previous_timestamp) + timedelta(hours=24)
            )
            codes = [row.special_code for row in same_timestamp if row.special_code is not None]
            recognized_codes = [code for code in codes if code in {-100, 255, -127, -3276}]
            ticks.append(_CanonicalTick(
                observation=RawObservation(
                    object_id=observation.object_id, channel_id=observation.channel_id,
                    timestamp=observation.timestamp, system_type=observation.system_type,
                    is_alarm=current, sensor_value=observation.sensor_value,
                ),
                event_count=len(same_timestamp), alarm_count=sum(row.is_alarm for row in same_timestamp),
                invalid_alarm_count=sum(row.invalid_alarm for row in same_timestamp),
                numeric_value_count=sum(_numeric(row.sensor_value) for row in same_timestamp),
                raw_value_count=len(same_timestamp),
                malfunction_text_count=sum(row.malfunction_text for row in same_timestamp),
                malfunction_text_channels=int(any(row.malfunction_text for row in same_timestamp)),
                special_code_counts=(sum(code == -100 for code in codes), sum(code == 255 for code in codes),
                                     sum(code == -127 for code in codes), sum(code == -3276 for code in codes),
                                     int(bool(recognized_codes))),
                onset=onset, previous_timestamp=previous_timestamp,
            ))
            previous_alarm = current
            previous_timestamp = timestamp
    buckets: dict[tuple[int, int, datetime], list[_CanonicalTick]] = {}
    for item in ticks:
        observation = item.observation
        hour = as_utc(observation.timestamp).replace(minute=0, second=0, microsecond=0)
        buckets.setdefault((observation.object_id, observation.channel_id, hour), []).append(item)
    result: list[HourlyState] = []
    for (_, channel_id, hour), bucket in sorted(buckets.items(), key=lambda item: item[0]):
        first = bucket[0].observation
        last = max(bucket, key=lambda item: as_utc(item.observation.timestamp)).observation
        alarms = [item for item in bucket if item.observation.is_alarm]
        result.append(HourlyState(
            object_id=first.object_id, channel_id=channel_id, hour_ts=hour,
            system_type=SYSTEM_NAMES.get(first.system_type, first.system_type),
            event_count=sum(item.event_count for item in bucket),
            alarm_count=sum(item.alarm_count for item in bucket),
            onset_count=sum(item.onset for item in bucket),
            active_channels=1, alarm_channels=int(bool(alarms)),
            invalid_alarm_count=sum(item.invalid_alarm_count for item in bucket),
            numeric_value_count=sum(item.numeric_value_count for item in bucket),
            raw_value_count=sum(item.raw_value_count for item in bucket), observed=True,
            last_event_ts=last.timestamp,
            malfunction_text_count=sum(item.malfunction_text_count for item in bucket),
            malfunction_text_channels=int(any(item.malfunction_text_channels for item in bucket)),
            special_code_minus_100_count=sum(item.special_code_counts[0] for item in bucket),
            special_code_255_count=sum(item.special_code_counts[1] for item in bucket),
            special_code_minus_127_count=sum(item.special_code_counts[2] for item in bucket),
            special_code_minus_3276_count=sum(item.special_code_counts[3] for item in bucket),
            special_code_channels=int(any(item.special_code_counts[4] for item in bucket)),
            last_alarm_ts=hour if any(item.observation.is_alarm for item in bucket) else None,
            last_onset_ts=hour if any(item.onset for item in bucket) else None,
            last_malfunction_text_ts=max(
                (item.observation.timestamp for item in bucket if item.malfunction_text_count),
                default=None
            ),
            last_alarm_state=bool(last.is_alarm),
        ))
    return result


class InMemoryStateStore:
    """Small deterministic store for bounded hourly state and optional events.

    Production wiring can replace this object with a persisted hourly-state
    repository without changing provider semantics.
    """

    def __init__(self, rows: Iterable[HourlyState], reference_counts: dict[int, int],
                 system_reference_counts: dict[tuple[int, str], int] | None = None,
                 available_from: datetime | None = None, available_to: datetime | None = None,
                 reference_snapshot_id: str | None = None, reference_hash: str | None = None,
                 raw_observations: Iterable[RawObservation] | None = None,
                 event_coverage_from: datetime | None = None,
                 event_coverage_to: datetime | None = None):
        self._rows = sorted(rows, key=lambda row: (row.object_id, as_utc(row.hour_ts)))
        self._rows_by_object: dict[int, list[HourlyState]] = {}
        self._row_times_by_object: dict[int, list[datetime]] = {}
        self._index_state_rows()
        self.reference_counts = dict(reference_counts)
        self.system_reference_counts = dict(system_reference_counts or {})
        self.available_from = available_from
        self.available_to = available_to
        self.reference_snapshot_id = reference_snapshot_id
        self.reference_hash = reference_hash
        self.raw_observations = (None if raw_observations is None else
                                 sorted(raw_observations,
                                        key=lambda item: (item.object_id, as_utc(item.timestamp), item.channel_id)))
        self._raw_by_object: dict[int, list[RawObservation]] = {}
        self._raw_times_by_object: dict[int, list[datetime]] = {}
        self._index_raw_observations()
        self.event_coverage_from = event_coverage_from
        self.event_coverage_to = event_coverage_to

    def rows(self, object_id: int, start: datetime, end: datetime) -> list[HourlyState]:
        start, end = as_utc(start), as_utc(end)
        rows = self._rows_by_object.get(object_id, [])
        times = self._row_times_by_object.get(object_id, [])
        return rows[bisect_right(times, start):bisect_right(times, end)]

    def _index_state_rows(self) -> None:
        self._rows_by_object = {}
        self._row_times_by_object = {}
        for row in self._rows:
            self._rows_by_object.setdefault(row.object_id, []).append(row)
        for object_id, rows in self._rows_by_object.items():
            self._row_times_by_object[object_id] = [as_utc(row.hour_ts) for row in rows]

    def _index_raw_observations(self) -> None:
        self._raw_by_object = {}
        self._raw_times_by_object = {}
        for row in self.raw_observations or ():
            self._raw_by_object.setdefault(row.object_id, []).append(row)
        for object_id, rows in self._raw_by_object.items():
            rows.sort(key=lambda item: (as_utc(item.timestamp), item.channel_id))
            self._raw_times_by_object[object_id] = [as_utc(item.timestamp) for item in rows]

    def raw_window(self, object_id: int, start: datetime, end: datetime) -> list[RawObservation]:
        rows = self._raw_by_object.get(object_id, [])
        timestamps = self._raw_times_by_object.get(object_id, [])
        left = bisect_left(timestamps, as_utc(start))
        right = bisect_right(timestamps, as_utc(end))
        return rows[left:right]

    def append(self, rows: Iterable[HourlyState]) -> None:
        self._rows.extend(rows)
        self._rows.sort(key=lambda row: (row.object_id, as_utc(row.hour_ts), row.channel_id))
        self._index_state_rows()

    def replace_event_window(self, observations: Iterable[RawObservation], *,
                             available_from: datetime, available_to: datetime) -> None:
        """Replace a provenance-checked event slice used for open-hour scoring."""
        start, end = as_utc(available_from), as_utc(available_to)
        selected = list(observations)
        if start > end or any(not start <= as_utc(row.timestamp) <= end for row in selected):
            raise FeatureProviderError("event source rows fall outside declared coverage")
        self.raw_observations = sorted(
            selected, key=lambda item: (item.object_id, as_utc(item.timestamp), item.channel_id)
        )
        self._index_raw_observations()
        self.event_coverage_from = start
        self.event_coverage_to = end

    def channel_states_before(self, object_id: int,
                              timestamp: datetime) -> dict[int, tuple[bool, datetime | None]]:
        """Return the latest known alarm state per channel before a bucket."""
        boundary = as_utc(timestamp)
        latest: dict[int, HourlyState] = {}
        rows = self._rows_by_object.get(object_id, [])
        times = self._row_times_by_object.get(object_id, [])
        for row in rows[:bisect_left(times, boundary)]:
            prior = latest.get(row.channel_id)
            if prior is None or as_utc(row.hour_ts) > as_utc(prior.hour_ts):
                latest[row.channel_id] = row
        result: dict[int, tuple[bool, datetime | None]] = {}
        for channel_id, row in latest.items():
            last_ts = as_utc(row.last_event_ts) if row.last_event_ts else None
            if (last_ts is not None and boundary - last_ts <= timedelta(hours=24)
                    and row.last_alarm_state is None):
                raise FeatureProviderError("prior channel alarm state unavailable for open-hour overlay")
            result[channel_id] = (bool(row.last_alarm_state) if row.last_alarm_state is not None else False,
                                  last_ts)
        return result


class SQLiteFeatureStateStore(InMemoryStateStore):
    """Persisted bounded serving state behind the same provider interface.

    SQLite is deliberately an implementation detail for local/backend
    deployment. The provider sees bounded hydrated state plus an optional
    complete event slice for the open hour; an ingestion service can implement
    the same contract with another database.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS feature_state (payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS feature_meta (key TEXT PRIMARY KEY, value TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS feature_raw_observations (payload TEXT NOT NULL)")
            rows = [json.loads(item[0]) for item in connection.execute("SELECT payload FROM feature_state")]
            raw_rows = [json.loads(item[0]) for item in connection.execute(
                "SELECT payload FROM feature_raw_observations"
            )]
            meta = dict(connection.execute("SELECT key, value FROM feature_meta"))
        finally:
            connection.close()
        decoded = [_state_from_json(row) for row in rows]
        super().__init__(
            decoded,
            {int(key): int(value) for key, value in json.loads(meta.get("reference_counts", "{}" )).items()},
            {(int(key.split("|", 1)[0]), key.split("|", 1)[1]): int(value)
             for key, value in json.loads(meta.get("system_reference_counts", "{}" )).items()},
            datetime.fromisoformat(meta["available_from"]) if meta.get("available_from") else None,
            datetime.fromisoformat(meta["available_to"]) if meta.get("available_to") else None,
            meta.get("reference_snapshot_id"), meta.get("reference_hash"),
            ([_raw_observation_from_json(row) for row in raw_rows]
             if meta.get("event_source_enabled") == "true" else None),
            datetime.fromisoformat(meta["event_coverage_from"]) if meta.get("event_coverage_from") else None,
            datetime.fromisoformat(meta["event_coverage_to"]) if meta.get("event_coverage_to") else None,
        )

    @classmethod
    def create(cls, path: str | Path, *, rows: Iterable[HourlyState],
               reference_counts: dict[int, int],
               system_reference_counts: dict[tuple[int, str], int],
               available_from: datetime, available_to: datetime,
               reference_snapshot_id: str, reference_hash: str,
               raw_observations: Iterable[RawObservation] | None = None,
               event_coverage_from: datetime | None = None,
               event_coverage_to: datetime | None = None) -> "SQLiteFeatureStateStore":
        """Create a new persisted state without changing the existing file API."""
        store = cls.__new__(cls)
        store.path = str(path)
        InMemoryStateStore.__init__(
            store, rows, reference_counts, system_reference_counts,
            available_from, available_to, reference_snapshot_id, reference_hash,
            raw_observations, event_coverage_from, event_coverage_to,
        )
        store.persist()
        return store

    def persist(self) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS feature_state (payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS feature_meta (key TEXT PRIMARY KEY, value TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS feature_raw_observations (payload TEXT NOT NULL)")
            connection.execute("DELETE FROM feature_state")
            connection.execute("DELETE FROM feature_meta")
            connection.execute("DELETE FROM feature_raw_observations")
            connection.executemany(
                "INSERT INTO feature_state(payload) VALUES (?)",
                [(json.dumps(_state_to_json(row), sort_keys=True),) for row in self._rows],
            )
            if self.raw_observations is not None:
                connection.executemany(
                    "INSERT INTO feature_raw_observations(payload) VALUES (?)",
                    [(json.dumps(_raw_observation_to_json(row), sort_keys=True),)
                     for row in self.raw_observations],
                )
            meta = {
                "reference_counts": json.dumps(self.reference_counts, sort_keys=True),
                "system_reference_counts": json.dumps({f"{key[0]}|{key[1]}": value
                                                         for key, value in self.system_reference_counts.items()}, sort_keys=True),
                "available_from": self.available_from.isoformat() if self.available_from else "",
                "available_to": self.available_to.isoformat() if self.available_to else "",
                "reference_snapshot_id": self.reference_snapshot_id or "",
                "reference_hash": self.reference_hash or "",
                "event_source_enabled": "true" if self.raw_observations is not None else "false",
                "event_coverage_from": self.event_coverage_from.isoformat() if self.event_coverage_from else "",
                "event_coverage_to": self.event_coverage_to.isoformat() if self.event_coverage_to else "",
            }
            connection.executemany("INSERT INTO feature_meta(key, value) VALUES (?, ?)", meta.items())
            connection.commit()
        finally:
            connection.close()

    def replace_event_window(self, observations: Iterable[RawObservation], *,
                             available_from: datetime, available_to: datetime) -> None:
        """Replace the event overlay and persist it before reporting success."""
        previous = (self.raw_observations, self.event_coverage_from, self.event_coverage_to)
        super().replace_event_window(
            observations, available_from=available_from, available_to=available_to
        )
        try:
            self.persist()
        except Exception:
            self.raw_observations, self.event_coverage_from, self.event_coverage_to = previous
            raise


def _state_to_json(row: HourlyState) -> dict[str, object]:
    result = row.__dict__.copy()
    for key in ("hour_ts", "last_event_ts", "last_alarm_ts", "last_onset_ts", "last_malfunction_text_ts"):
        if result[key] is not None:
            result[key] = result[key].isoformat()
    return result


def _state_from_json(payload: dict[str, object]) -> HourlyState:
    for key in ("hour_ts", "last_event_ts", "last_alarm_ts", "last_onset_ts", "last_malfunction_text_ts"):
        if payload.get(key) is not None:
            payload[key] = datetime.fromisoformat(str(payload[key]))
    return HourlyState(**payload)


def _raw_observation_to_json(row: RawObservation) -> dict[str, object]:
    payload = row.__dict__.copy()
    payload["timestamp"] = row.timestamp.isoformat()
    return payload


def _raw_observation_from_json(payload: dict[str, object]) -> RawObservation:
    return RawObservation(**{**payload, "timestamp": datetime.fromisoformat(str(payload["timestamp"]))})


class StateFeatureProvider:
    """Build frozen features, replacing only the open bucket from raw events."""

    def __init__(self, state: InMemoryStateStore, *, expected_reference_snapshot_id: str | None = None,
                 expected_reference_hash: str | None = None,
                 require_complete_coverage: bool = False):
        self.state = state
        self.require_complete_coverage = require_complete_coverage
        if (expected_reference_snapshot_id is not None
                and state.reference_snapshot_id != expected_reference_snapshot_id):
            raise FeatureProviderError("reference snapshot mismatch")
        if expected_reference_hash is not None and state.reference_hash != expected_reference_hash:
            raise FeatureProviderError("reference hash mismatch")

    def metadata(self) -> dict[str, str | None]:
        """Expose reference/state identity without putting it into model features."""
        return {
            "reference_snapshot_id": self.state.reference_snapshot_id,
            "reference_hash": self.state.reference_hash,
            "available_from": as_utc(self.state.available_from).isoformat() if self.state.available_from else None,
            "available_to": as_utc(self.state.available_to).isoformat() if self.state.available_to else None,
            "event_source_available": "true" if self.state.raw_observations is not None else "false",
            "event_coverage_from": (as_utc(self.state.event_coverage_from).isoformat()
                                    if self.state.event_coverage_from else None),
            "event_coverage_to": (as_utc(self.state.event_coverage_to).isoformat()
                                  if self.state.event_coverage_to else None),
        }

    @staticmethod
    def _sum(rows: list[HourlyState], field: str) -> int:
        return sum(getattr(row, field) for row in rows)

    @staticmethod
    def _last(rows: list[HourlyState], field: str) -> datetime | None:
        values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
        return max(values) if values else None

    @staticmethod
    def _distinct_channels(rows: list[HourlyState], predicate=None) -> int:
        return len({row.channel_id for row in rows if predicate is None or predicate(row)})

    @staticmethod
    def _hours_since(value: datetime | None, snapshot_ts: datetime) -> float | None:
        return None if value is None else (as_utc(snapshot_ts) - as_utc(value)).total_seconds() / 3600

    @staticmethod
    def _closed_hour_snapshot(snapshot_ts: datetime) -> bool:
        return (snapshot_ts.minute == 59 and snapshot_ts.second == 59
                and snapshot_ts.microsecond == 0)

    def _open_hour_rows(self, object_id: int, snapshot_ts: datetime) -> list[HourlyState] | None:
        if self._closed_hour_snapshot(snapshot_ts):
            return None
        bucket_start = snapshot_ts.replace(minute=0, second=0, microsecond=0)
        raw = self.state.raw_observations
        covered_from = self.state.event_coverage_from
        covered_to = self.state.event_coverage_to
        if (raw is None or covered_from is None or covered_to is None
                or as_utc(covered_from) > as_utc(bucket_start)
                or as_utc(covered_to) < as_utc(snapshot_ts)):
            raise FeatureProviderError("complete event-level source for open hour is unavailable")
        selected = self.state.raw_window(object_id, bucket_start, snapshot_ts)
        initial = self.state.channel_states_before(object_id, bucket_start)
        return hydrate_hourly_state(selected, initial_channel_state=initial)

    def _window(self, object_id: int, snapshot_ts: datetime, hours: int,
                open_hour_rows: list[HourlyState] | None = None) -> list[HourlyState]:
        start = as_utc(snapshot_ts) - timedelta(hours=hours)
        rows = self.state.rows(object_id, start, snapshot_ts)
        if open_hour_rows is not None:
            bucket_start = snapshot_ts.replace(minute=0, second=0, microsecond=0)
            # Never combine a partial overlay with an already materialized full
            # hour: the latter may contain observations after snapshot_ts.
            rows = [row for row in rows if as_utc(row.hour_ts) != as_utc(bucket_start)]
            rows.extend(open_hour_rows)
            rows = [row for row in rows if start < as_utc(row.hour_ts) <= as_utc(snapshot_ts)]
        return rows

    def snapshot(self, object_id: int, snapshot_ts: datetime) -> dict[str, object]:
        snapshot_ts = as_utc(snapshot_ts)
        if self.require_complete_coverage:
            required_start = snapshot_ts - timedelta(hours=168)
            if self.state.available_from is None or self.state.available_to is None:
                raise FeatureProviderError("feature state coverage metadata unavailable")
            if as_utc(self.state.available_from) > required_start:
                raise FeatureProviderError("feature state history is incomplete")
            if as_utc(self.state.available_to) < snapshot_ts:
                raise FeatureProviderError("feature state source is stale")
        if object_id not in self.state.reference_counts:
            raise FeatureProviderError(f"reference unavailable for object {object_id}")
        open_hour_rows = self._open_hour_rows(object_id, snapshot_ts)
        rows_1, rows_6, rows_24, rows_7d = [self._window(object_id, snapshot_ts, h, open_hour_rows)
                                             for h, _ in WINDOWS]
        ref = self.state.reference_counts[object_id]
        values: dict[str, object] = {"current_reference_channel_count": float(ref)}
        for rows, hours in ((rows_1, 1), (rows_6, 6), (rows_24, 24), (rows_7d, 168)):
            label = f"{hours}h" if hours != 168 else "7d"
            for field in ("event_count", "alarm_count", "onset_count"):
                values[f"{field}_{label}"] = float(self._sum(rows, field))
        values["active_channels_24h"] = float(self._distinct_channels(
            rows_24, lambda row: row.active_channels > 0
        ))
        values["alarm_channels_24h"] = float(self._distinct_channels(
            rows_24, lambda row: row.alarm_count > 0
        ))
        for field in ("invalid_alarm_count", "numeric_value_count", "raw_value_count"):
            values[f"{field}_24h"] = float(self._sum(rows_24, field))
        last_event = self._last(rows_7d, "last_event_ts")
        values["observed_hours_24h"] = float(len({as_utc(row.hour_ts) for row in rows_24 if row.observed}))
        values["hours_since_last_observation_7d"] = self._hours_since(last_event, snapshot_ts)

        for slug in SYSTEM_SLUGS:
            system_rows = [row for row in rows_24 if row.system_type == slug]
            for field in ("event_count", "alarm_count", "onset_count"):
                values[f"system_{slug}_{field}_24h"] = float(self._sum(system_rows, field))
            values[f"system_{slug}_active_channels_24h"] = float(self._distinct_channels(
                system_rows, lambda row: row.active_channels > 0
            ))

        for hours, label in WINDOWS:
            window = self._window(object_id, snapshot_ts, hours, open_hour_rows)
            start = snapshot_ts - timedelta(hours=hours)
            overlaps_no_record_day = any(
                (start.date() <= datetime.fromisoformat(day).date() <= snapshot_ts.date())
                for day in WHOLE_DAY_NO_RECORDS
            )
            values[f"whole_day_no_records_in_lookback_{label}"] = overlaps_no_record_day
            values[f"lookback_complete_{label}"] = (
                self.state.available_from is not None
                and as_utc(start) >= as_utc(self.state.available_from)
                and (self.state.available_to is None or snapshot_ts <= as_utc(self.state.available_to))
                and not overlaps_no_record_day
            )
        values["has_recent_data_24h"] = bool(rows_24)
        values["channel_coverage_24h"] = (values["active_channels_24h"] / ref if ref else None)
        raw = self._sum(rows_24, "raw_value_count")
        values["numeric_parse_rate_24h"] = (self._sum(rows_24, "numeric_value_count") / raw
                                             if raw else None)
        diag = {
            "malfunction_text_count": "malfunction_text_count",
            "malfunction_text_channels": "malfunction_text_channels",
            "special_code_minus_100_count": "special_code_minus_100_count",
            "special_code_255_count": "special_code_255_count",
            "special_code_minus_127_count": "special_code_minus_127_count",
            "special_code_minus_3276_count": "special_code_minus_3276_count",
            "special_code_channels": "special_code_channels",
        }
        for name, field in diag.items():
            for rows, label in ((rows_1, "1h"), (rows_6, "6h"), (rows_24, "24h"), (rows_7d, "7d")):
                if name not in {"malfunction_text_count"} and label != "24h":
                    continue
                if name in {"malfunction_text_channels", "special_code_channels"}:
                    values[f"{name}_{label}"] = float(self._distinct_channels(
                        rows, lambda row: getattr(row, field) > 0
                    ))
                else:
                    values[f"{name}_{label}"] = float(self._sum(rows, field))
        values["hours_since_last_malfunction_text_7d"] = self._hours_since(
            self._last(rows_7d, "last_malfunction_text_ts"), snapshot_ts)
        values["hours_since_last_alarm_hour_7d"] = self._hours_since(
            self._last(rows_7d, "last_alarm_ts"), snapshot_ts)
        values["hours_since_last_onset_hour_7d"] = self._hours_since(
            self._last(rows_7d, "last_onset_ts"), snapshot_ts)
        rates = {label: values[f"alarm_count_{label}"] / values[f"event_count_{label}"]
                 if values[f"event_count_{label}"] else None
                 for _, label in WINDOWS}
        for label, value in rates.items():
            values[f"alarm_rate_{label}"] = value
        values["alarm_rate_delta_6h_vs_24h"] = (rates["6h"] - rates["24h"]
                                                if rates["6h"] is not None and rates["24h"] is not None else None)
        values["alarm_rate_delta_24h_vs_7d"] = (rates["24h"] - rates["7d"]
                                                 if rates["24h"] is not None and rates["7d"] is not None else None)
        values["events_per_active_channel_24h"] = (values["event_count_24h"] / values["active_channels_24h"]
                                                    if values["active_channels_24h"] else None)
        for slug in SYSTEM_SLUGS:
            current = self.state.system_reference_counts.get((object_id, slug), 0)
            values[f"system_{slug}_current_reference_channels"] = float(current)
            values[f"system_{slug}_coverage_24h"] = (
                values[f"system_{slug}_active_channels_24h"] / current
                if current else None
            )
        missing = set(FEATURE_NAMES) - values.keys()
        extra = values.keys() - set(FEATURE_NAMES)
        if missing or extra:
            raise FeatureProviderError(f"feature schema mismatch missing={sorted(missing)} extra={sorted(extra)}")
        return {name: values[name] for name in FEATURE_NAMES}


class MappingFeatureStore:
    """Explicit test adapter for existing golden rows; never a production fallback."""

    def __init__(self, rows: dict[tuple[int, str], list[object]]):
        self.rows = rows

    def snapshot(self, object_id: int, snapshot_ts: datetime) -> dict[str, object]:
        key = (object_id, as_utc(snapshot_ts).isoformat())
        try:
            vector = self.rows[key]
        except KeyError as error:
            raise FeatureProviderError(f"golden row unavailable for {key}") from error
        if len(vector) != len(FEATURE_NAMES):
            raise FeatureProviderError("golden row does not contain exactly 87 features")
        return dict(zip(FEATURE_NAMES, vector))
