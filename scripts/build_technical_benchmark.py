"""Build a small, deterministic technical benchmark from annual source CSVs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "ml/data/raw/annual"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "ml/data/interim/technical_benchmark"
SELECTION_RULE = "first-N-valid-date-rows-per-calendar-month-in-source-order"
REQUIRED_COLUMNS = {"ид_события", "ид_канала_данных", "дата", "время", "тревожное", "значение_датчика"}


def _year_from_path(path: Path) -> int | None:
    match = re.fullmatch(r"ext-journal-(\d{4})\.csv", path.name)
    return int(match.group(1)) if match else None


def _date_parts(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        return parsed.year, parsed.month
    except (ValueError, TypeError):
        return None


def _row_timestamp(row: dict[str, str | None]) -> str:
    date, time = row.get("дата") or "", row.get("время") or ""
    return f"{date}T{time}"


def _sample_source(path: Path, year: int, rows_per_month: int,
                   selected_month: int | None, max_source_rows: int | None) -> dict:
    size = path.stat()
    selected: dict[int, list[tuple[int, dict[str, str | None]]]] = {}
    invalid_date_rows = 0
    scanned_rows = 0
    truncated = False
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
            raise ValueError(f"{path.name}: missing required columns: {missing}")
        for row_number, row in enumerate(reader, start=2):
            if max_source_rows is not None and scanned_rows >= max_source_rows:
                truncated = True
                break
            scanned_rows += 1
            parsed = _date_parts(row.get("дата"))
            if parsed is None:
                invalid_date_rows += 1
                continue
            row_year, month = parsed
            if row_year != year or (selected_month is not None and month != selected_month):
                continue
            month_rows = selected.setdefault(month, [])
            if len(month_rows) < rows_per_month:
                month_rows.append((row_number, row))

    return {
        "year": year,
        "source_file": path.name,
        "source_size_bytes": size.st_size,
        "source_mtime_ns": size.st_mtime_ns,
        "rows_scanned": scanned_rows,
        "invalid_date_rows_skipped": invalid_date_rows,
        "truncated_by_row_limit": truncated,
        "header": reader.fieldnames,
        "months": selected,
    }


def build_benchmark(input_dir: Path, output_dir: Path, *, rows_per_month: int = 100,
                    year: int | None = None, month: int | None = None,
                    max_source_rows: int | None = None, overwrite: bool = False) -> dict:
    if rows_per_month < 1:
        raise ValueError("rows_per_month must be positive")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    if max_source_rows is not None and max_source_rows < 1:
        raise ValueError("max_source_rows must be positive")

    sources = sorted((path for path in input_dir.glob("ext-journal-*.csv")
                      if _year_from_path(path) is not None), key=lambda p: _year_from_path(p))
    if year is not None:
        sources = [path for path in sources if _year_from_path(path) == year]
        if not sources:
            raise FileNotFoundError(f"No annual source file for year {year} in {input_dir}")
    if not sources:
        raise FileNotFoundError(f"No ext-journal-YYYY.csv files in {input_dir}")

    sampled = [
        _sample_source(path, _year_from_path(path), rows_per_month, month, max_source_rows)
        for path in sources
    ]
    manifest = {
        "benchmark_version": 1,
        "purpose": "technical regression and smoke tests only; not an ML quality holdout",
        "selection_rule": SELECTION_RULE,
        "rows_per_month_limit": rows_per_month,
        "year_filter": year,
        "month_filter": month,
        "max_source_rows_per_file": max_source_rows,
        "sources": [],
    }

    outputs: list[tuple[Path, bytes]] = []
    for source in sampled:
        output_source = {key: value for key, value in source.items() if key != "months"}
        output_source["samples"] = []
        for sample_month, rows in sorted(source["months"].items()):
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=source["header"], lineterminator="\n")
            writer.writeheader()
            for _, row in rows:
                writer.writerow(row)
            content = buffer.getvalue().encode("utf-8")
            filename = f"events_{source['year']}_{sample_month:02}.csv"
            outputs.append((output_dir / filename, content))
            output_source["samples"].append({
                "month": sample_month,
                "file": filename,
                "rows": len(rows),
                "source_row_start": rows[0][0],
                "source_row_end": rows[-1][0],
                "timestamp_start": _row_timestamp(rows[0][1]),
                "timestamp_end": _row_timestamp(rows[-1][1]),
                "sha256": hashlib.sha256(content).hexdigest(),
            })
        manifest["sources"].append(output_source)

    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    outputs.append((output_dir / "manifest.json", manifest_bytes))
    existing = [path for path, _ in outputs if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"Output exists; use --overwrite to replace generated files: {existing[0]}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, content in outputs:
        path.write_bytes(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--rows-per-month", type=int, default=100)
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--max-source-rows", type=int,
                        help="stop after this many CSV data rows per file (for bounded dry runs)")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace benchmark files with the same generated names")
    args = parser.parse_args()
    try:
        manifest = build_benchmark(
            args.input_dir, args.output_dir, rows_per_month=args.rows_per_month,
            year=args.year, month=args.month, max_source_rows=args.max_source_rows,
            overwrite=args.overwrite,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as error:
        parser.error(str(error))
    summary = [{"year": item["year"], "rows_scanned": item["rows_scanned"],
                "truncated": item["truncated_by_row_limit"],
                "samples": [{"month": sample["month"], "rows": sample["rows"]}
                            for sample in item["samples"]]}
               for item in manifest["sources"]]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
