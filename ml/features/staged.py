from __future__ import annotations

import json
import os
import argparse
import time
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import duckdb

from .build_risk_dataset import MANIFEST, OUTPUT_DIR, PROCESSED, SYSTEM_TYPES, _q
from .common import HORIZON_HOURS, KNOWN_GAP_DATES, SNAPSHOT_HOURS
from .diagnostics import build_diagnostics
from .base_snapshots import base_snapshot_sql
from .augment import diagnostic_features_sql, alarm_recency_sql, augment_sql


ORGANIZER_STATE_REFERENCE_CONTRACT = {
    "confirmed_join_key": "тип_датчика",
    "state_lookup_key": ["тип_датчика", "название_состояния"],
    "must_not_join_by": "ид_канала_данных",
    "confirmed_example": {
        "ид_канала_данных": 120578,
        "тип_датчика": "КД АВ",
        "states": [
            "Дыма нет",
            "Обнаружен дым",
            "Движения нет",
            "Обнаружено движение",
            "Движение вверх",
            "Движение вниз",
            "Движение влево",
            "Движение вправо",
        ],
    },
    "phase1_effect": (
        "Documentation/reference semantics only; source тревожное remains canonical "
        "and frozen Phase 1 features, targets, and 87-feature model contract are unchanged."
    ),
    "ambiguous_mapping_policy": (
        "Fail closed when the sensor-type/state-name key is missing or maps to "
        "conflicting alarm flags; do not infer from channel ID or physical meaning."
    ),
}


def _hourly_sql(year: int, current_path: Path | None = None, previous_path: Path | None = None) -> str:
    files = _q(str(current_path or PROCESSED / f"events_{year}.parquet"))
    previous = previous_path or Path(f"/tmp/tgoat-risk-channel-hour-{year-1}.parquet")
    prior_sql = (
        f"SELECT channel_id, arg_max(last_alarm_state, last_event_ts) AS prior_alarm_state, "
        f"max(last_event_ts) AS prior_timestamp "
        f"FROM read_parquet({_q(str(previous))}) GROUP BY channel_id"
        if year > 2019 or previous_path is not None else
        "SELECT NULL::BIGINT AS channel_id, NULL::INTEGER AS prior_alarm_state, "
        "NULL::TIMESTAMP AS prior_timestamp WHERE false"
    )
    return f"""
    WITH source AS (
      SELECT timestamp, channel_id, object_id, object_name, system_type, is_alarm,
             invalid_alarm, sensor_value, source_year
      FROM read_parquet({files}, union_by_name=true)
      WHERE object_id IS NOT NULL AND channel_id IS NOT NULL
    ), ticks AS (
      SELECT channel_id, timestamp, object_id, max(object_name) object_name,
             max(system_type) system_type,
             max(CASE WHEN is_alarm THEN 1 ELSE 0 END) alarm_state,
             count(*) event_count,
             sum(CASE WHEN is_alarm THEN 1 ELSE 0 END) alarm_count,
             sum(CASE WHEN invalid_alarm THEN 1 ELSE 0 END) invalid_alarm_count,
             count(CASE WHEN try_cast(replace(sensor_value, ',', '.') AS DOUBLE) IS NOT NULL THEN 1 END) numeric_value_count,
             count(*) raw_value_count,
             max(source_year) source_year
      FROM source GROUP BY ALL
    ), prior AS (
      {prior_sql}
    ), ordered AS (
      SELECT t.*, p.prior_alarm_state, p.prior_timestamp,
             row_number() OVER (PARTITION BY t.channel_id ORDER BY t.timestamp) AS tick_number,
             lag(t.alarm_state) OVER (PARTITION BY t.channel_id ORDER BY t.timestamp) AS within_year_alarm_state,
             lag(t.timestamp) OVER (PARTITION BY t.channel_id ORDER BY t.timestamp) AS within_year_timestamp
      FROM ticks t LEFT JOIN prior p USING (channel_id)
    ), marked AS (
      SELECT *,
             CASE WHEN tick_number=1 THEN prior_alarm_state ELSE within_year_alarm_state END AS previous_alarm_state,
             CASE WHEN tick_number=1 THEN prior_timestamp ELSE within_year_timestamp END AS previous_timestamp
      FROM ordered
    ), hourly AS (
      SELECT date_trunc('hour', timestamp) hour_ts, max(timestamp) last_event_ts,
             object_id, channel_id, max(object_name) object_name, max(system_type) system_type,
             arg_max(alarm_state, timestamp) last_alarm_state,
             sum(event_count) event_count, sum(alarm_count) alarm_count,
             sum(CASE WHEN alarm_state=1 AND previous_alarm_state=0
                            AND previous_timestamp IS NOT NULL
                            AND timestamp <= previous_timestamp + INTERVAL '24 hours' THEN 1 ELSE 0 END) onset_count,
             sum(invalid_alarm_count) invalid_alarm_count, sum(numeric_value_count) numeric_value_count,
             sum(raw_value_count) raw_value_count, max(source_year) source_year
      FROM marked GROUP BY ALL
    )
    SELECT * FROM hourly
    """

def build_staged(*, reuse_hourly: bool = False) -> dict:
    started_all = time.monotonic()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / "snapshots.parquet"
    spill_dir = Path("/tmp/tgoat-risk-spill")
    spill_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(os.environ.get("TGOAT_DUCKDB_TEMP", "/tmp/tgoat-risk-baseline.duckdb"))
    con.execute("PRAGMA threads=2")
    con.execute("PRAGMA memory_limit='6GB'")
    con.execute("PRAGMA preserve_insertion_order=false")
    con.execute(f"SET temp_directory={_q(str(spill_dir))}")
    con.execute("SET enable_progress_bar=false")
    hourly_paths = []
    annual_stages = []
    for year in range(2019, 2027):
        temp = Path(f"/tmp/tgoat-risk-channel-hour-{year}.parquet")
        hourly_paths.append(temp)
        if reuse_hourly:
            if not temp.exists() or temp.stat().st_size == 0:
                raise FileNotFoundError(f"required annual intermediate missing: {temp}")
            columns = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet({_q(str(temp))})").fetchall()}
            if not {"last_alarm_state", "last_event_ts", "onset_count"}.issubset(columns):
                raise ValueError(f"annual intermediate lacks cross-year state: {temp}")
            annual_stages.append({"year": year, "bytes": temp.stat().st_size, "reused": True})
            print(f"reusing channel-hour {year}: {temp.stat().st_size} bytes", flush=True)
        else:
            started = time.monotonic()
            print(f"building channel-hour {year}", flush=True)
            con.execute(f"COPY ({_hourly_sql(year)}) TO {_q(str(temp))} (FORMAT PARQUET, COMPRESSION ZSTD)")
            seconds = round(time.monotonic()-started, 1)
            annual_stages.append({"year": year, "bytes": temp.stat().st_size, "seconds": seconds, "reused": False})
            print(f"channel-hour {year}: {temp.stat().st_size} bytes in {seconds:.1f}s", flush=True)
    diagnostic_path = Path('/tmp/tgoat-risk-diagnostics.parquet')
    started = time.monotonic()
    print('building sparse diagnostic aggregates', flush=True)
    diagnostic_stats = build_diagnostics(con, diagnostic_path)
    diagnostic_stats['seconds'] = round(time.monotonic()-started, 1)
    print(f"diagnostics: {diagnostic_stats}", flush=True)
    print("building materialized base snapshots", flush=True)
    stage_started = time.monotonic()
    base_path = Path('/tmp/tgoat-risk-base-snapshots.parquet')
    con.execute(f"COPY ({base_snapshot_sql(hourly_paths, ROOT / 'backend/data/objects.json')}) TO {_q(str(base_path))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"base snapshots: {base_path.stat().st_size} bytes in {time.monotonic()-stage_started:.1f}s", flush=True)
    print("building materialized diagnostic features", flush=True)
    diagnostic_features_path = Path('/tmp/tgoat-risk-snapshot-diagnostic.parquet')
    con.execute(f"COPY ({diagnostic_features_sql(base_path, diagnostic_path)}) TO {_q(str(diagnostic_features_path))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"diagnostic features: {diagnostic_features_path.stat().st_size} bytes", flush=True)
    print("building materialized alarm recency features", flush=True)
    recency_path = Path('/tmp/tgoat-risk-snapshot-recency.parquet')
    con.execute(f"COPY ({alarm_recency_sql(base_path, hourly_paths)}) TO {_q(str(recency_path))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    print(f"alarm recency: {recency_path.stat().st_size} bytes", flush=True)
    print("building final narrow snapshot join", flush=True)
    started = time.monotonic()
    next_output = Path('/tmp/tgoat-risk-final-snapshots.parquet')
    con.execute(f"COPY ({augment_sql(base_path, diagnostic_features_path, recency_path, ROOT / 'backend/data/channels.json')}) TO {_q(str(next_output))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    snapshot_seconds = round(time.monotonic()-started, 1)
    os.replace(next_output, output)
    print(f"object snapshots: {output.stat().st_size} bytes in {snapshot_seconds:.1f}s", flush=True)
    stats = con.execute(f"SELECT count(*), count(distinct object_id), min(snapshot_ts), max(snapshot_ts), count(*) FILTER (WHERE target_window_complete) FROM read_parquet({_q(str(output))})").fetchone()
    years = con.execute(f"SELECT snapshot_year, split, count(*), count(*) FILTER (WHERE proxy_anomaly_24h=1), count(*) FILTER (WHERE proxy_anomaly_24h IS NULL) FROM read_parquet({_q(str(output))}) GROUP BY 1,2 ORDER BY 1,2").fetchall()
    reasons = con.execute(f"SELECT target_incomplete_reason, count(*) FROM read_parquet({_q(str(output))}) WHERE target_incomplete_reason IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()
    columns = [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet({_q(str(output))})").fetchall()]
    non_features = {"object_id", "object_name", "snapshot_ts", "snapshot_year", "proxy_anomaly_24h", "target_incomplete_reason", "target_window_complete", "split", "transition_period", "feature_uses_future_data"}
    features = [column for column in columns if column not in non_features]
    source_manifests = [json.loads((PROCESSED/f'events_{year}.parquet.meta.json').read_text()) for year in range(2019,2027)]
    code_hash = hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in ('staged.py','diagnostics.py','common.py','build_risk_dataset.py'))).hexdigest()
    manifest = {
        "dataset":"risk_baseline_v1", "status":"built", "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "unit":"object_x_6h_snapshot", "target":"proxy_anomaly_24h",
        "target_definition":"conservative episode onset in (t0, t0+24h] after confirmed non-alarm on same channel",
        "horizon_hours":24, "snapshot_hours":6, "lookbacks_hours":[1,6,24,168],
        "rows":stats[0], "objects":stats[1], "min_snapshot_ts":str(stats[2]), "max_snapshot_ts":str(stats[3]),
        "labeled_rows":stats[4], "output_bytes":output.stat().st_size,
        "rows_by_year_split":[{"year":a,"split":b,"rows":c,"positives":d,"unlabeled":e,"positive_rate":round(d/(c-e),6) if c>e else None} for a,b,c,d,e in years],
        "target_incomplete_reasons":dict(reasons),
        "feature_count":len(features), "feature_columns":features,
        "source_rows":sum(item['rows'] for item in source_manifests),
        "source_partitions":[{"year":year,"path":f"ml/data/processed/events_{year}.parquet","manifest":item} for year,item in zip(range(2019,2027),source_manifests)],
        "current_reference_source":"backend/data/objects.json and backend/data/channels.json",
        "current_reference_channel_count_semantics":"current reference denominator, not historical at snapshot time",
        "split_policy":{"train":[2019,2020,2022,2023],"validation":[2024],"test":[2025],"recent_2026":[2026],"transition_excluded":[2021]},
        "known_gap_dates":list(KNOWN_GAP_DATES),
        "whole_day_no_records_policy":"Conservative modeling policy for ambiguous dates with no records; not customer-confirmed technical data loss.",
        "target_end_of_data_policy":"A 24h horizon beyond the available registered-timestamp dataset is unknown and excluded from supervised splits.",
        "timestamp_semantics":"source registration timestamp; network delay/backfill and physical event time are not modeled",
        "organizer_state_reference_contract":ORGANIZER_STATE_REFERENCE_CONTRACT,
        "snapshot_grid":"78 objects × 4 snapshots/day across the available calendar period",
        "event_id_deduplication":False,
        "unmatched_historical_channels":"excluded from object model; no synthetic mapping",
        "feature_uses_future_data":False,"ml_training":False,"code_sha256":code_hash,
        "annual_stages":annual_stages,"diagnostic_stage":{**diagnostic_stats,"scan_scope":"exhaustive over all eight canonical processed Parquet partitions","matched_malfunction_text_records":1487653,"special_code_records":8156,"sparse_channel_hour_rows":211747},
        "feature_stage_materialization":["base_snapshots","diagnostics","alarm_recency","final_narrow_join"],
        "phase2_evaluation":{"technical":["precision","recall","pr_auc","threshold_analysis","temporal_validation_test"],"user_facing":"merge repeated positive object forecasts into forecast episodes/cards"},
        "snapshot_stage_seconds":snapshot_seconds,"total_seconds":round(time.monotonic()-started_all,1),
    }
    MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    con.close()
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prototype-year", type=int, choices=range(2019, 2027))
    parser.add_argument("--reuse-hourly", action="store_true")
    args = parser.parse_args()
    if args.prototype_year is None:
        print(json.dumps(build_staged(reuse_hourly=args.reuse_hourly), ensure_ascii=False, indent=2))
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        con.execute("PRAGMA threads=2")
        con.execute("PRAGMA memory_limit='6GB'")
        con.execute("PRAGMA preserve_insertion_order=false")
        spill_dir = Path('/tmp/tgoat-risk-spill')
        spill_dir.mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory={_q(str(spill_dir))}")
        output = Path(f"/tmp/tgoat-risk-channel-hour-{args.prototype_year}.parquet")
        started = time.monotonic()
        con.execute(f"COPY ({_hourly_sql(args.prototype_year)}) TO {_q(str(output))} (FORMAT PARQUET, COMPRESSION ZSTD)")
        stats = con.execute(f"SELECT count(*), sum(event_count), sum(alarm_count), sum(onset_count), count(distinct object_id), count(distinct channel_id) FROM read_parquet({_q(str(output))})").fetchone()
        print(json.dumps({"year": args.prototype_year, "seconds": round(time.monotonic()-started, 1), "bytes": output.stat().st_size, "rows": stats[0], "events": stats[1], "alarms": stats[2], "onsets": stats[3], "objects": stats[4], "channels": stats[5]}, indent=2))
