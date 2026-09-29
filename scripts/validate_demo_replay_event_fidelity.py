"""Check replay event extraction against the accepted materialized hourly state."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.config import as_utc
from backend.app.services.ml_features import HourlyState, InMemoryStateStore, hydrate_hourly_state
from backend.app.services.reference_data import get_channels_by_object, get_reference_meta
from backend.app.services.replay_runtime import (
    DEFAULT_MANIFEST, load_replay_event_source, load_replay_manifest,
)


def normalize(value):
    return as_utc(value).isoformat() if hasattr(value, "tzinfo") else value


def main() -> None:
    manifest, hourly = load_replay_manifest(DEFAULT_MANIFEST)
    events = load_replay_event_source(manifest, DEFAULT_MANIFEST)
    reference = get_reference_meta()
    by_object = defaultdict(list)
    for event in events:
        by_object[event.object_id].append(event)
    first_hour = as_utc(datetime.fromisoformat(manifest["replay_start"])).replace(
        minute=0, second=0, microsecond=0) + timedelta(hours=1)
    replay_end = as_utc(datetime.fromisoformat(manifest["replay_end"]))
    store = InMemoryStateStore(hourly, {}, {}, reference_snapshot_id=f"reference-v{reference['schema_version']}",
                               reference_hash=reference["source_sha256"])
    expected: dict[tuple[int, int, str], HourlyState] = {}
    for object_id, observations in by_object.items():
        prior = store.channel_states_before(object_id, first_hour)
        for row in hydrate_hourly_state(observations, initial_channel_state=prior):
            timestamp = as_utc(row.hour_ts)
            if first_hour <= timestamp <= replay_end:
                expected[(row.object_id, row.channel_id, timestamp.isoformat())] = row
    materialized = {
        (row.object_id, row.channel_id, as_utc(row.hour_ts).isoformat()): row
        for row in hourly if first_hour <= as_utc(row.hour_ts) <= replay_end
    }
    mismatches = []
    for key in sorted(set(expected) | set(materialized)):
        left, right = expected.get(key), materialized.get(key)
        if left is None or right is None:
            mismatches.append({"key": key, "reason": "missing event-derived row" if left else "extra materialized row"})
            continue
        for field in fields(HourlyState):
            if normalize(getattr(left, field.name)) != normalize(getattr(right, field.name)):
                mismatches.append({"key": key, "field": field.name,
                                   "event_value": normalize(getattr(left, field.name)),
                                   "hourly_value": normalize(getattr(right, field.name))})
                break
    counts_by_object = defaultdict(int)
    for event in events:
        counts_by_object[event.object_id] += 1
    summary = {
        "status": "PASS" if not mismatches else "FAIL",
        "objects_in_event_slice": len({event.object_id for event in events}),
        "event_scope_objects": len(manifest["event_source_object_ids"]),
        "event_rows": len(events),
        "compared_hourly_rows": len(materialized),
        "event_derived_rows": len(expected),
        "mismatched_rows_or_fields": len(mismatches),
        "first_mismatches": mismatches[:20],
        "source_event_counts_by_object_match_manifest": {
            str(object_id): counts_by_object[object_id] for object_id in manifest["object_ids"]
        } == manifest["event_source_row_count_by_object"],
    }
    report_path = DEFAULT_MANIFEST.parent / "event_fidelity_report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["report_file"] = str(report_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if mismatches or not summary["source_event_counts_by_object_match_manifest"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
