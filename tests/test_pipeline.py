import json
import sys

import polars as pl
import pytest

from scripts.normalization import (
    canonicalize_events,
    metadata_path,
    normalize_file,
    output_is_current,
    validate_parquet,
)
from scripts import audit_processed, normalize_sample, validate_sample


@pytest.fixture
def pipeline_files(tmp_path):
    channels = tmp_path / "channels.csv"
    objects = tmp_path / "objects.csv"
    events = tmp_path / "events.csv"
    channels.write_text(
        "ид_канала_данных,тип_инж_системы,тип_датчика,тег_инженерной_системы,название_датчика,ид_объект\n"
        "2866,security,motion,tag-1,sensor,28\n", encoding="utf-8")
    objects.write_text(
        "ид_объект,иерархия_уровень,родитель,вид_объекта,диспетчерское_название_объекта\n"
        "28,3,9,guardObject,object\n", encoding="utf-8")
    events.write_text(
        "ид_события,ид_канала_данных,дата,время,тревожное,значение_датчика\n"
        "1,2866,2025-01-01,00:00:00,t,on\n"
        "ид_события,ид_канала_данных,дата,время,тревожное,значение_датчика\n"
        "2,2866,2025-01-01,01:00:00,UNKNOWN,off\n", encoding="utf-8")
    return events, channels, objects, tmp_path / "events_2025.parquet"


def test_pipeline_repeated_header_invalid_alarm_and_manifest(pipeline_files):
    events, channels, objects, output = pipeline_files
    metadata = normalize_file(events, output, 2025, channels, objects)
    frame = pl.read_parquet(output)
    assert frame.height == 2
    assert frame["invalid_alarm"].to_list() == [False, True]
    assert frame["is_alarm"].to_list() == [True, None]
    assert metadata["invalid_alarm_rows"] == 1
    assert json.loads(metadata_path(output).read_text())["pipeline_version"] == "3"
    assert metadata["partial"] is False and metadata["limit"] is None


def test_canonicalize_events_accepts_reusable_lazy_input(pipeline_files):
    _, channels, objects, _ = pipeline_files
    events = pl.LazyFrame({
        "ид_события": ["1", None, "1", "ид_события"],
        "ид_канала_данных": ["2866", "2866", "2866", "ид_канала_данных"],
        "дата": ["2025-01-01", "2025-01-01", "2025-01-01", "дата"],
        "время": ["00:00:00", "00:00:01", "00:00:02", "время"],
        "тревожное": ["unknown", "f", "t", "тревожное"],
        "значение_датчика": ["raw", "null-id", "duplicate", "значение_датчика"],
    })
    result = canonicalize_events(events, 2025, channels, objects).collect()
    assert result["event_id_raw"].to_list() == ["1", None, "1"]
    assert result["sensor_value"].to_list() == ["raw", "null-id", "duplicate"]
    assert result["invalid_event_id"].to_list() == [False, True, False]
    assert result["invalid_alarm"].to_list() == [True, False, False]


def test_pipeline_wrong_year_is_rejected_atomically(pipeline_files):
    events, channels, objects, output = pipeline_files
    output.write_bytes(b"existing-final")
    with pytest.raises(ValueError, match="timestamps do not match"):
        normalize_file(events, output, 2024, channels, objects)
    assert output.read_bytes() == b"existing-final"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_stale_source_and_reference_are_detected(pipeline_files):
    events, channels, objects, output = pipeline_files
    normalize_file(events, output, 2025, channels, objects)
    assert output_is_current(events, output, 2025, channels, objects) == (True, "current")
    events.write_text(events.read_text() + "3,2866,2025-01-01,02:00:00,f,off\n")
    current, reason = output_is_current(events, output, 2025, channels, objects)
    assert current is False and reason == "stale source"

    normalize_file(events, output, 2025, channels, objects)
    channels.write_text(channels.read_text() + "2867,security,motion,tag-2,sensor2,28\n")
    current, reason = output_is_current(events, output, 2025, channels, objects)
    assert current is False and reason == "stale reference_sha256"


def test_corrupted_parquet_is_not_current(pipeline_files):
    events, channels, objects, output = pipeline_files
    normalize_file(events, output, 2025, channels, objects)
    output.write_bytes(b"corrupt")
    current, reason = output_is_current(events, output, 2025, channels, objects)
    assert current is False and reason.startswith("invalid output")


def test_partial_and_truncated_outputs_are_not_current(pipeline_files):
    events, channels, objects, output = pipeline_files
    metadata = normalize_file(events, output, 2025, channels, objects, limit=1)
    assert metadata["partial"] is True
    assert output_is_current(events, output, 2025, channels, objects) == (False, "partial output")
    normalize_file(events, output, 2025, channels, objects)
    pl.read_parquet(output).head(1).write_parquet(output)
    assert output_is_current(events, output, 2025, channels, objects) == (
        False, "row count does not match metadata"
    )


@pytest.mark.parametrize("column,dtype", [("channel_id", pl.String), ("is_alarm", pl.String)])
def test_validate_parquet_rejects_wrong_dtypes(pipeline_files, column, dtype):
    events, channels, objects, output = pipeline_files
    normalize_file(events, output, 2025, channels, objects)
    pl.read_parquet(output).with_columns(pl.col(column).cast(dtype)).write_parquet(output)
    with pytest.raises(ValueError, match="wrong dtypes"):
        validate_parquet(output, 2025)


def test_alarm_normalization_including_null(tmp_path, pipeline_files):
    _, channels, objects, output = pipeline_files
    events = tmp_path / "alarms.csv"
    values = ["t", "f", "true", "false", "TRUE", "FALSE", "", "unknown"]
    rows = "".join(f"{i},2866,2025-01-01,00:00:{i:02d},{value},x\n"
                   for i, value in enumerate(values, 1))
    events.write_text(
        "ид_события,ид_канала_данных,дата,время,тревожное,значение_датчика\n" + rows,
        encoding="utf-8",
    )
    normalize_file(events, output, 2025, channels, objects)
    frame = pl.read_parquet(output)
    assert frame["is_alarm"].to_list() == [True, False, True, False, True, False, None, None]
    assert frame["invalid_alarm"].to_list() == [False] * 6 + [True, True]


@pytest.mark.parametrize("reference", ["channels", "objects"])
def test_normalization_rejects_duplicate_reference_ids(pipeline_files, reference):
    events, channels, objects, output = pipeline_files
    path = channels if reference == "channels" else objects
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines + [lines[-1]]) + "\n")
    with pytest.raises(ValueError, match=f"Duplicate .*_id in {reference} reference"):
        normalize_file(events, output, 2025, channels, objects)


def test_validate_parquet_checks_schema(tmp_path):
    path = tmp_path / "bad.parquet"
    pl.DataFrame({"source_year": [2025]}).write_parquet(path)
    with pytest.raises(ValueError, match="schema is missing"):
        validate_parquet(path, 2025)


def test_validate_parquet_rejects_extra_columns(pipeline_files):
    events, channels, objects, output = pipeline_files
    normalize_file(events, output, 2025, channels, objects)
    pl.read_parquet(output).with_columns(pl.lit("unexpected").alias("extra_column")).write_parquet(output)
    with pytest.raises(ValueError, match="unexpected columns"):
        validate_parquet(output, 2025)


def test_sample_cli_requires_explicit_year(monkeypatch):
    for module in (normalize_sample, validate_sample):
        monkeypatch.setattr(sys, "argv", [module.__name__])
        with pytest.raises(SystemExit) as error:
            module.parse_args()
        assert error.value.code == 2


def test_audit_requires_full_current_set_unless_explicitly_partial(pipeline_files, monkeypatch):
    events, channels, objects, output = pipeline_files
    normalize_file(events, output, 2025, channels, objects)
    monkeypatch.setattr(audit_processed, "EXPECTED_YEARS", [2024, 2025])
    raw_dir = events.parent / "raw"
    data_dir = events.parent / "processed"
    raw_dir.mkdir(); data_dir.mkdir()
    annual = raw_dir / "ext-journal-2025.csv"
    annual.write_bytes(events.read_bytes())
    target = data_dir / "events_2025.parquet"
    normalize_file(annual, target, 2025, channels, objects)
    with pytest.raises(ValueError, match="Missing required years"):
        audit_processed.run_audit(data_dir, raw_dir, channels, objects, allow_partial=False)
    summary, missing = audit_processed.run_audit(
        data_dir, raw_dir, channels, objects, allow_partial=True
    )
    assert missing == [2024] and summary["rows"].sum() == 2
    target.with_suffix(target.suffix + ".meta.json").unlink()
    with pytest.raises(ValueError, match="Stale or invalid"):
        audit_processed.run_audit(data_dir, raw_dir, channels, objects, allow_partial=True)
