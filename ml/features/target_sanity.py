"""Target sanity diagnostics over materialized Phase 1 outputs only."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from statistics import median

import duckdb

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "ml/data/features/risk_baseline_v1/snapshots.parquet"
ARTIFACT = ROOT / "ml/artifacts/risk_baseline_v1/target_sanity.json"


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + j - 1) / 2 + 1
        for k in order[i:j]:
            ranks[k] = rank
        i = j
    return ranks


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    a, b = _rank(left), _rank(right)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    numerator = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    denominator = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return round(numerator / denominator, 6) if denominator else 0.0


def run(dataset: Path = DATASET, output: Path = ARTIFACT) -> dict:
    con = duckdb.connect()
    source = f"read_parquet('{dataset.resolve()}')"
    objects = con.execute(
        f"SELECT object_id,object_name,object_type,channels_count FROM read_json_auto('{(ROOT / 'backend/data/objects.json').resolve()}') WHERE channels_count>0 ORDER BY object_id"
    ).fetchall()
    onset_files = "[" + ",".join(f"'/tmp/tgoat-risk-channel-hour-{year}.parquet'" for year in range(2019, 2027)) + "]"
    onset_rows = con.execute(
        f"SELECT object_id,sum(onset_count) FROM read_parquet({onset_files}) GROUP BY object_id"
    ).fetchall()
    onset_by_object = {row[0]: int(row[1] or 0) for row in onset_rows}
    rows = con.execute(
        f"""SELECT object_id,count(*) FILTER (WHERE proxy_anomaly_24h=1),
          count(*) FILTER (WHERE proxy_anomaly_24h IS NOT NULL),
          avg(active_channels_24h),avg(event_count_24h),min(snapshot_ts),max(snapshot_ts)
          FROM {source} GROUP BY object_id ORDER BY object_id"""
    ).fetchall()
    stats = {}
    for object_id, positives, labeled, mean_active, mean_events, start, end in rows:
        days = (end.date() - start.date()).days + 1
        channels = next(item[3] for item in objects if item[0] == object_id)
        onsets = onset_by_object.get(object_id, 0)
        prevalence = positives / labeled if labeled else None
        stats[object_id] = {
            "object_id": object_id, "positive_snapshots": positives,
            "labeled_snapshots": labeled, "prevalence": round(prevalence, 6),
            "current_channel_count": channels,
            "total_conservative_onsets": onsets,
            "onsets_per_day": round(onsets / days, 6),
            "onsets_per_channel_per_day": round(onsets / days / channels, 8) if channels else None,
            "mean_active_channel_count": round(mean_active or 0, 6),
            "mean_event_volume_24h": round(mean_events or 0, 6),
            "snapshot_start": str(start), "snapshot_end": str(end),
        }
    obj_rows = []
    for object_id, name, object_type, _ in objects:
        row = stats[object_id].copy()
        row.update({"object_name": name, "object_type": object_type})
        obj_rows.append(row)
    values = [row["prevalence"] for row in obj_rows]
    correlations = {
        "prevalence_vs_current_channel_count": _spearman(values, [row["current_channel_count"] for row in obj_rows]),
        "prevalence_vs_mean_active_channel_count": _spearman(values, [row["mean_active_channel_count"] for row in obj_rows]),
        "prevalence_vs_mean_event_volume_24h": _spearman(values, [row["mean_event_volume_24h"] for row in obj_rows]),
        "prevalence_vs_onset_rate_per_channel_day": _spearman(values, [row["onsets_per_channel_per_day"] for row in obj_rows]),
    }
    sequence = con.execute(
        f"SELECT object_id,snapshot_ts,proxy_anomaly_24h FROM {source} WHERE proxy_anomaly_24h IS NOT NULL ORDER BY object_id,snapshot_ts"
    ).fetchall()
    runs: dict[int, list[int]] = {row[0]: [] for row in objects}
    current_object = None
    current_run = 0
    previous_ts = None
    for object_id, timestamp, target in sequence:
        if object_id != current_object:
            current_object, current_run, previous_ts = object_id, 0, None
        if target == 1 and previous_ts is not None and timestamp - previous_ts == __import__('datetime').timedelta(hours=6):
            current_run += 1
        elif target == 1:
            if current_run:
                runs[object_id].append(current_run)
            current_run = 1
        else:
            if current_run:
                runs[object_id].append(current_run)
            current_run = 0
        previous_ts = timestamp
    if current_object is not None and current_run:
        runs[current_object].append(current_run)
    run_summary = {}
    for object_id, lengths in runs.items():
        run_summary[object_id] = {"median": median(lengths) if lengths else 0, "p95": sorted(lengths)[max(0, int(len(lengths) * .95) - 1)] if lengths else 0, "max": max(lengths) if lengths else 0, "run_count": len(lengths)}
    high = [row for row in obj_rows if row["prevalence"] > .5]
    result = {
        "scope": "materialized snapshot parquet plus existing annual channel-hour parquet; no raw scan",
        "objects": obj_rows,
        "high_prevalence_objects": high,
        "prevalence_summary": {"min": min(values), "median": median(values), "max": max(values), "objects_zero_positive": sum(row["positive_snapshots"] == 0 for row in obj_rows), "objects_above_50_percent": len(high)},
        "spearman": correlations,
        "positive_run_lengths": run_summary,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ARTIFACT)
    args = parser.parse_args()
    print(json.dumps(run(output=args.output), ensure_ascii=False, indent=2))
