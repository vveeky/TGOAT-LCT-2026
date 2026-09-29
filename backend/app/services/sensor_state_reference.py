"""Fail-closed lookup for the organizer's sensor-type state dictionary.

This reference is auxiliary metadata. It never replaces the event's source
``тревожное`` value used by the frozen target and feature contract.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "sensor_states.csv"
EXPECTED_COLUMNS = (
    "тип_датчика",
    "ид_набор_состояний",
    "название_состояния",
    "тревожное",
)


class SensorStateReferenceUnavailableError(RuntimeError):
    """The state reference is absent or malformed."""


@dataclass(frozen=True)
class SensorStateRow:
    sensor_type: str
    state_set_id: int
    state_name: str
    is_alarm: bool


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


@lru_cache(maxsize=4)
def _read_rows(path_text: str, size: int, mtime_ns: int) -> tuple[SensorStateRow, ...]:
    del size, mtime_ns  # Included in the cache key so changed files reload.
    path = Path(path_text)
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or set(reader.fieldnames) != set(EXPECTED_COLUMNS):
                raise SensorStateReferenceUnavailableError("invalid sensor state reference columns")
            rows: list[SensorStateRow] = []
            for line_number, row in enumerate(reader, start=2):
                sensor_type = (row.get("тип_датчика") or "").strip()
                state_name = (row.get("название_состояния") or "").strip()
                raw_set_id = (row.get("ид_набор_состояний") or "").strip()
                raw_alarm = (row.get("тревожное") or "").strip().casefold()
                if not sensor_type or not state_name:
                    raise SensorStateReferenceUnavailableError(
                        f"empty state key at CSV line {line_number}"
                    )
                try:
                    state_set_id = int(raw_set_id)
                except ValueError as error:
                    raise SensorStateReferenceUnavailableError(
                        f"invalid state-set ID at CSV line {line_number}"
                    ) from error
                if raw_alarm not in {"true", "false"}:
                    raise SensorStateReferenceUnavailableError(
                        f"invalid alarm flag at CSV line {line_number}"
                    )
                rows.append(SensorStateRow(
                    sensor_type=sensor_type,
                    state_set_id=state_set_id,
                    state_name=state_name,
                    is_alarm=raw_alarm == "true",
                ))
    except SensorStateReferenceUnavailableError:
        raise
    except OSError as error:
        raise SensorStateReferenceUnavailableError(
            f"cannot read sensor state reference: {error}"
        ) from error
    if not rows:
        raise SensorStateReferenceUnavailableError("sensor state reference is empty")
    return tuple(rows)


def load_sensor_state_rows(path: Path = DATA_FILE) -> tuple[SensorStateRow, ...]:
    """Load all rows, preserving duplicates so conflicts remain detectable."""
    try:
        stat = path.stat()
    except OSError as error:
        raise SensorStateReferenceUnavailableError(
            f"cannot stat sensor state reference: {error}"
        ) from error
    return _read_rows(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def resolve_sensor_state(
    sensor_type: str | None,
    state_name: str | None,
    state_set_id: int | str | None = None,
    *,
    path: Path = DATA_FILE,
) -> dict[str, object]:
    """Resolve by sensor type + exact state name, never by channel ID.

    Identical duplicate rows do not multiply results. A key with conflicting
    alarm flags is ambiguous, even when a state-set ID is supplied.
    """
    if not _key(sensor_type) or not _key(state_name):
        return {
            "status": "unknown",
            "is_alarm": None,
            "state_set_ids": [],
            "state_set_ambiguous": False,
            "row_count": 0,
            "reason": "missing_lookup_key",
        }

    requested_set: int | None = None
    if state_set_id is not None:
        try:
            requested_set = int(state_set_id)
        except (TypeError, ValueError):
            return {
                "status": "unknown",
                "is_alarm": None,
                "state_set_ids": [],
                "state_set_ambiguous": False,
                "row_count": 0,
                "reason": "invalid_state_set_id",
            }

    rows = [
        row for row in load_sensor_state_rows(path)
        if _key(row.sensor_type) == _key(sensor_type)
        and _key(row.state_name) == _key(state_name)
        and (requested_set is None or row.state_set_id == requested_set)
    ]
    state_set_ids = sorted({row.state_set_id for row in rows})
    if not rows:
        return {
            "status": "unknown",
            "is_alarm": None,
            "state_set_ids": [],
            "state_set_ambiguous": False,
            "row_count": 0,
            "reason": "no_matching_state",
        }

    if requested_set is None and len(state_set_ids) > 1:
        return {
            "status": "ambiguous",
            "is_alarm": None,
            "state_set_ids": state_set_ids,
            "state_set_ambiguous": True,
            "row_count": len(rows),
            "reason": "state_set_id_required",
        }

    alarm_values = {row.is_alarm for row in rows}
    if len(alarm_values) != 1:
        return {
            "status": "ambiguous",
            "is_alarm": None,
            "state_set_ids": state_set_ids,
            "state_set_ambiguous": len(state_set_ids) > 1,
            "row_count": len(rows),
            "reason": "conflicting_alarm_flags",
        }

    return {
        "status": "resolved",
        "is_alarm": alarm_values.pop(),
        "state_set_ids": state_set_ids,
        "state_set_ambiguous": requested_set is None and len(state_set_ids) > 1,
        "row_count": len(rows),
        "reason": None,
    }
