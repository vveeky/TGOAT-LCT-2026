"""Add sparse diagnostics and derived safe rates to materialized snapshots."""

from __future__ import annotations

from pathlib import Path

from .build_risk_dataset import SYSTEM_TYPES, _q


def diagnostic_features_sql(base: Path, diagnostics: Path) -> str:
    return f"""
      WITH base AS (SELECT object_id,snapshot_ts FROM read_parquet({_q(str(base))})),
      diagnostics AS (SELECT * FROM read_parquet({_q(str(diagnostics))}))
      SELECT b.object_id,b.snapshot_ts,
        sum(CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '1 hour' THEN d.malfunction_text_count ELSE 0 END) malfunction_text_count_1h,
        sum(CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '6 hours' THEN d.malfunction_text_count ELSE 0 END) malfunction_text_count_6h,
        sum(CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '24 hours' THEN d.malfunction_text_count ELSE 0 END) malfunction_text_count_24h,
        sum(coalesce(d.malfunction_text_count,0)) malfunction_text_count_7d,
        count(DISTINCT CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '24 hours' AND d.malfunction_text_count>0 THEN d.channel_id END) malfunction_text_channels_24h,
        date_diff('second',max(d.last_malfunction_text_ts),b.snapshot_ts)::DOUBLE/3600.0 hours_since_last_malfunction_text_7d,
        sum(CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '24 hours' THEN d.special_code_minus_100_count ELSE 0 END) special_code_minus_100_count_24h,
        sum(CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '24 hours' THEN d.special_code_255_count ELSE 0 END) special_code_255_count_24h,
        sum(CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '24 hours' THEN d.special_code_minus_127_count ELSE 0 END) special_code_minus_127_count_24h,
        sum(CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '24 hours' THEN d.special_code_minus_3276_count ELSE 0 END) special_code_minus_3276_count_24h,
        count(DISTINCT CASE WHEN d.hour_ts>b.snapshot_ts-INTERVAL '24 hours' AND
          d.special_code_minus_100_count+d.special_code_255_count+d.special_code_minus_127_count+d.special_code_minus_3276_count>0
          THEN d.channel_id END) special_code_channels_24h
      FROM base b LEFT JOIN diagnostics d ON d.object_id=b.object_id
        AND d.hour_ts>b.snapshot_ts-INTERVAL '168 hours' AND d.hour_ts<=b.snapshot_ts
      GROUP BY b.object_id,b.snapshot_ts ORDER BY b.snapshot_ts,b.object_id
    """


def alarm_recency_sql(base: Path, hourly: list[Path]) -> str:
    hourly_files = "[" + ", ".join(_q(str(path)) for path in hourly) + "]"
    return f"""
      WITH base AS (SELECT object_id,snapshot_ts FROM read_parquet({_q(str(base))})),
      alarm_hours AS (
        SELECT object_id,hour_ts,alarm_count,onset_count FROM read_parquet({hourly_files})
        WHERE alarm_count>0 OR onset_count>0
      )
      SELECT b.object_id,b.snapshot_ts,
        date_diff('second',max(CASE WHEN h.alarm_count>0 THEN h.hour_ts END),b.snapshot_ts)::DOUBLE/3600.0 hours_since_last_alarm_hour_7d,
        date_diff('second',max(CASE WHEN h.onset_count>0 THEN h.hour_ts END),b.snapshot_ts)::DOUBLE/3600.0 hours_since_last_onset_hour_7d
      FROM base b LEFT JOIN alarm_hours h ON h.object_id=b.object_id
        AND h.hour_ts>b.snapshot_ts-INTERVAL '168 hours' AND h.hour_ts<=b.snapshot_ts
      GROUP BY b.object_id,b.snapshot_ts ORDER BY b.snapshot_ts,b.object_id
    """


def augment_sql(base: Path, diagnostics: Path, recency: Path, channels_reference: Path) -> str:
    reference_columns = ",\n        ".join(
        f"count(*) FILTER (WHERE system_type={_q(system)}) system_{slug}_current_reference_channels"
        for slug, system in SYSTEM_TYPES
    )
    reference_output = ",\n      ".join(
        f"rs.system_{slug}_current_reference_channels, "
        f"CASE WHEN rs.system_{slug}_current_reference_channels>0 "
        f"THEN b.system_{slug}_active_channels_24h::DOUBLE/rs.system_{slug}_current_reference_channels "
        f"ELSE NULL END system_{slug}_coverage_24h"
        for slug, _ in SYSTEM_TYPES
    )
    return f"""
      WITH base AS (SELECT * FROM read_parquet({_q(str(base))})),
      diagnostics AS (SELECT * FROM read_parquet({_q(str(diagnostics))})),
      recency AS (SELECT * FROM read_parquet({_q(str(recency))})),
      reference_system AS (
        SELECT object_id,{reference_columns}
        FROM read_json_auto({_q(str(channels_reference))}) GROUP BY object_id
      ),
      diagnostic_features AS (SELECT * FROM diagnostics),
      alarm_recency AS (SELECT * FROM recency)
      SELECT b.* EXCLUDE (target_incomplete_reason,whole_day_no_records_in_lookback_1h,whole_day_no_records_in_lookback_6h,whole_day_no_records_in_lookback_24h,whole_day_no_records_in_lookback_7d),
        b.target_incomplete_reason,
        b.whole_day_no_records_in_lookback_1h,
        b.whole_day_no_records_in_lookback_6h,
        b.whole_day_no_records_in_lookback_24h,
        b.whole_day_no_records_in_lookback_7d,
        d.* EXCLUDE (object_id,snapshot_ts),r.* EXCLUDE (object_id,snapshot_ts),
        CASE WHEN b.event_count_1h>0 THEN b.alarm_count_1h::DOUBLE/b.event_count_1h ELSE NULL END alarm_rate_1h,
        CASE WHEN b.event_count_6h>0 THEN b.alarm_count_6h::DOUBLE/b.event_count_6h ELSE NULL END alarm_rate_6h,
        CASE WHEN b.event_count_24h>0 THEN b.alarm_count_24h::DOUBLE/b.event_count_24h ELSE NULL END alarm_rate_24h,
        CASE WHEN b.event_count_7d>0 THEN b.alarm_count_7d::DOUBLE/b.event_count_7d ELSE NULL END alarm_rate_7d,
        CASE WHEN b.event_count_6h>0 AND b.event_count_24h>0 THEN b.alarm_count_6h::DOUBLE/b.event_count_6h-b.alarm_count_24h::DOUBLE/b.event_count_24h ELSE NULL END alarm_rate_delta_6h_vs_24h,
        CASE WHEN b.event_count_24h>0 AND b.event_count_7d>0 THEN b.alarm_count_24h::DOUBLE/b.event_count_24h-b.alarm_count_7d::DOUBLE/b.event_count_7d ELSE NULL END alarm_rate_delta_24h_vs_7d,
        CASE WHEN b.active_channels_24h>0 THEN b.event_count_24h::DOUBLE/b.active_channels_24h ELSE NULL END events_per_active_channel_24h,
        {reference_output}
      FROM base b JOIN diagnostic_features d USING(object_id,snapshot_ts)
        JOIN alarm_recency r USING(object_id,snapshot_ts)
        LEFT JOIN reference_system rs USING(object_id)
      ORDER BY b.snapshot_ts,b.object_id
    """
