"""Bounded, provenance-checked bootstrap for the checked-in demo event slice."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.config import as_utc
from backend.app.services.ml_features import (
    RawObservation,
    SQLiteFeatureStateStore,
    SYSTEM_NAMES,
    hydrate_hourly_state,
    is_canonical_snapshot,
)
from backend.app.services.reference_data import (
    get_channels_by_id,
    get_channels_by_object,
    get_reference_meta,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "ml/data/demo/runtime_window.json"


class DemoFeatureBootstrapError(ValueError):
    """The bounded demo source cannot safely satisfy the frozen state contract."""


def _datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DemoFeatureBootstrapError(f"invalid timestamp in demo manifest: {value}") from error


def _alarm(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "t", "1"}:
        return True
    if normalized in {"false", "f", "0"}:
        return False
    raise DemoFeatureBootstrapError(f"invalid alarm value in demo source: {value!r}")


def bootstrap_demo_feature_state(output_path: str | Path, *,
                                 manifest_path: str | Path = DEFAULT_MANIFEST) -> dict[str, object]:
    """Hydrate one bounded, checked-in event fixture into SQLite feature state.

    The source window and watermark are asserted by a sidecar manifest whose
    digest pins the exact extracted observations. Nothing here reads annual
    source data or fabricates event rows.
    """
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing state database: {output}")

    manifest_file = Path(manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DemoFeatureBootstrapError("cannot read demo source manifest") from error

    source = (manifest_file.parent / manifest["source_file"]).resolve()
    expected_digest = str(manifest["source_sha256"])
    actual_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_digest != expected_digest:
        raise DemoFeatureBootstrapError("demo source digest does not match manifest")

    object_id = int(manifest["object_id"])
    snapshot = as_utc(_datetime(manifest["snapshot_ts"]))
    available_from = as_utc(_datetime(manifest["available_from"]))
    available_to = as_utc(_datetime(manifest["available_to"]))
    if not is_canonical_snapshot(snapshot):
        raise DemoFeatureBootstrapError("demo snapshot is not a canonical 6-hour boundary")
    if available_from > snapshot - timedelta(hours=168):
        raise DemoFeatureBootstrapError("demo source does not cover the complete 168-hour lookback")
    if available_to < snapshot:
        raise DemoFeatureBootstrapError("demo source watermark is earlier than its snapshot")

    reference = get_reference_meta()
    object_channels = get_channels_by_object().get(object_id, [])
    if not object_channels:
        raise DemoFeatureBootstrapError(f"object {object_id} has no channels in current reference")
    channels_by_id = get_channels_by_id()
    ref_counts = {object_id: len(object_channels)}
    system_ref_counts: dict[tuple[int, str], int] = {}
    for channel in object_channels:
        system = SYSTEM_NAMES.get(channel["system_type"], channel["system_type"])
        key = (object_id, system)
        system_ref_counts[key] = system_ref_counts.get(key, 0) + 1

    observations: list[RawObservation] = []
    with source.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        required = {"event_id", "channel_id", "timestamp", "system_type", "is_alarm",
                    "sensor_value", "invalid_alarm"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise DemoFeatureBootstrapError("demo source is missing required canonical event columns")
        for line_number, row in enumerate(reader, start=2):
            try:
                channel_id = int(row["channel_id"])
                timestamp = _datetime(row["timestamp"])
                timestamp_utc = as_utc(timestamp)
                channel = channels_by_id[channel_id]
                if channel["object_id"] != object_id:
                    raise DemoFeatureBootstrapError("demo event channel belongs to another object")
                if timestamp_utc < available_from or timestamp_utc > snapshot:
                    raise DemoFeatureBootstrapError("demo event is outside its declared source window")
                raw_value = row.get("sensor_value") or ""
                normalized_value = raw_value.strip().lower()
                try:
                    numeric_code = int(normalized_value)
                except ValueError:
                    numeric_code = None
                observations.append(RawObservation(
                    object_id=object_id,
                    channel_id=channel_id,
                    timestamp=timestamp,
                    system_type=channel["system_type"],
                    is_alarm=_alarm(row["is_alarm"]),
                    sensor_value=raw_value,
                    invalid_alarm=_alarm(row["invalid_alarm"]),
                    malfunction_text=normalized_value == "неисправен",
                    special_code=numeric_code,
                ))
            except (KeyError, TypeError, ValueError) as error:
                if isinstance(error, DemoFeatureBootstrapError):
                    raise
                raise DemoFeatureBootstrapError(
                    f"invalid demo event at CSV line {line_number}"
                ) from error

    if not observations:
        raise DemoFeatureBootstrapError("demo source contains no observations")
    if len(observations) != int(manifest["rows"]):
        raise DemoFeatureBootstrapError("demo source row count does not match manifest")

    hourly = hydrate_hourly_state(observations)
    output.parent.mkdir(parents=True, exist_ok=True)
    SQLiteFeatureStateStore.create(
        output,
        rows=hourly,
        reference_counts=ref_counts,
        system_reference_counts=system_ref_counts,
        available_from=available_from,
        available_to=available_to,
        reference_snapshot_id=f"reference-v{reference['schema_version']}",
        reference_hash=reference["source_sha256"],
        raw_observations=observations,
        event_coverage_from=available_from,
        event_coverage_to=available_to,
    )
    return {
        "output": str(output),
        "object_id": object_id,
        "source_rows": len(observations),
        "hourly_state_rows": len(hourly),
        "snapshot_ts": snapshot.isoformat(),
        "available_from": available_from.isoformat(),
        "available_to": available_to.isoformat(),
        "reference_snapshot_id": f"reference-v{reference['schema_version']}",
        "reference_hash": reference["source_sha256"],
        "source_sha256": actual_digest,
        "event_level_source_rows": len(observations),
        "event_level_source_coverage_from": available_from.isoformat(),
        "event_level_source_coverage_to": available_to.isoformat(),
    }
