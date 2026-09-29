from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from backend.app.services.ml_features import (
    FEATURE_NAMES, FeatureProviderError, HourlyState, InMemoryStateStore,
    MappingFeatureStore, RawObservation, SQLiteFeatureStateStore, StateFeatureProvider,
    hydrate_hourly_state, is_canonical_snapshot, latest_canonical_snapshot,
)
from backend.app.services.demo_feature_state import bootstrap_demo_feature_state
from backend.app.config import as_utc
from backend.app.main import app
from backend.app.services.auth import auth_provider
from backend.app.services.risk_inference import RiskInferenceService
from backend.app.services.runtime_ml import build_risk_inference_service
from backend.app.services.reference_data import get_channels_data, get_objects_data, get_reference_meta
from ml.inference.load_risk_model import FrozenRiskModel


UTC = timezone.utc


def row(ts, *, alarm=0, onset=0, event=1, system="gas", **kwargs):
    return HourlyState(
        object_id=1, channel_id=11, hour_ts=ts, system_type=system, event_count=event,
        alarm_count=alarm, onset_count=onset, active_channels=1,
        raw_value_count=1, numeric_value_count=1, last_event_ts=ts,
        last_alarm_ts=ts if alarm else None, last_onset_ts=ts if onset else None,
        **kwargs,
    )


def test_state_provider_returns_exact_frozen_schema_and_order():
    snapshot = datetime(2024, 4, 6, 23, 59, 59, tzinfo=UTC)
    state = InMemoryStateStore(
        [row(datetime(2024, 4, 6, 23, tzinfo=UTC), alarm=1, onset=1)], {1: 2}, {(1, "gas"): 1}
    )
    values = StateFeatureProvider(state).snapshot(1, snapshot)
    assert tuple(values) == FEATURE_NAMES
    assert len(values) == 87
    assert values["current_reference_channel_count"] == 2.0
    assert values["alarm_count_1h"] == 1.0
    assert values["whole_day_no_records_in_lookback_1h"] is False


def test_state_provider_does_not_use_future_rows():
    snapshot = datetime(2024, 1, 1, 5, 59, 59, tzinfo=UTC)
    state = InMemoryStateStore([
        row(datetime(2024, 1, 1, 5, tzinfo=UTC), alarm=0),
        row(datetime(2024, 1, 1, 6, tzinfo=UTC), alarm=1, onset=1),
    ], {1: 1}, {(1, "gas"): 1})
    values = StateFeatureProvider(state).snapshot(1, snapshot)
    assert values["event_count_1h"] == 1.0
    assert values["alarm_count_1h"] == 0.0
    assert values["onset_count_1h"] == 0.0


def test_window_boundary_is_open_at_start_and_closed_at_snapshot():
    snapshot = datetime(2024, 1, 1, 5, 59, 59, tzinfo=UTC)
    state = InMemoryStateStore([
        row(datetime(2024, 1, 1, 4, 59, 59, tzinfo=UTC), event=1),
        row(datetime(2024, 1, 1, 5, 0, 0, tzinfo=UTC), event=1),
        row(snapshot, event=1),
    ], {1: 1}, {(1, "gas"): 1})
    values = StateFeatureProvider(state).snapshot(1, snapshot)
    assert values["event_count_1h"] == 2.0


def test_open_hour_overlay_excludes_events_after_as_of_and_preserves_closed_hour_parity():
    start = datetime(2024, 1, 1, 9, tzinfo=UTC)
    observations = [
        RawObservation(1, 11, start + timedelta(minutes=10), "gas", False),
        # A future alarm must not leak into any partial-hour alarm/onset or
        # recency feature, not merely into the event-count feature.
        RawObservation(1, 11, start + timedelta(minutes=50), "gas", True),
    ]
    hourly = hydrate_hourly_state(observations)
    state = InMemoryStateStore(
        hourly, {1: 1}, {(1, "gas"): 1},
        available_from=start - timedelta(hours=168), available_to=start + timedelta(minutes=59, seconds=59),
        raw_observations=observations,
        event_coverage_from=start - timedelta(hours=1),
        event_coverage_to=start + timedelta(minutes=59, seconds=59),
    )
    provider = StateFeatureProvider(state, require_complete_coverage=True)

    partial = provider.snapshot(1, start + timedelta(minutes=30))
    assert partial["event_count_1h"] == 1.0
    assert partial["event_count_6h"] == 1.0
    assert partial["alarm_count_1h"] == 0.0
    assert partial["onset_count_1h"] == 0.0
    assert partial["hours_since_last_alarm_hour_7d"] is None
    assert partial["hours_since_last_onset_hour_7d"] is None
    assert partial["hours_since_last_observation_7d"] == pytest.approx(1 / 3)

    closed = provider.snapshot(1, start + timedelta(minutes=59, seconds=59))
    legacy = StateFeatureProvider(InMemoryStateStore(
        hourly, {1: 1}, {(1, "gas"): 1},
        available_from=start - timedelta(hours=168), available_to=start + timedelta(minutes=59, seconds=59),
    )).snapshot(1, start + timedelta(minutes=59, seconds=59))
    assert closed == legacy
    assert closed["event_count_1h"] == 2.0
    assert closed["alarm_count_1h"] == 1.0
    assert closed["onset_count_1h"] == 1.0


@pytest.mark.parametrize(("prior_alarm", "expected_onsets"), [(False, 1.0), (True, 0.0)])
def test_open_hour_overlay_carries_prior_channel_alarm_state(prior_alarm, expected_onsets):
    hour = datetime(2024, 1, 1, 9, tzinfo=UTC)
    observations = [
        RawObservation(1, 11, hour - timedelta(minutes=10), "gas", prior_alarm),
        RawObservation(1, 11, hour + timedelta(minutes=10), "gas", True),
        RawObservation(1, 11, hour + timedelta(minutes=50), "gas", True),
    ]
    state = InMemoryStateStore(
        hydrate_hourly_state(observations), {1: 1}, {(1, "gas"): 1},
        available_from=hour - timedelta(hours=169), available_to=hour + timedelta(minutes=50),
        raw_observations=observations, event_coverage_from=hour - timedelta(hours=1),
        event_coverage_to=hour + timedelta(minutes=50),
    )
    values = StateFeatureProvider(state, require_complete_coverage=True).snapshot(
        1, hour + timedelta(minutes=30)
    )
    assert values["onset_count_1h"] == expected_onsets
    assert values["onset_count_6h"] == expected_onsets


@pytest.mark.parametrize("coverage", ["missing_source", "late_start", "stale_end"])
def test_open_hour_scoring_fails_closed_without_complete_event_source(coverage):
    hour = datetime(2024, 1, 1, 9, tzinfo=UTC)
    event = RawObservation(1, 11, hour + timedelta(minutes=10), "gas", False)
    store_options = {}
    if coverage != "missing_source":
        store_options = {
            "raw_observations": [event],
            "event_coverage_from": (hour + timedelta(minutes=1) if coverage == "late_start" else hour),
            "event_coverage_to": (hour + timedelta(minutes=29) if coverage == "stale_end"
                                  else hour + timedelta(minutes=30)),
        }
    state = InMemoryStateStore(
        hydrate_hourly_state([event]), {1: 1}, {(1, "gas"): 1},
        available_from=hour - timedelta(hours=168), available_to=hour + timedelta(minutes=30),
        **store_options,
    )
    with pytest.raises(FeatureProviderError, match="event-level source"):
        StateFeatureProvider(state, require_complete_coverage=True).snapshot(
            1, hour + timedelta(minutes=30)
        )


def test_open_hour_event_source_survives_sqlite_restart(tmp_path):
    hour = datetime(2024, 1, 1, 9, tzinfo=UTC)
    observations = [
        RawObservation(1, 11, hour + timedelta(minutes=10), "gas", False),
        RawObservation(1, 11, hour + timedelta(minutes=50), "gas", False),
    ]
    rows = hydrate_hourly_state(observations)
    kwargs = dict(
        rows=rows, reference_counts={1: 1}, system_reference_counts={(1, "gas"): 1},
        available_from=hour - timedelta(hours=168), available_to=hour + timedelta(minutes=50),
        reference_snapshot_id="reference-v2", reference_hash="a" * 64,
        raw_observations=observations, event_coverage_from=hour - timedelta(hours=1),
        event_coverage_to=hour + timedelta(minutes=50),
    )
    warm = SQLiteFeatureStateStore.create(tmp_path / "event-aware.sqlite", **kwargs)
    expected = StateFeatureProvider(warm, require_complete_coverage=True).snapshot(
        1, hour + timedelta(minutes=30)
    )
    restarted = SQLiteFeatureStateStore(tmp_path / "event-aware.sqlite")
    actual = StateFeatureProvider(restarted, require_complete_coverage=True).snapshot(
        1, hour + timedelta(minutes=30)
    )
    assert actual == expected
    assert restarted.raw_observations == observations


def test_sqlite_restart_preserves_prior_alarm_state_for_open_hour_onset(tmp_path):
    hour = datetime(2024, 1, 1, 9, tzinfo=UTC)
    prior = RawObservation(1, 11, hour - timedelta(minutes=10), "gas", False)
    current = RawObservation(1, 11, hour + timedelta(minutes=10), "gas", True)
    path = tmp_path / "restart-onset-carry.sqlite"
    SQLiteFeatureStateStore.create(
        path,
        rows=hydrate_hourly_state([prior]),
        reference_counts={1: 1},
        system_reference_counts={(1, "gas"): 1},
        available_from=hour - timedelta(hours=168),
        available_to=hour + timedelta(minutes=30),
        reference_snapshot_id="reference-v2",
        reference_hash="a" * 64,
        raw_observations=[current],
        event_coverage_from=hour,
        event_coverage_to=hour + timedelta(minutes=30),
    )

    restarted = SQLiteFeatureStateStore(path)
    values = StateFeatureProvider(restarted, require_complete_coverage=True).snapshot(
        1, hour + timedelta(minutes=30)
    )

    assert restarted.channel_states_before(1, hour) == {11: (False, hour - timedelta(minutes=10))}
    assert values["event_count_1h"] == 1.0
    assert values["alarm_count_1h"] == 1.0
    assert values["onset_count_1h"] == 1.0


def test_replacing_sqlite_event_window_is_durable_before_success(tmp_path):
    hour = datetime(2024, 1, 1, 9, tzinfo=UTC)
    initial = RawObservation(1, 11, hour + timedelta(minutes=10), "gas", False)
    replacement = RawObservation(1, 11, hour + timedelta(minutes=20), "gas", True)
    path = tmp_path / "replace-event-window.sqlite"
    store = SQLiteFeatureStateStore.create(
        path, rows=hydrate_hourly_state([initial]), reference_counts={1: 1},
        system_reference_counts={(1, "gas"): 1},
        available_from=hour - timedelta(hours=168), available_to=hour + timedelta(hours=20),
        reference_snapshot_id="reference-v2", reference_hash="a" * 64,
        raw_observations=[initial], event_coverage_from=hour,
        event_coverage_to=hour + timedelta(minutes=10),
    )

    store.replace_event_window(
        [replacement], available_from=hour, available_to=hour + timedelta(minutes=20)
    )
    restarted = SQLiteFeatureStateStore(path)

    assert restarted.raw_observations == [replacement]
    assert restarted.event_coverage_to == hour + timedelta(minutes=20)


def test_hydrator_applies_same_gap_policy_across_years():
    states = hydrate_hourly_state([
        RawObservation(1, 11, datetime(2021, 12, 30, 23, tzinfo=UTC), "gas", False),
        RawObservation(1, 11, datetime(2022, 1, 1, 0, tzinfo=UTC), "gas", True),
    ])
    assert [state.onset_count for state in states] == [0, 0]


def test_year_boundary_state_is_not_reset():
    snapshot = datetime(2022, 1, 1, 5, 59, 59, tzinfo=UTC)
    state = InMemoryStateStore([
        row(datetime(2021, 12, 31, 23, tzinfo=UTC), alarm=1),
        row(datetime(2022, 1, 1, 5, tzinfo=UTC), alarm=1),
    ], {1: 1}, {(1, "gas"): 1})
    values = StateFeatureProvider(state).snapshot(1, snapshot)
    assert values["alarm_count_7d"] == 2.0
    assert values["alarm_count_24h"] == 2.0


def test_hydrator_carries_alarm_state_across_year_boundary():
    states = hydrate_hourly_state([
        RawObservation(1, 11, datetime(2021, 12, 31, 23, tzinfo=UTC), "gas", False),
        RawObservation(1, 11, datetime(2022, 1, 1, 0, tzinfo=UTC), "gas", True),
    ])
    assert [state.onset_count for state in states] == [0, 1]


def test_hydrator_does_not_create_onset_for_true_to_true():
    states = hydrate_hourly_state([
        RawObservation(1, 11, datetime(2021, 12, 31, 23, tzinfo=UTC), "gas", True),
        RawObservation(1, 11, datetime(2022, 1, 1, 0, tzinfo=UTC), "gas", True),
    ])
    assert [state.onset_count for state in states] == [0, 0]


def test_hydrator_groups_same_timestamp_before_transition_logic():
    states = hydrate_hourly_state([
        RawObservation(1, 11, datetime(2024, 1, 1, 0, tzinfo=UTC), "gas", False),
        RawObservation(1, 11, datetime(2024, 1, 1, 1, tzinfo=UTC), "gas", False),
        RawObservation(1, 11, datetime(2024, 1, 1, 1, tzinfo=UTC), "gas", True),
    ])
    assert states[-1].event_count == 2
    assert states[-1].alarm_count == 1
    assert states[-1].onset_count == 1


def test_pre_window_predecessor_affects_first_event_in_window():
    predecessor = datetime(2024, 1, 1, 0, tzinfo=UTC)
    first_in_window = datetime(2024, 1, 1, 12, tzinfo=UTC)
    states = hydrate_hourly_state([
        RawObservation(1, 11, predecessor, "gas", False),
        RawObservation(1, 11, first_in_window, "gas", True),
    ])
    state = InMemoryStateStore(states, {1: 1}, {(1, "gas"): 1})
    values = StateFeatureProvider(state).snapshot(
        1, datetime(2024, 1, 1, 12, 59, 59, tzinfo=UTC)
    )
    assert values["onset_count_1h"] == 1.0


def test_whole_day_no_records_is_a_feature_flag_not_filled_events():
    snapshot = datetime(2024, 4, 8, 5, 59, 59, tzinfo=UTC)
    state = InMemoryStateStore([], {1: 1}, {(1, "gas"): 1})
    values = StateFeatureProvider(state).snapshot(1, snapshot)
    assert values["whole_day_no_records_in_lookback_24h"] is True
    assert values["has_recent_data_24h"] is False
    assert values["event_count_24h"] == 0.0


def test_diagnostic_channel_features_count_distinct_channels_not_channel_hours():
    snapshot = datetime(2024, 4, 6, 23, 59, 59, tzinfo=UTC)
    states = [
        row(datetime(2024, 4, 6, 20, tzinfo=UTC), system="diagnostic",
            malfunction_text_channels=1, special_code_channels=1),
        row(datetime(2024, 4, 6, 21, tzinfo=UTC), system="diagnostic",
            malfunction_text_channels=1, special_code_channels=1),
    ]
    values = StateFeatureProvider(InMemoryStateStore(
        states, {1: 1}, {(1, "diagnostic"): 1}
    )).snapshot(1, snapshot)

    assert values["malfunction_text_channels_24h"] == 1.0
    assert values["special_code_channels_24h"] == 1.0


def test_unrecognized_numeric_diagnostic_is_not_a_special_code_channel():
    states = hydrate_hourly_state([
        RawObservation(1, 11, datetime(2024, 4, 6, 20, tzinfo=UTC), "diagnostic", False,
                       special_code=123),
    ])
    assert states[0].special_code_channels == 0


def test_recognized_special_code_counts_on_non_diagnostic_subsystem():
    states = hydrate_hourly_state([
        RawObservation(1, 11, datetime(2024, 4, 6, 20, tzinfo=UTC), "gas", False,
                       special_code=-100),
    ])

    assert states[0].special_code_minus_100_count == 1
    assert states[0].special_code_channels == 1


def test_missing_reference_is_degraded_not_fabricated():
    with pytest.raises(FeatureProviderError, match="reference unavailable"):
        StateFeatureProvider(InMemoryStateStore([], {})).snapshot(
            1, datetime(2024, 1, 1, tzinfo=UTC)
        )


def test_unmatched_historical_channel_stays_in_counts_but_not_reference_denominator():
    states = hydrate_hourly_state([
        RawObservation(1, 9999, datetime(2024, 1, 1, 5, tzinfo=UTC), "gas", False),
    ])
    values = StateFeatureProvider(InMemoryStateStore(
        states, {1: 1}, {(1, "gas"): 1},
        reference_snapshot_id="reference-v2", reference_hash="a" * 64,
    )).snapshot(1, datetime(2024, 1, 1, 5, 59, 59, tzinfo=UTC))
    assert values["active_channels_24h"] == 1.0
    assert values["current_reference_channel_count"] == 1.0


def test_persisted_state_restart_matches_warm_state(tmp_path):
    rows = hydrate_hourly_state([
        RawObservation(1, 11, datetime(2024, 1, 1, 0, tzinfo=UTC), "gas", False),
        RawObservation(1, 11, datetime(2024, 1, 1, 5, tzinfo=UTC), "gas", True),
    ])
    kwargs = dict(reference_counts={1: 1}, system_reference_counts={(1, "gas"): 1},
                  available_from=datetime(2019, 1, 1, tzinfo=UTC),
                  reference_snapshot_id="reference-v2", reference_hash="a" * 64)
    warm_store = InMemoryStateStore(rows, **kwargs)
    warm = StateFeatureProvider(warm_store, expected_reference_hash="a" * 64).snapshot(
        1, datetime(2024, 1, 1, 5, 59, 59, tzinfo=UTC)
    )
    persisted = SQLiteFeatureStateStore(tmp_path / "state.sqlite")
    persisted.append(rows)
    persisted.reference_counts = warm_store.reference_counts
    persisted.system_reference_counts = warm_store.system_reference_counts
    persisted.available_from = warm_store.available_from
    persisted.reference_snapshot_id = warm_store.reference_snapshot_id
    persisted.reference_hash = warm_store.reference_hash
    persisted.persist()
    restarted = StateFeatureProvider(
        SQLiteFeatureStateStore(tmp_path / "state.sqlite"), expected_reference_hash="a" * 64
    ).snapshot(1, datetime(2024, 1, 1, 5, 59, 59, tzinfo=UTC))
    assert restarted == warm


def test_reference_mismatch_fails_closed():
    state = InMemoryStateStore([], {1: 1}, {(1, "gas"): 1}, reference_hash="a" * 64)
    with pytest.raises(FeatureProviderError, match="reference hash mismatch"):
        StateFeatureProvider(state, expected_reference_hash="b" * 64)


def _persist_runtime_state(path, snapshot, *, reference_hash=None, available_from=None, available_to=None):
    meta = get_reference_meta()
    state = SQLiteFeatureStateStore(path)
    objects = [item for item in get_objects_data() if item["has_channels"]]
    channels = get_channels_data()
    state.reference_counts = {item["object_id"]: item["channels_count"] for item in objects}
    state.system_reference_counts = {}
    for channel in channels:
        key = (channel["object_id"], {
            "Газовая охрана": "gas",
            "Охранная подсистема": "security",
            "Диспетчерский контроль": "dispatch",
            "Пожарная охрана": "fire",
            "Температурная подсистема": "temperature",
            "Диагностическая подсистема": "diagnostic",
        }.get(channel["system_type"], channel["system_type"]))
        state.system_reference_counts[key] = state.system_reference_counts.get(key, 0) + 1
    state.available_from = (snapshot - timedelta(hours=168)
                            if available_from is None else available_from)
    state.available_to = snapshot if available_to is None else available_to
    state.reference_snapshot_id = f"reference-v{meta['schema_version']}"
    state.reference_hash = reference_hash or meta["source_sha256"]
    state.persist()


def test_configured_sqlite_runtime_path_builds_provider_and_returns_real_model_score(tmp_path, monkeypatch):
    snapshot = datetime(2026, 6, 30, 20, 59, 59, tzinfo=UTC)
    path = tmp_path / "feature-state.sqlite"
    _persist_runtime_state(path, snapshot)
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(path))

    service = build_risk_inference_service()
    result = service.predict(5122, snapshot)

    assert isinstance(service.provider, StateFeatureProvider)
    assert result["status"] == "available"
    assert isinstance(result["risk_probability"], float)
    assert result["reference_hash"] == get_reference_meta()["source_sha256"]


def test_bounded_demo_source_hydrates_sqlite_and_runs_frozen_model(tmp_path, monkeypatch):
    path = tmp_path / "demo-feature-state.sqlite"
    built = bootstrap_demo_feature_state(path)
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(path))

    service = build_risk_inference_service()
    result = service.predict(5343, datetime(2026, 6, 30, 20, 59, 59, tzinfo=UTC))

    assert built["source_rows"] == 5240
    assert built["hourly_state_rows"] > 0
    assert isinstance(service.provider, StateFeatureProvider)
    assert result["status"] == "available"
    assert result["risk_score_kind"] == "ml"
    assert isinstance(result["risk_probability"], float)
    assert result["source_freshness_ts"] == "2026-06-30T20:59:59+00:00"
    assert len(SQLiteFeatureStateStore(path).raw_observations) == built["event_level_source_rows"]


def test_demo_closed_canonical_hours_match_hourly_only_feature_vectors(tmp_path):
    path = tmp_path / "demo-canonical-parity.sqlite"
    bootstrap_demo_feature_state(path)
    event_aware = SQLiteFeatureStateStore(path)
    hourly_only = InMemoryStateStore(
        event_aware._rows,
        event_aware.reference_counts,
        event_aware.system_reference_counts,
        event_aware.available_from,
        event_aware.available_to,
        event_aware.reference_snapshot_id,
        event_aware.reference_hash,
    )
    event_provider = StateFeatureProvider(event_aware, require_complete_coverage=True)
    hourly_provider = StateFeatureProvider(hourly_only, require_complete_coverage=True)
    canonical_times = [
        datetime(2026, 6, 30, hour, 59, 59, tzinfo=UTC)
        for hour in (2, 8, 14, 20)
    ]

    assert all(is_canonical_snapshot(timestamp) for timestamp in canonical_times)
    for timestamp in canonical_times:
        assert event_provider.snapshot(5343, timestamp) == hourly_provider.snapshot(5343, timestamp)


def test_bounded_demo_runtime_supports_safe_mid_hour_score(tmp_path, monkeypatch):
    path = tmp_path / "demo-open-hour.sqlite"
    built = bootstrap_demo_feature_state(path)
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(path))
    service = build_risk_inference_service()
    as_of = datetime(2026, 6, 30, 20, 30, tzinfo=UTC)

    result = service.predict(5343, as_of)
    features = service.provider.snapshot(5343, as_of)

    assert built["event_level_source_rows"] == 5240
    assert result["status"] == "available"
    assert features["hours_since_last_observation_7d"] is None or features[
        "hours_since_last_observation_7d"
    ] >= 0


def test_configured_legacy_hourly_runtime_fails_closed_for_mid_hour_score(tmp_path, monkeypatch):
    snapshot = datetime(2026, 6, 30, 20, 30, tzinfo=UTC)
    path = tmp_path / "hourly-only-state.sqlite"
    _persist_runtime_state(path, snapshot)
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(path))

    result = build_risk_inference_service().predict(5122, snapshot)

    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "provider_unavailable"
    assert result["risk_probability"] is None


def test_bounded_demo_bootstrap_refuses_existing_database(tmp_path):
    path = tmp_path / "existing.sqlite"
    path.write_bytes(b"preserve existing file")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        bootstrap_demo_feature_state(path)

    assert path.read_bytes() == b"preserve existing file"


@pytest.mark.parametrize("coverage", ["stale", "short_history", "missing_start", "missing_end"])
def test_configured_sqlite_runtime_fails_closed_for_incomplete_source_coverage(
    tmp_path, monkeypatch, coverage
):
    snapshot = datetime(2026, 6, 30, 20, 59, 59, tzinfo=UTC)
    path = tmp_path / "feature-state.sqlite"
    kwargs = {}
    if coverage == "stale":
        kwargs["available_to"] = snapshot - timedelta(hours=1)
    elif coverage == "short_history":
        kwargs["available_from"] = snapshot - timedelta(hours=167)
    _persist_runtime_state(path, snapshot, **kwargs)
    if coverage == "missing_start":
        incomplete = SQLiteFeatureStateStore(path)
        incomplete.available_from = None
        incomplete.persist()
    elif coverage == "missing_end":
        incomplete = SQLiteFeatureStateStore(path)
        incomplete.available_to = None
        incomplete.persist()
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(path))

    result = build_risk_inference_service().predict(5122, snapshot)

    assert result["status"] == "degraded"
    assert result["risk_probability"] is None
    assert result["risk_flag"] is None


def test_current_snapshot_after_historical_source_end_is_degraded(tmp_path, monkeypatch):
    historical_snapshot = datetime(2026, 6, 30, 20, 59, 59, tzinfo=UTC)
    current_snapshot = datetime(2026, 9, 22, 14, 59, 59, tzinfo=UTC)
    path = tmp_path / "feature-state.sqlite"
    _persist_runtime_state(path, historical_snapshot)
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(path))

    result = build_risk_inference_service().predict(5122, current_snapshot)

    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "provider_unavailable"
    assert result["risk_probability"] is None


def test_configured_sqlite_runtime_fails_closed_on_reference_hash_mismatch(tmp_path, monkeypatch):
    snapshot = datetime(2026, 6, 30, 20, 59, 59, tzinfo=UTC)
    path = tmp_path / "feature-state.sqlite"
    _persist_runtime_state(path, snapshot, reference_hash="0" * 64)
    monkeypatch.setenv("TGOAT_FEATURE_STATE_DB", str(path))

    result = build_risk_inference_service().predict(5122, snapshot)

    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "reference_mismatch"
    assert result["risk_probability"] is None


class _ProbabilityModel:
    def __init__(self, probability):
        self.probability = probability

    def predict_proba(self, frame):
        return np.array([[1 - self.probability, self.probability]])


def _service(probability=0.5, provider=None, calls=None):
    calls = calls if calls is not None else []
    model = FrozenRiskModel(_ProbabilityModel(probability), FEATURE_NAMES, 0.3987734643,
                            "risk_baseline_v1_phase1_1_hgb", "risk_baseline_v1_phase1_1")
    def loader():
        calls.append("load")
        return model
    provider = provider or MappingFeatureStore({(1, "2024-01-01T00:00:00+00:00"): [0.0] * 87})
    return RiskInferenceService(provider, loader=loader), calls


def test_inference_service_loads_once_and_applies_frozen_threshold():
    service, calls = _service(0.3987734643)
    result = service.predict(1, datetime(2024, 1, 1, tzinfo=UTC))
    assert result["status"] == "available"
    assert result["risk_flag"] is True
    service.predict(1, datetime(2024, 1, 1, tzinfo=UTC))
    assert calls == ["load"]
    assert service.load_count == 1


def test_inference_service_degrades_without_fabricated_probability():
    provider = MappingFeatureStore({})
    service, _ = _service(provider=provider)
    result = service.predict(1, datetime(2024, 1, 1, tzinfo=UTC))
    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "provider_unavailable"
    assert result["risk_probability"] is None


def test_inference_service_degrades_when_artifact_loader_fails():
    service = RiskInferenceService(MappingFeatureStore({}), loader=lambda: (_ for _ in ()).throw(
        RuntimeError("bad artifact")
    ))
    result = service.predict(1, datetime(2024, 1, 1, tzinfo=UTC))
    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "artifact_integrity_error"
    assert result["risk_probability"] is None


def test_inference_service_reference_mismatch_is_explicit():
    class ReferenceMismatchProvider:
        def snapshot(self, object_id, snapshot_ts):
            raise FeatureProviderError("reference hash mismatch")
    service, _ = _service(provider=ReferenceMismatchProvider())
    result = service.predict(1, datetime(2024, 1, 1, tzinfo=UTC))
    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "reference_mismatch"
    assert result["risk_probability"] is None


def test_service_batch_and_concurrent_calls_are_deterministic():
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    rows = {(object_id, ts.isoformat()): [0.0] * 87 for object_id in range(1, 79)}
    service, calls = _service(0.5, provider=MappingFeatureStore(rows))
    requests = [(object_id, ts) for object_id in range(1, 79)]
    batch = service.batch_predict(requests)
    assert len(batch) == 78
    with ThreadPoolExecutor(max_workers=8) as pool:
        concurrent = list(pool.map(lambda request: service.predict(*request), requests[:20]))
    assert all(item["risk_probability"] == 0.5 for item in batch + concurrent)
    assert calls == ["load"]
    assert service.load_count == 1


def test_fastapi_lifespan_creates_one_service_per_process():
    from fastapi.testclient import TestClient
    with TestClient(app):
        first = app.state.risk_inference_service
        second = app.state.risk_inference_service
        assert first is second
        assert first.load_count == 1


def test_object_risk_endpoint_exposes_degraded_ml_contract():
    from fastapi.testclient import TestClient
    user = auth_provider.authenticate("central", "central-demo")
    with TestClient(app) as api:
        token = auth_provider.issue_token(user)
        response = api.get(
            "/api/v1/objects/5581/risk",
            params={"snapshot_ts": "2024-05-23T11:59:59+03:00"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["degraded_reason"] == "provider_unavailable"
    assert payload["risk_probability"] is None
    assert payload["risk_flag"] is None
    assert "error" not in payload


def test_canonical_snapshot_contract_and_rbac_regression():
    moscow = ZoneInfo("Europe/Moscow")
    for hour in (5, 11, 17, 23):
        assert is_canonical_snapshot(datetime(2024, 5, 23, hour, 59, 59, tzinfo=moscow))
    assert is_canonical_snapshot(datetime(2024, 5, 23, 8, 59, 59, tzinfo=UTC))
    assert not is_canonical_snapshot(datetime(2024, 5, 23, 17, 7, 13, tzinfo=UTC))
    selected = latest_canonical_snapshot(datetime(2024, 5, 23, 9, 0, tzinfo=UTC))
    assert selected.isoformat() == "2024-05-23T08:59:59+00:00"
    from fastapi.testclient import TestClient
    user = auth_provider.authenticate("tech-9", "tech-demo-2")
    with TestClient(app) as api:
        token = auth_provider.issue_token(user)
        forbidden = api.get(
            "/api/v1/objects/5581/risk",
            params={"snapshot_ts": "2024-05-23T11:59:59+03:00"},
            headers={"Authorization": f"Bearer {token}"},
        )
        invalid = api.get(
            "/api/v1/objects/28/risk",
            params={"snapshot_ts": "2024-05-23T20:07:13+03:00"},
            headers={"Authorization": f"Bearer {auth_provider.issue_token(auth_provider.authenticate('central', 'central-demo'))}"},
        )
    assert forbidden.status_code == 404
    assert invalid.status_code == 422


def test_golden_adapter_preserves_all_87_values_and_order():
    payload = json.loads((__import__("pathlib").Path(__file__).parents[0] / "fixtures" /
                          "risk_baseline_v1_golden.json").read_text())
    item = payload["items"][0]
    ts = item["snapshot_ts"]
    values = MappingFeatureStore({(item["object_id"], as_utc(datetime.fromisoformat(ts)).isoformat()): item["features"]}).snapshot(
        item["object_id"], datetime.fromisoformat(ts)
    )
    assert tuple(values) == tuple(payload["feature_names"])
    assert len(values) == 87
    assert list(values.values()) == item["features"]
