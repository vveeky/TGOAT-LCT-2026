from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from ml.features.common import (
    KNOWN_GAP_DATES,
    is_target_window_complete,
    crosses_split_role,
    snapshot_floor,
    split_for_year,
    target_window,
)
from ml.features.base_snapshots import base_snapshot_sql
from ml.features.staged import _hourly_sql


def _events_file(con: duckdb.DuckDBPyConnection, path: Path, year: int, rows: list[tuple[str, int, bool]]) -> None:
    con.execute("CREATE OR REPLACE TABLE fixture_events (timestamp TIMESTAMP, channel_id BIGINT, object_id BIGINT, object_name VARCHAR, system_type VARCHAR, is_alarm BOOLEAN, invalid_alarm BOOLEAN, sensor_value VARCHAR, source_year INTEGER)")
    con.executemany("INSERT INTO fixture_events VALUES (?, ?, 28, 'fixture', 'Пожарная охрана', ?, false, '0', ?)", [(ts, channel, alarm, year) for ts, channel, alarm in rows])
    con.execute(f"COPY fixture_events TO '{path}' (FORMAT PARQUET)")


def _objects_file(path: Path) -> Path:
    path.write_text('[{"object_id":28,"object_name":"fixture","channels_count":1}]')
    return path


@pytest.mark.parametrize("prior_alarm,current_alarm,gap_hours,expected_onsets", [
    (False, True, 1, 1),
    (True, True, 1, 0),
    (False, True, 49, 0),
])
def test_annual_state_carry_matches_gap_policy(tmp_path, prior_alarm, current_alarm, gap_hours, expected_onsets):
    con = duckdb.connect()
    previous_input = tmp_path / 'events_2019.parquet'
    previous_hourly = tmp_path / 'hourly_2019.parquet'
    current_input = tmp_path / 'events_2020.parquet'
    current_hourly = tmp_path / 'hourly_2020.parquet'
    previous_ts = datetime(2020, 1, 1, 0, 30) - timedelta(hours=gap_hours)
    _events_file(con, previous_input, 2019, [(previous_ts.isoformat(sep=' '), 777, prior_alarm)])
    con.execute(f"COPY ({_hourly_sql(2019, current_path=previous_input)}) TO '{previous_hourly}' (FORMAT PARQUET)")
    _events_file(con, current_input, 2020, [('2020-01-01 00:30:00', 777, current_alarm)])
    con.execute(f"COPY ({_hourly_sql(2020, current_path=current_input, previous_path=previous_hourly)}) TO '{current_hourly}' (FORMAT PARQUET)")
    assert con.execute(f"SELECT sum(onset_count) FROM read_parquet('{current_hourly}')").fetchone()[0] == expected_onsets


def test_2021_state_is_carried_into_2022(tmp_path):
    con = duckdb.connect()
    prior_input = tmp_path / 'events_2021.parquet'
    prior_hourly = tmp_path / 'hourly_2021.parquet'
    next_input = tmp_path / 'events_2022.parquet'
    next_hourly = tmp_path / 'hourly_2022.parquet'
    _events_file(con, prior_input, 2021, [('2021-12-31 23:30:00', 777, False)])
    empty_prior = tmp_path / 'hourly_2020.parquet'
    con.execute(f"COPY (SELECT NULL::BIGINT channel_id, NULL::TIMESTAMP last_event_ts, NULL::INTEGER last_alarm_state WHERE false) TO '{empty_prior}' (FORMAT PARQUET)")
    con.execute(f"COPY ({_hourly_sql(2021, current_path=prior_input, previous_path=empty_prior)}) TO '{prior_hourly}' (FORMAT PARQUET)")
    _events_file(con, next_input, 2022, [('2022-01-01 00:30:00', 777, True)])
    con.execute(f"COPY ({_hourly_sql(2022, current_path=next_input, previous_path=prior_hourly)}) TO '{next_hourly}' (FORMAT PARQUET)")
    assert con.execute(f"SELECT sum(onset_count) FROM read_parquet('{next_hourly}')").fetchone()[0] == 1


def test_long_gap_same_policy_inside_year(tmp_path):
    con = duckdb.connect()
    events = tmp_path / 'events_2019.parquet'
    hourly = tmp_path / 'hourly_2019.parquet'
    _events_file(con, events, 2019, [
        ('2019-01-01 00:30:00', 777, False),
        ('2019-01-03 01:30:00', 777, True),
    ])
    con.execute(f"COPY ({_hourly_sql(2019, current_path=events)}) TO '{hourly}' (FORMAT PARQUET)")
    assert con.execute(f"SELECT sum(onset_count) FROM read_parquet('{hourly}')").fetchone()[0] == 0


def test_duplicate_event_rows_are_counted_without_event_id_dedup(tmp_path):
    con = duckdb.connect()
    events = tmp_path / 'events_2019.parquet'
    hourly = tmp_path / 'hourly_2019.parquet'
    _events_file(con, events, 2019, [
        ('2019-01-01 00:30:00', 777, False),
        ('2019-01-01 01:30:00', 777, True),
        ('2019-01-01 01:30:00', 777, True),
    ])
    con.execute(f"COPY ({_hourly_sql(2019, current_path=events)}) TO '{hourly}' (FORMAT PARQUET)")
    assert con.execute(f"SELECT sum(event_count),sum(alarm_count),sum(onset_count) FROM read_parquet('{hourly}')").fetchone() == (3, 2, 1)


def test_target_includes_right_boundary_and_excludes_snapshot_instant(tmp_path):
    con = duckdb.connect()
    events = tmp_path / 'events_2019.parquet'
    hourly = tmp_path / 'hourly_2019.parquet'
    _events_file(con, events, 2019, [
        ('2019-01-01 05:00:00', 777, False),
        ('2019-01-01 05:59:59', 777, True),
        ('2019-01-01 06:00:00', 778, False),
        ('2019-01-02 05:59:59', 778, True),
        ('2019-01-02 06:30:00', 778, False),
    ])
    con.execute(f"COPY ({_hourly_sql(2019, current_path=events)}) TO '{hourly}' (FORMAT PARQUET)")
    con.execute(f"CREATE OR REPLACE VIEW snapshots AS {base_snapshot_sql([hourly], _objects_file(tmp_path / 'objects.json'))}")
    row = con.execute("SELECT onset_count_1h,proxy_anomaly_24h FROM snapshots WHERE object_id=28 AND snapshot_ts=TIMESTAMP '2019-01-01 05:59:59'").fetchone()
    assert row == (1, 1)


def test_known_gap_and_end_of_data_labels_are_unknown(tmp_path):
    con = duckdb.connect()
    events = tmp_path / 'events_2024.parquet'
    hourly = tmp_path / 'hourly_2024.parquet'
    empty_prior = tmp_path / 'hourly_2023.parquet'
    con.execute(f"COPY (SELECT NULL::BIGINT channel_id, NULL::TIMESTAMP last_event_ts, NULL::INTEGER last_alarm_state WHERE false) TO '{empty_prior}' (FORMAT PARQUET)")
    _events_file(con, events, 2024, [
        ('2024-04-06 05:30:00', 777, False),
        ('2024-04-09 05:30:00', 777, True),
    ])
    con.execute(f"COPY ({_hourly_sql(2024, current_path=events, previous_path=empty_prior)}) TO '{hourly}' (FORMAT PARQUET)")
    con.execute(f"CREATE OR REPLACE VIEW snapshots AS {base_snapshot_sql([hourly], _objects_file(tmp_path / 'objects.json'))}")
    gap = con.execute("SELECT proxy_anomaly_24h, target_incomplete_reason, split FROM snapshots WHERE object_id=28 AND snapshot_ts=TIMESTAMP '2024-04-06 05:59:59'").fetchone()
    assert gap == (None, 'whole_day_no_records', 'unlabeled_excluded')
    missing_day = con.execute("SELECT proxy_anomaly_24h, whole_day_no_records_in_lookback_24h, lookback_complete_24h FROM snapshots WHERE object_id=28 AND snapshot_ts=TIMESTAMP '2024-04-08 05:59:59'").fetchone()
    assert missing_day == (None, True, False)
    end = con.execute("SELECT proxy_anomaly_24h, target_incomplete_reason, split FROM snapshots WHERE object_id=28 AND snapshot_ts=TIMESTAMP '2024-04-08 23:59:59'").fetchone()
    assert end == (None, 'end_of_data', 'unlabeled_excluded')


def test_snapshot_floor_and_target_boundary():
    ts = datetime(2024, 1, 1, 5, 59, 59)
    assert snapshot_floor(ts) == datetime(2024, 1, 1, 5, 59, 59)
    start, end = target_window(datetime(2024, 1, 1))
    assert start == datetime(2024, 1, 1)
    assert end == datetime(2024, 1, 2)


def test_target_does_not_cross_known_gap_or_dataset_end():
    assert not is_target_window_complete(datetime(2024, 4, 6, 18), datetime(2026, 6, 30, 23, 59, 59))
    assert not is_target_window_complete(datetime(2026, 6, 30), datetime(2026, 6, 30, 23, 59, 59))
    assert KNOWN_GAP_DATES == ("2024-04-07", "2024-04-08", "2026-06-01")


def test_temporal_split_keeps_transition_year_out_of_baseline():
    assert split_for_year(2019) == "train"
    assert split_for_year(2021) == "transition_excluded"
    assert split_for_year(2024) == "validation"
    assert split_for_year(2025) == "test"
    assert split_for_year(2026) == "recent_2026"


@pytest.mark.parametrize("ts,expected", [
    (datetime(2020, 12, 31, 23, 59, 59), True),
    (datetime(2023, 12, 31, 23, 59, 59), True),
    (datetime(2024, 12, 31, 23, 59, 59), True),
    (datetime(2025, 12, 31, 23, 59, 59), True),
    (datetime(2019, 12, 31, 23, 59, 59), False),
    (datetime(2022, 12, 31, 23, 59, 59), False),
    (datetime(2021, 12, 31, 23, 59, 59), False),
])
def test_cross_split_target_window_purge(ts, expected):
    assert crosses_split_role(ts) is expected


def test_future_events_can_change_target_but_not_features(tmp_path):
    con = duckdb.connect()
    empty = tmp_path / 'hourly_2018.parquet'
    con.execute(f"COPY (SELECT NULL::BIGINT channel_id, NULL::TIMESTAMP last_event_ts, NULL::INTEGER last_alarm_state WHERE false) TO '{empty}' (FORMAT PARQUET)")
    before = tmp_path / 'events_before.parquet'
    after = tmp_path / 'events_after.parquet'
    common = [
        ('2019-01-01 05:30:00', 777, False),
        ('2019-01-01 06:00:00', 777, False),
        ('2019-01-02 06:00:00', 777, False),
    ]
    _events_file(con, before, 2019, common)
    _events_file(con, after, 2019, common + [('2019-01-01 06:30:00', 777, True)])
    before_hourly = tmp_path / 'hourly_before.parquet'
    after_hourly = tmp_path / 'hourly_after.parquet'
    con.execute(f"COPY ({_hourly_sql(2019, current_path=before, previous_path=empty)}) TO '{before_hourly}' (FORMAT PARQUET)")
    con.execute(f"COPY ({_hourly_sql(2019, current_path=after, previous_path=empty)}) TO '{after_hourly}' (FORMAT PARQUET)")
    objects = _objects_file(tmp_path / 'objects.json')
    before_sql = base_snapshot_sql([before_hourly], objects)
    after_sql = base_snapshot_sql([after_hourly], objects)
    before_row = con.execute(f"SELECT * FROM ({before_sql}) WHERE snapshot_ts=TIMESTAMP '2019-01-01 05:59:59'").fetchone()
    after_row = con.execute(f"SELECT * FROM ({after_sql}) WHERE snapshot_ts=TIMESTAMP '2019-01-01 05:59:59'").fetchone()
    columns = [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM ({before_sql})").fetchall()]
    excluded = {'proxy_anomaly_24h', 'target_incomplete_reason', 'target_window_complete'}
    assert before_row[columns.index('proxy_anomaly_24h')] == 0
    assert after_row[columns.index('proxy_anomaly_24h')] == 1
    assert [before_row[i] for i, name in enumerate(columns) if name not in excluded] == [after_row[i] for i, name in enumerate(columns) if name not in excluded]
