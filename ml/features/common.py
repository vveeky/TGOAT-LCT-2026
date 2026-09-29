from __future__ import annotations

from datetime import datetime, timedelta

HORIZON_HOURS = 24
SNAPSHOT_HOURS = 6
LOOKBACK_HOURS = (1, 6, 24, 168)
TRANSITION_YEAR = 2021
TRAIN_YEARS = (2019, 2020, 2022, 2023)
VALIDATION_YEAR = 2024
TEST_YEAR = 2025
RECENT_YEAR = 2026
KNOWN_GAP_DATES = ("2024-04-07", "2024-04-08", "2026-06-01")


def snapshot_floor(timestamp: datetime) -> datetime:
    """Return the canonical end timestamp of the containing six-hour bucket."""
    bucket_hour = (timestamp.hour // SNAPSHOT_HOURS) * SNAPSHOT_HOURS
    return timestamp.replace(hour=bucket_hour + SNAPSHOT_HOURS - 1,
                             minute=59, second=59, microsecond=0)


def split_role_for_year(year: int) -> str:
    """Return the supervised temporal role, including the excluded transition."""
    return split_for_year(year)


def crosses_split_role(snapshot_ts: datetime) -> bool:
    """Whether a 24-hour target leaves the snapshot's supervised split role."""
    start_role = split_role_for_year(snapshot_ts.year)
    end_role = split_role_for_year((snapshot_ts + timedelta(hours=HORIZON_HOURS)).year)
    return start_role in {"train", "validation", "test", "recent_2026"} and start_role != end_role


def target_window(snapshot_ts: datetime, horizon_hours: int = HORIZON_HOURS) -> tuple[datetime, datetime]:
    return snapshot_ts, snapshot_ts + timedelta(hours=horizon_hours)


def split_for_year(year: int) -> str:
    if year in TRAIN_YEARS:
        return "train"
    if year == VALIDATION_YEAR:
        return "validation"
    if year == TEST_YEAR:
        return "test"
    if year == RECENT_YEAR:
        return "recent_2026"
    if year == TRANSITION_YEAR:
        return "transition_excluded"
    return "out_of_scope"


def is_target_window_complete(snapshot_ts: datetime, max_event_ts: datetime) -> bool:
    start, end = target_window(snapshot_ts)
    if end > max_event_ts:
        return False
    dates = {start.date().isoformat(), (end - timedelta(seconds=1)).date().isoformat()}
    return not dates.intersection(KNOWN_GAP_DATES)
