"""Independent checks for the complete object snapshot dataset."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "ml/data/features/risk_baseline_v1/snapshots.parquet"
MANIFEST = ROOT / "ml/artifacts/risk_baseline_v1/manifest.json"


def validate() -> dict:
    if not DATASET.exists() or not MANIFEST.exists():
        raise SystemExit("risk_baseline_v1 is not built")
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("status") != "built":
        raise SystemExit("risk_baseline_v1 manifest is not in built state")
    con = duckdb.connect()
    con.execute("PRAGMA threads=1")
    source = f"read_parquet('{DATASET}')"
    row = con.execute(f"""
        SELECT count(*) AS rows, count(DISTINCT object_id) AS objects,
          count(*) FILTER (WHERE proxy_anomaly_24h = 1) AS positives,
          count(*) FILTER (WHERE proxy_anomaly_24h IS NULL) AS unlabeled,
          count(*) FILTER (WHERE target_incomplete_reason = 'whole_day_no_records') AS whole_day_no_records_excluded,
          count(*) FILTER (WHERE target_incomplete_reason = 'cross_split_target_window') AS cross_split_target_window_excluded,
          count(*) FILTER (WHERE target_incomplete_reason = 'end_of_data') AS end_of_data_excluded,
          count(*) FILTER (WHERE NOT target_window_complete AND split <> 'unlabeled_excluded') AS incomplete_in_supervised_split,
          count(*) FILTER (WHERE target_window_complete AND proxy_anomaly_24h IS NULL) AS missing_valid_label,
          count(*) FILTER (WHERE NOT target_window_complete AND proxy_anomaly_24h IS NOT NULL) AS invalid_label_filled,
          count(*) FILTER (WHERE feature_uses_future_data IS DISTINCT FROM false) AS future_feature_rows,
          count(*) FILTER (WHERE channel_coverage_24h > 1 OR channel_coverage_24h < 0) AS impossible_coverage_rows,
          count(*) FILTER (WHERE current_reference_channel_count <= 0) AS bad_reference_rows,
          count(*) FILTER (WHERE snapshot_year = 2021 AND split <> 'transition_excluded') AS transition_split_errors,
          count(*) FILTER (WHERE snapshot_year <> 2021 AND target_window_complete AND
            ((snapshot_year IN (2019, 2020, 2022, 2023) AND split <> 'train') OR
             (snapshot_year = 2024 AND split <> 'validation') OR
             (snapshot_year = 2025 AND split <> 'test') OR
             (snapshot_year = 2026 AND split <> 'recent_2026'))) AS temporal_split_errors
        FROM {source}
    """).fetchone()
    names = [item[0] for item in con.description]
    checks = dict(zip(names, row))
    checks["duplicate_snapshots"] = con.execute(f"""
        SELECT count(*) FROM (
          SELECT object_id, snapshot_ts FROM {source}
          GROUP BY 1, 2 HAVING count(*) > 1
        )
    """).fetchone()[0]
    checks["physical_sort_violations"] = con.execute(f"""
        WITH numbered AS (
          SELECT row_number() OVER () AS source_row, snapshot_ts, object_id FROM {source}
        ), adjacent AS (
          SELECT snapshot_ts, object_id,
            lag(snapshot_ts) OVER (ORDER BY source_row) AS previous_ts,
            lag(object_id) OVER (ORDER BY source_row) AS previous_object
          FROM numbered
        )
        SELECT count(*) FROM adjacent
        WHERE snapshot_ts < previous_ts OR
          (snapshot_ts = previous_ts AND object_id < previous_object)
    """).fetchone()[0]
    checks["yearly"] = [dict(zip(("year", "rows", "positives", "unlabeled", "whole_day_no_records", "end_excluded"), item))
        for item in con.execute(f"""
          SELECT snapshot_year, count(*), count(*) FILTER (WHERE proxy_anomaly_24h=1),
            count(*) FILTER (WHERE proxy_anomaly_24h IS NULL),
            count(*) FILTER (WHERE target_incomplete_reason='whole_day_no_records'),
            count(*) FILTER (WHERE target_incomplete_reason='end_of_data')
          FROM {source} GROUP BY 1 ORDER BY 1
        """).fetchall()]
    checks["null_numeric_parse_rate_rows"] = con.execute(f"SELECT count(*) FROM {source} WHERE numeric_parse_rate_24h IS NULL").fetchone()[0]
    checks["whole_day_no_records_lookback_7d_rows"] = con.execute(f"SELECT count(*) FROM {source} WHERE whole_day_no_records_in_lookback_7d").fetchone()[0]
    checks["manifest_rows_match"] = checks["rows"] == manifest["rows"]
    checks["reason_counts_match"] = checks["unlabeled"] == (checks["whole_day_no_records_excluded"] + checks["cross_split_target_window_excluded"] + checks["end_of_data_excluded"])
    checks["cross_split_in_supervised_split"] = con.execute(f"SELECT count(*) FROM {source} WHERE target_incomplete_reason='cross_split_target_window' AND split <> 'unlabeled_excluded'").fetchone()[0]
    must_be_zero = (
        "incomplete_in_supervised_split", "missing_valid_label", "invalid_label_filled",
        "future_feature_rows", "impossible_coverage_rows", "bad_reference_rows",
        "transition_split_errors", "temporal_split_errors", "duplicate_snapshots",
        "physical_sort_violations", "cross_split_in_supervised_split",
    )
    checks["pass"] = (
        checks["rows"] > 0 and checks["objects"] == 78 and
        checks["manifest_rows_match"] and checks["reason_counts_match"] and
        all(checks[key] == 0 for key in must_be_zero)
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if not checks["pass"]:
        raise SystemExit(1)
    return checks


if __name__ == "__main__":
    validate()
