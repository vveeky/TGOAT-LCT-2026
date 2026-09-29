"""Export only the bounded replay interval as a digest-pinned event stream.

This reads the already processed canonical parquet. It does not rebuild or
modify Phase 1 data or the model artifact.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import duckdb

from backend.app.services.reference_data import get_channels_data


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ml/data/demo/replay/replay_manifest.json"
SOURCE = ROOT / "ml/data/processed/events_2026.parquet"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    output = MANIFEST.parent / "replay-events-2026-05-05--2026-05-07.jsonl.gz"
    if output.exists() and not args.force:
        raise SystemExit("event slice exists; use --force to replace this generated file")
    upstream_digest = digest(SOURCE)
    if upstream_digest != manifest["upstream_source_sha256"]:
        raise SystemExit("processed parquet digest differs from the accepted replay manifest")

    object_ids = set(map(int, manifest["object_ids"]))
    channel_ref = {int(item["channel_id"]): item for item in get_channels_data()
                   if int(item["object_id"]) in object_ids}
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TEMP TABLE demo_channel_ref(channel_id BIGINT, object_id BIGINT, system_type VARCHAR)")
    connection.executemany("INSERT INTO demo_channel_ref VALUES (?, ?, ?)", [
        (channel_id, item["object_id"], item["system_type"])
        for channel_id, item in sorted(channel_ref.items())
    ])
    start = manifest["replay_start"][:19].replace("T", " ")
    end = manifest["replay_end"][:19].replace("T", " ")
    cursor = connection.execute("""
        SELECT c.object_id, e.channel_id, e.timestamp, e.is_alarm,
               e.invalid_alarm, e.sensor_value, c.system_type
        FROM read_parquet(?) e
        JOIN demo_channel_ref c ON c.channel_id=e.channel_id
        WHERE e.timestamp > ? AND e.timestamp <= ?
        ORDER BY e.timestamp, e.event_id
    """, [str(SOURCE), start, end])

    count = 0
    counts_by_object = {object_id: 0 for object_id in object_ids}
    with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
        import io
        text = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
        while batch := cursor.fetchmany(10_000):
            for object_id, channel_id, timestamp, alarm, invalid, sensor_value, system_type in batch:
                value = (sensor_value or "").strip().lower()
                try:
                    special_code = int(value)
                except ValueError:
                    special_code = None
                payload = {
                    "object_id": int(object_id), "channel_id": int(channel_id),
                    "timestamp": timestamp.isoformat(), "system_type": system_type,
                    "is_alarm": bool(alarm), "invalid_alarm": bool(invalid),
                    "sensor_value": sensor_value or "", "malfunction_text": value == "неисправен",
                    "special_code": special_code,
                }
                text.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                count += 1
                counts_by_object[int(object_id)] += 1
        text.close()
    connection.close()

    manifest.update({
        "event_source_file": output.name,
        "event_source_sha256": digest(output),
        "event_source_kind": "bounded_registered_event_slice",
        "event_source_row_count": count,
        "event_source_object_ids": sorted(object_ids),
        "event_source_row_count_by_object": {str(key): value for key, value in sorted(counts_by_object.items())},
        "event_source_window_start_exclusive": manifest["replay_start"],
        "event_source_window_end_inclusive": manifest["replay_end"],
        "event_source_upstream_sha256": upstream_digest,
        "event_source_semantics": "registered timestamps as recorded; no physical-time/backfill reconstruction",
    })
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(output), "rows": count, "bytes": output.stat().st_size,
                      "sha256": manifest["event_source_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
