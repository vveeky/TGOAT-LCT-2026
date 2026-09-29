"""Build a bounded, digest-pinned canonical hourly-state replay package."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO

import duckdb

from backend.app.services.ml_features import RawObservation, SYSTEM_NAMES, hydrate_hourly_state
from backend.app.services.reference_data import get_channels_data, get_objects_data, get_reference_meta
from backend.app.config import as_utc


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ml/data/processed/events_2026.parquet"
OUTPUT_DIR = ROOT / "ml/data/demo/replay"
REPLAY_START = datetime.fromisoformat("2026-05-05T11:59:59+03:00")
REPLAY_END = datetime.fromisoformat("2026-05-07T11:59:59+03:00")
WARMUP_START = REPLAY_START - timedelta(hours=168)
SOURCE_START = REPLAY_START - timedelta(hours=192)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="replace only the two generated replay outputs")
    return parser.parse_args()


def event_to_observation(row, channel) -> RawObservation:
    raw_value = row[5] or ""
    normalized_value = raw_value.strip().lower()
    try:
        special_code = int(normalized_value)
    except ValueError:
        special_code = None
    return RawObservation(
        object_id=channel["object_id"], channel_id=channel["channel_id"],
        timestamp=row[2], system_type=channel["system_type"],
        is_alarm=bool(row[3]), sensor_value=raw_value,
        invalid_alarm=bool(row[4]), malfunction_text=normalized_value == "неисправен",
        special_code=special_code,
    )


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state_path = OUTPUT_DIR / "hourly-state-2026-04-27--2026-05-07.jsonl.gz"
    manifest_path = OUTPUT_DIR / "replay_manifest.json"
    if not args.force and (state_path.exists() or manifest_path.exists()):
        raise SystemExit("replay output exists; use --force only to replace this generated replay package")

    objects = get_objects_data()
    object_ids = sorted(int(item["object_id"]) for item in objects if item["has_channels"])
    channel_by_id = {int(item["channel_id"]): item for item in get_channels_data()
                     if int(item["object_id"]) in set(object_ids)}
    reference = get_reference_meta()
    upstream_digest = sha256(SOURCE)

    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TEMP TABLE demo_channel_ref(channel_id BIGINT, object_id BIGINT, system_type VARCHAR)")
    connection.executemany("INSERT INTO demo_channel_ref VALUES (?, ?, ?)", [
        (channel_id, channel["object_id"], channel["system_type"])
        for channel_id, channel in sorted(channel_by_id.items())
    ])
    rows = connection.execute("""
        SELECT c.object_id, e.event_id, e.channel_id, e.timestamp,
               e.is_alarm, e.invalid_alarm, e.sensor_value
        FROM read_parquet(?) AS e
        INNER JOIN demo_channel_ref AS c ON c.channel_id = e.channel_id
        WHERE e.timestamp >= ? AND e.timestamp <= ?
        ORDER BY c.object_id, e.timestamp, e.event_id
    """, [str(SOURCE), SOURCE_START.replace(tzinfo=None), REPLAY_END.replace(tzinfo=None)])

    hourly_count = 0
    source_count = 0
    covered_ids: list[int] = []
    object_event_counts: dict[int, int] = {}
    state_output = state_path.open("wb")
    gzip_stream = gzip.GzipFile(fileobj=state_output, mode="wb", filename="", mtime=0)
    import io
    text_output: TextIO = io.TextIOWrapper(gzip_stream, encoding="utf-8", newline="")
    current_object = None
    observations: list[RawObservation] = []

    def write_object(object_id: int | None, batch: list[RawObservation]) -> None:
        nonlocal hourly_count
        if object_id is None:
            return
        object_event_counts[object_id] = len(batch)
        # A covered object has at least one real registered event in the complete 168h
        # feature window; otherwise it remains fail-closed in the replay provider.
        has_lookback_events = any(as_utc(item.timestamp) > as_utc(WARMUP_START)
                                  and as_utc(item.timestamp) <= as_utc(REPLAY_START)
                                  for item in batch)
        if has_lookback_events:
            covered_ids.append(object_id)
        hydrated = hydrate_hourly_state(batch)
        for item in hydrated:
            payload = item.__dict__.copy()
            for field in ("hour_ts", "last_event_ts", "last_alarm_ts", "last_onset_ts",
                          "last_malfunction_text_ts"):
                if payload[field] is not None:
                    payload[field] = payload[field].isoformat()
            text_output.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        hourly_count += len(hydrated)

    while True:
        batch = rows.fetchmany(20_000)
        if not batch:
            break
        for row in batch:
            object_id = int(row[0])
            channel = channel_by_id.get(int(row[2]))
            if channel is None or channel["object_id"] != object_id or row[3] is None:
                continue
            if current_object is not None and object_id != current_object:
                write_object(current_object, observations)
                observations = []
            current_object = object_id
            observations.append(event_to_observation(row[1:], channel))
            source_count += 1
    write_object(current_object, observations)
    text_output.close()
    state_output.close()
    connection.close()

    model_manifest = json.loads((ROOT / "ml/artifacts/risk_baseline_v1/phase2_manifest.json").read_text())
    reference_counts = {str(object_id): len([c for c in channel_by_id.values() if c["object_id"] == object_id])
                        for object_id in object_ids}
    manifest = {
        "replay_id": "moscow-2026-05-05-demo-v1",
        "source_file": state_path.name,
        "source_sha256": sha256(state_path),
        "source_kind": "canonical_hourly_state_from_registered_events",
        "upstream_source": "ml/data/processed/events_2026.parquet",
        "upstream_source_sha256": upstream_digest,
        "source_timezone": "Europe/Moscow",
        "source_window_start": SOURCE_START.isoformat(),
        "warmup_start": WARMUP_START.isoformat(),
        "replay_start": REPLAY_START.isoformat(),
        "replay_end": REPLAY_END.isoformat(),
        "warmup_hours": 168,
        "extra_onset_carry_hours": 24,
        "object_ids": object_ids,
        "covered_object_ids": sorted(covered_ids),
        "uncovered_object_ids": sorted(set(object_ids) - set(covered_ids)),
        "reference_snapshot_id": f"reference-v{reference['schema_version']}",
        "reference_sha256": reference["source_sha256"],
        "reference_channel_counts": reference_counts,
        "artifact_sha256": model_manifest["sha256"],
        "feature_count": model_manifest["feature_count"],
        "threshold": model_manifest["threshold"],
        "snapshot_cadence_hours": model_manifest["snapshot_cadence_hours"],
        "forecast_horizon_hours": model_manifest["forecast_horizon_hours"],
        "source_row_count": source_count,
        "hourly_state_row_count": hourly_count,
        "source_event_count_by_object": {str(key): value for key, value in sorted(object_event_counts.items())},
        "default_speed": 1800,
        "selection_evidence": {
            "prepared_snapshot_rows_cover_all_objects": len(object_ids),
            "selected_replay_cycles": 9,
            "frozen_model_positive_count_by_cycle": [15, 16, 17, 14, 23, 20, 16, 12, 20],
            "mean_absolute_probability_change_per_object_per_6h": 0.057081034671111815,
            "probability_changes_at_least_0_10_per_6h": 117,
            "positive_flag_transitions": 53,
            "candidate_window_screen": "bounded prepared snapshot parquet only; no annual feature rebuild",
        },
        "coverage_policy": "objects with no registered source event in the 168h model lookback are fail-closed; no scores are fabricated",
        "data_disclaimer": "Historical registered-event replay; it is not live state or a reconstruction of physical event time.",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "state_file": str(state_path),
                      "source_rows": source_count, "hourly_state_rows": hourly_count,
                      "covered_objects": len(covered_ids), "operational_objects": len(object_ids),
                      "state_bytes": state_path.stat().st_size, "source_sha256": manifest["source_sha256"],
                      "upstream_sha256": upstream_digest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
