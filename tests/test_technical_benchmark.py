import csv
import json

import pytest

from scripts.build_technical_benchmark import build_benchmark


HEADER = ["ид_события", "ид_канала_данных", "дата", "время", "тревожное", "значение_датчика"]


def _source(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows([
            ["1", "10", "2024-01-01", "00:00:00", "f", "a"],
            ["2", "10", "2024-01-01", "00:00:01", "t", "b"],
            ["3", "10", "2024-01-02", "00:00:00", "f", "c"],
            ["4", "10", "2024-02-01", "00:00:00", "f", "d"],
        ])
    return path


def test_benchmark_selection_manifest_and_output_are_deterministic(tmp_path):
    source_dir = tmp_path / "raw"
    _source(source_dir / "ext-journal-2024.csv")
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first = build_benchmark(source_dir, first_dir, rows_per_month=2)
    second = build_benchmark(source_dir, second_dir, rows_per_month=2)
    samples = first["sources"][0]["samples"]
    assert [(item["month"], item["rows"]) for item in samples] == [(1, 2), (2, 1)]
    assert samples[0]["source_row_start"] == 2 and samples[0]["source_row_end"] == 3
    assert first == second
    assert sorted(path.name for path in first_dir.iterdir()) == sorted(path.name for path in second_dir.iterdir())
    assert all((first_dir / path.name).read_bytes() == (second_dir / path.name).read_bytes()
               for path in first_dir.iterdir())
    with (first_dir / "events_2024_01.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["ид_события"] for row in rows] == ["1", "2"]
    persisted = json.loads((first_dir / "manifest.json").read_text(encoding="utf-8"))
    assert persisted["purpose"].startswith("technical regression")
    assert persisted["sources"][0]["samples"][0]["sha256"]


def test_benchmark_source_row_cap_is_explicitly_recorded(tmp_path):
    source_dir = tmp_path / "raw"
    _source(source_dir / "ext-journal-2024.csv")
    manifest = build_benchmark(source_dir, tmp_path / "limited", rows_per_month=5,
                                max_source_rows=2)
    source = manifest["sources"][0]
    assert source["rows_scanned"] == 2
    assert source["truncated_by_row_limit"] is True
    assert [(sample["month"], sample["rows"]) for sample in source["samples"]] == [(1, 2)]


def test_benchmark_does_not_overwrite_without_explicit_flag(tmp_path):
    source_dir = tmp_path / "raw"
    _source(source_dir / "ext-journal-2024.csv")
    output_dir = tmp_path / "benchmark"
    build_benchmark(source_dir, output_dir)
    with pytest.raises(FileExistsError, match="--overwrite"):
        build_benchmark(source_dir, output_dir)
