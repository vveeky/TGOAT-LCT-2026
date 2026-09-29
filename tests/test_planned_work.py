import csv
from datetime import datetime, timezone

import pytest

from backend.app.services.planned_work import (
    FIELDS, _bounds, normalize_record, parse_source_deadline, query_planned_work,
)


OBJECTS = [
    {"object_id": 28, "object_name": "Коллектор Северный"},
    {"object_id": 29, "object_name": "Коллектор Северный"},
]


def record(**updates):
    return {
        "planned_work_id": "work-1", "object_name_raw": "Коллектор Северный",
        "equipment_context": "газовый датчик", "work_type": "ТО",
        "planned_start": "2026-05-06", "planned_end": "2026-05-06", "notes": "",
        **updates,
    }


def test_exact_id_mapping_and_date_only_precision_are_preserved():
    item = normalize_record(record(object_id="28"), source_file="ppr.csv", source_row=2,
                            objects=OBJECTS)
    assert item["object_id"] == 28
    assert item["mapping_method"] == "source_object_id"
    assert item["mapping_confidence"] == "exact"
    assert item["planned_start"] == "2026-05-06"
    assert item["date_precision"] == "date_only"


def test_ambiguous_exact_name_is_not_mapped():
    item = normalize_record(record(), source_file="ppr.csv", source_row=2, objects=OBJECTS)
    assert item["object_id"] is None
    assert item["mapping_method"] == "ambiguous_exact_name"
    assert item["mapping_confidence"] == "ambiguous"


def test_timestamp_precision_and_explicit_timezone_are_preserved():
    item = normalize_record(record(object_id="28", planned_start="2026-05-06T09:15:00+03:00",
                                   planned_end="2026-05-06T10:15:00+03:00"),
                            source_file="ppr.csv", source_row=2, objects=OBJECTS)
    assert item["date_precision"] == "timestamp"
    assert item["planned_start"].endswith("+03:00")


def test_ppr_deadline_keeps_source_text_and_moscow_timestamp():
    constraint = parse_source_deadline("до 900 13.01.2026")
    assert constraint == ("2026-01-13T09:00:00+03:00", "до 900 13.01.2026")
    item = normalize_record(record(
        object_id="28", planned_start="2026-01-13", planned_end="2026-01-13",
        temporal_constraint_kind="deadline", constraint_at=constraint[0],
        source_time_text=constraint[1]), source_file="ppr.xlsx", source_row=10,
        objects=OBJECTS)
    assert item["date_precision"] == "date_only"
    assert item["temporal_constraint_kind"] == "deadline"
    assert item["constraint_at"] == "2026-01-13T09:00:00+03:00"
    assert item["source_time_text"] == "до 900 13.01.2026"


def test_future_work_is_not_active_and_date_only_is_active_for_whole_local_day(tmp_path):
    path = tmp_path / "canonical.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({"planned_work_id": "future", "source_file": "ppr.xlsx",
                         "source_row": 7, "object_id": 28, "object_name_raw": "North",
                         "equipment_context": "gas", "work_type": "testing",
                         "planned_start": "2026-05-07", "planned_end": "2026-05-07",
                         "date_precision": "date_only", "mapping_method": "source_object_id",
                         "mapping_confidence": "exact", "notes": ""})
    future_query = query_planned_work(28, datetime(2026, 5, 6, 12, tzinfo=timezone.utc), path=path)
    assert future_query["active"] == []
    assert [row["planned_work_id"] for row in future_query["upcoming"]] == ["future"]
    active_query = query_planned_work(28, datetime(2026, 5, 7, 12, tzinfo=timezone.utc), path=path)
    assert [row["planned_work_id"] for row in active_query["active"]] == ["future"]


def test_read_canonical_preserves_original_source_row_provenance(tmp_path):
    path = tmp_path / "canonical.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({"planned_work_id": "source-row-check", "source_file": "ppr.xlsx",
                         "source_row": 123, "object_id": 28,
                         "object_name_raw": "Коллектор Северный", "equipment_context": "gas",
                         "work_type": "testing", "planned_start": "2026-05-07",
                         "planned_end": "2026-05-07", "date_precision": "date_only",
                         "mapping_method": "source_object_id", "mapping_confidence": "exact"})

    result = query_planned_work(
        28, datetime(2026, 5, 6, 12, tzinfo=timezone.utc), path=path,
    )

    assert result["upcoming"][0]["source_row"] == 123


def test_deadline_is_never_full_day_active_and_moves_after_deadline(tmp_path):
    path = tmp_path / "canonical.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({"planned_work_id": "deadline", "source_file": "ppr.xlsx",
                         "source_row": 7, "object_id": 28, "object_name_raw": "North",
                         "equipment_context": "gas", "work_type": "Передача датчиков",
                         "planned_start": "2026-05-07", "planned_end": "2026-05-07",
                         "date_precision": "date_only", "temporal_constraint_kind": "deadline",
                         "constraint_at": "2026-05-07T09:00:00+03:00",
                         "source_time_text": "до 900 07.05.2026",
                         "mapping_method": "source_object_id", "mapping_confidence": "exact",
                         "notes": ""})
    before = query_planned_work(28, datetime(2026, 5, 7, 5, 59, tzinfo=timezone.utc), path=path)
    assert before["active"] == []
    assert before["upcoming"][0]["deadline_at"] == "2026-05-07T09:00:00+03:00"
    after = query_planned_work(28, datetime(2026, 5, 7, 6, 1, tzinfo=timezone.utc), path=path)
    assert after["active"] == after["upcoming"] == []
    assert after["recent"][0]["planned_work_id"] == "deadline"


def test_object_id_outside_reference_is_rejected():
    with pytest.raises(ValueError, match="not in the project registry"):
        normalize_record(record(object_id="999999"), source_file="ppr.csv", source_row=2,
                         objects=OBJECTS)


def test_month_precision_covers_the_whole_calendar_month_without_inventing_a_day():
    item = normalize_record(record(object_id="28", planned_start="2026-12",
                                   planned_end="2026-12"),
                            source_file="to.xlsx", source_row=12, objects=OBJECTS)
    assert item["date_precision"] == "month_only"
    start, end = _bounds(item)
    assert start.isoformat() == "2026-12-01T00:00:00+03:00"
    assert end.isoformat() == "2027-01-01T00:00:00+03:00"


def test_source_deadline_normalization_is_deterministic():
    source_fields = {
        "temporal_constraint_kind": "deadline",
        "constraint_at": "2026-01-13T09:00:00+03:00",
        "source_time_text": "до 900 13.01.2026",
    }
    first = normalize_record(record(object_id="28", planned_start="2026-01-13",
                                    planned_end="2026-01-13", **source_fields),
                             source_file="ppr.xlsx", source_row=10, objects=OBJECTS)
    second = normalize_record(record(object_id="28", planned_start="2026-01-13",
                                     planned_end="2026-01-13", **source_fields),
                              source_file="ppr.xlsx", source_row=10, objects=OBJECTS)
    assert first == second
