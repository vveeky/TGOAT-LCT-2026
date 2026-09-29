import json
from pathlib import Path

import pytest

from backend.app.services.sensor_state_reference import resolve_sensor_state
from ml.features.staged import ORGANIZER_STATE_REFERENCE_CONTRACT


def test_phase1_manifest_and_rebuild_metadata_preserve_organizer_join_contract():
    manifest_path = Path(__file__).resolve().parents[1] / "ml/artifacts/risk_baseline_v1/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["organizer_state_reference_contract"] == ORGANIZER_STATE_REFERENCE_CONTRACT


@pytest.mark.parametrize(
    ("state_name", "state_set_id", "expected_alarm"),
    [
        ("Дыма нет", 5, False),
        ("Обнаружен дым", 5, True),
        ("Движения нет", 6, False),
        ("Обнаружено движение", 6, True),
        ("Движение вверх", 6, True),
        ("Движение вниз", 6, True),
        ("Движение влево", 6, True),
        ("Движение вправо", 6, True),
    ],
)
def test_organizer_confirmed_kd_av_states_resolve_by_sensor_type(
    state_name, state_set_id, expected_alarm
):
    result = resolve_sensor_state("КД АВ", state_name, state_set_id)

    assert result["status"] == "resolved"
    assert result["is_alarm"] is expected_alarm
    assert result["state_set_ids"] == [state_set_id]
    assert result["row_count"] == 1


def test_same_state_across_sets_requires_state_set_id_without_row_multiplication():
    result = resolve_sensor_state("КД Дверь", "Норма")

    assert result == {
        "status": "ambiguous",
        "is_alarm": None,
        "state_set_ids": [1, 2],
        "state_set_ambiguous": True,
        "row_count": 2,
        "reason": "state_set_id_required",
    }

    filtered = resolve_sensor_state("КД Дверь", "Норма", 1)
    assert filtered["status"] == "resolved"
    assert filtered["is_alarm"] is False
    assert filtered["row_count"] == 1


@pytest.mark.parametrize("state_set_id", [None, 13])
def test_conflicting_gas_state_is_unknown_even_with_state_set_id(state_set_id):
    result = resolve_sensor_state(
        "Газовый датчик", "Температура ниже 3ºC1", state_set_id
    )

    assert result["status"] == "ambiguous"
    assert result["is_alarm"] is None
    assert result["state_set_ids"] == [13]
    assert result["row_count"] == 3
    assert result["reason"] == "conflicting_alarm_flags"


def test_identical_duplicate_rows_resolve_once_without_multiplying_records():
    result = resolve_sensor_state(
        "Газовый датчик", "Температура выше 35ºC1", 13
    )

    assert result["status"] == "resolved"
    assert result["is_alarm"] is True
    assert result["row_count"] == 2


def test_lookup_is_exact_on_sensor_type_and_state_name_but_ignores_case_and_edges():
    result = resolve_sensor_state("  кд ав ", "  ОБНАРУЖЕН ДЫМ  ")

    assert result["status"] == "resolved"
    assert result["is_alarm"] is True


@pytest.mark.parametrize(
    ("sensor_type", "state_name", "state_set_id", "reason"),
    [
        (None, "Норма", None, "missing_lookup_key"),
        ("КД АВ", None, None, "missing_lookup_key"),
        ("Несуществующий тип", "Норма", None, "no_matching_state"),
        ("КД АВ", "Неизвестное состояние", None, "no_matching_state"),
        ("КД АВ", "Дыма нет", "not-an-id", "invalid_state_set_id"),
    ],
)
def test_missing_or_invalid_keys_fail_closed(sensor_type, state_name, state_set_id, reason):
    result = resolve_sensor_state(sensor_type, state_name, state_set_id)

    assert result["status"] == "unknown"
    assert result["is_alarm"] is None
    assert result["reason"] == reason
