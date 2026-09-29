"""Bounded object snapshot query over annual channel-hour aggregates."""

from __future__ import annotations

from pathlib import Path

from .build_risk_dataset import SYSTEM_TYPES, _q
from .common import KNOWN_GAP_DATES


def base_snapshot_sql(hourly: list[Path], objects_reference: Path) -> str:
    files = "[" + ", ".join(_q(str(path)) for path in hourly) + "]"
    systems = ",\n        ".join(
        expression
        for slug, system in SYSTEM_TYPES
        for expression in (
            f"sum(CASE WHEN h.system_type={_q(system)} AND h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.event_count ELSE 0 END) system_{slug}_event_count_24h",
            f"sum(CASE WHEN h.system_type={_q(system)} AND h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.alarm_count ELSE 0 END) system_{slug}_alarm_count_24h",
            f"sum(CASE WHEN h.system_type={_q(system)} AND h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.onset_count ELSE 0 END) system_{slug}_onset_count_24h",
            f"count(DISTINCT CASE WHEN h.system_type={_q(system)} AND h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.channel_id END) system_{slug}_active_channels_24h",
        )
    )

    def gap_overlap(start: str, end: str) -> str:
        return "(" + " OR ".join(
            f"({start}<TIMESTAMP {_q(day)}+INTERVAL '1 day' AND {end}>=TIMESTAMP {_q(day)})"
            for day in KNOWN_GAP_DATES
        ) + ")"

    target_gap = gap_overlap('f.snapshot_ts', "f.snapshot_ts+INTERVAL '24 hours'")
    split_boundary = "date_part('year', f.snapshot_ts) IN (2020, 2023, 2024, 2025) AND date_part('year', f.snapshot_ts + INTERVAL '24 hours') <> date_part('year', f.snapshot_ts)"
    lookback_flags = ",\n        ".join(
        f"{gap_overlap(f'f.snapshot_ts-INTERVAL \'{hours} hours\'', 'f.snapshot_ts')} whole_day_no_records_in_lookback_{label}, "
        f"(f.snapshot_ts-INTERVAL '{hours} hours'>=b.min_hour AND NOT "
        f"{gap_overlap(f'f.snapshot_ts-INTERVAL \'{hours} hours\'', 'f.snapshot_ts')}) lookback_complete_{label}"
        for hours, label in ((1, '1h'), (6, '6h'), (24, '24h'), (168, '7d'))
    )
    return f"""
      WITH source AS (SELECT * FROM read_parquet({files})),
      objects AS (SELECT object_id,object_name,channels_count current_reference_channel_count
                  FROM read_json_auto({_q(str(objects_reference))}) WHERE channels_count>0),
      bounds AS (SELECT min(hour_ts) min_hour,max(last_event_ts) max_event_ts FROM source),
      snapshots AS (
        SELECT object_id,snapshot_ts FROM objects CROSS JOIN bounds,
        generate_series(date_trunc('day',min_hour)+INTERVAL '5 hours 59 minutes 59 seconds',
                        max_event_ts,INTERVAL '6 hours') AS g(snapshot_ts)
      ), features AS (
        SELECT s.object_id,s.snapshot_ts,o.object_name,o.current_reference_channel_count,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '1 hour' THEN h.event_count ELSE 0 END) event_count_1h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '1 hour' THEN h.alarm_count ELSE 0 END) alarm_count_1h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '1 hour' THEN h.onset_count ELSE 0 END) onset_count_1h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '6 hours' THEN h.event_count ELSE 0 END) event_count_6h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '6 hours' THEN h.alarm_count ELSE 0 END) alarm_count_6h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '6 hours' THEN h.onset_count ELSE 0 END) onset_count_6h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.event_count ELSE 0 END) event_count_24h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.alarm_count ELSE 0 END) alarm_count_24h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.onset_count ELSE 0 END) onset_count_24h,
          sum(coalesce(h.event_count,0)) event_count_7d,
          sum(coalesce(h.alarm_count,0)) alarm_count_7d,
          sum(coalesce(h.onset_count,0)) onset_count_7d,
          count(DISTINCT CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.channel_id END) active_channels_24h,
          count(DISTINCT CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' AND h.alarm_count>0 THEN h.channel_id END) alarm_channels_24h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.invalid_alarm_count ELSE 0 END) invalid_alarm_count_24h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.numeric_value_count ELSE 0 END) numeric_value_count_24h,
          sum(CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.raw_value_count ELSE 0 END) raw_value_count_24h,
          count(DISTINCT CASE WHEN h.hour_ts>s.snapshot_ts-INTERVAL '24 hours' THEN h.hour_ts END) observed_hours_24h,
          date_diff('second',max(h.last_event_ts),s.snapshot_ts)::DOUBLE/3600.0 hours_since_last_observation_7d,
          {systems}
        FROM snapshots s JOIN objects o USING(object_id)
        LEFT JOIN source h ON h.object_id=s.object_id
          AND h.hour_ts>s.snapshot_ts-INTERVAL '168 hours' AND h.hour_ts<=s.snapshot_ts
        GROUP BY s.object_id,s.snapshot_ts,o.object_name,o.current_reference_channel_count
      ), flagged AS (
        SELECT f.*,date_part('year',f.snapshot_ts)::INTEGER snapshot_year,
          CASE WHEN f.snapshot_ts+INTERVAL '24 hours'>b.max_event_ts THEN 'end_of_data'
               WHEN {target_gap} THEN 'whole_day_no_records'
               WHEN {split_boundary} THEN 'cross_split_target_window' ELSE NULL END target_incomplete_reason,
          {lookback_flags},
          f.event_count_24h>0 has_recent_data_24h
        FROM features f CROSS JOIN bounds b
      ), labeled AS (
        SELECT f.*,
          CASE WHEN target_incomplete_reason IS NOT NULL THEN NULL
               WHEN EXISTS (SELECT 1 FROM source x WHERE x.object_id=f.object_id
                 AND x.hour_ts>f.snapshot_ts AND x.hour_ts<=f.snapshot_ts+INTERVAL '24 hours'
                 AND x.onset_count>0) THEN 1 ELSE 0 END proxy_anomaly_24h,
          target_incomplete_reason IS NULL target_window_complete,
          CASE WHEN target_incomplete_reason IS NOT NULL THEN 'unlabeled_excluded'
               WHEN snapshot_year=2021 THEN 'transition_excluded'
               WHEN snapshot_year IN (2019,2020,2022,2023) THEN 'train'
               WHEN snapshot_year=2024 THEN 'validation'
               WHEN snapshot_year=2025 THEN 'test'
               WHEN snapshot_year=2026 THEN 'recent_2026' ELSE 'out_of_scope' END split
        FROM flagged f
      )
      SELECT *,active_channels_24h::DOUBLE/current_reference_channel_count channel_coverage_24h,
        CASE WHEN raw_value_count_24h>0 THEN numeric_value_count_24h::DOUBLE/raw_value_count_24h
             ELSE NULL END numeric_parse_rate_24h,
        snapshot_year=2021 transition_period,false feature_uses_future_data
      FROM labeled ORDER BY snapshot_ts,object_id
    """
