"""Normalize supplied 2026 PPR/maintenance workbooks without fuzzy object mapping."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.planned_work import FIELDS, normalize_record, parse_source_deadline
from backend.app.services.reference_data import get_objects_data


SOURCE_DIR = ROOT / "backend/data/planned_work_sources"
DEFAULT_OUTPUT = ROOT / "backend/data/planned_work.normalized.csv"
MONTHS = ("янв.", "февр.", "март", "апр.", "май", "июн.",
          "июл.", "авг.", "сен.", "окт.", "ноя.", "дек.")
MONTH_NUMBERS = {name: index for index, name in enumerate(
    ("январь", "февраль", "март", "апрель", "май", "июнь",
     "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"), start=1)}
PHASES = (
    (5, "Демонтаж датчиков метана"),
    (6, "Передача датчиков на ППР и поверку"),
    (7, "Возврат датчиков из обслуживания"),
    (8, "Сдача работ комиссии"),
)


def _date_only(value) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", str(value or ""))
    if match:
        day, month, year = map(int, match.groups())
        return date(year, month, day).isoformat()
    return None


def normalize_ppr(path: Path, objects: list[dict]) -> list[dict]:
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book.active
    output = []
    for row_no, row in enumerate(
        sheet.iter_rows(min_row=10, min_col=2, max_col=8, values_only=True), start=10
    ):
        raw_name = row[1]
        if not raw_name or not str(raw_name).strip().casefold().startswith("объект"):
            continue
        count = row[2]
        source_month = str(row[0] or "").strip()
        for column, phase in PHASES:
            raw_value = row[column - 2]
            day = _date_only(raw_value)
            if day is None:
                continue
            deadline = parse_source_deadline(raw_value)
            source_month_number = MONTH_NUMBERS.get(source_month.casefold())
            month_note = (f"source_month={source_month}; "
                          if source_month_number is not None and int(day[5:7]) != source_month_number
                          else "")
            note_parts = [part for part in (month_note.strip(), f"source_cell={raw_value}" if not isinstance(raw_value, (date, datetime)) else None) if part]
            output.append(normalize_record({
                "planned_work_id": f"ppr-2026:r{row_no}:c{column}",
                "object_name_raw": str(raw_name),
                "equipment_context": f"Датчики метана; {count} шт." if count is not None else "Датчики метана",
                "work_type": phase, "planned_start": day, "planned_end": day,
                "temporal_constraint_kind": "deadline" if deadline else "none",
                "constraint_at": deadline[0] if deadline else None,
                "source_time_text": deadline[1] if deadline else None,
                "notes": "; ".join(note_parts) or None,
            }, source_file=path.name, source_row=row_no, objects=objects))
    book.close()
    return output


def normalize_monthly_schedule(path: Path, objects: list[dict]) -> list[dict]:
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book.active
    output = []
    object_name = None
    object_number = None
    for row_no, row in enumerate(
        sheet.iter_rows(min_row=6, min_col=2, max_col=18, values_only=True), start=6
    ):
        marker = str(row[1] or "").strip()
        number = row[0]
        if isinstance(number, int) and (marker.casefold() == "объект" or marker.casefold().startswith("к-р")):
            object_number = number
            object_name = marker if marker.casefold().startswith("к-р") else f"Объект {number}"
            continue
        equipment = str(row[2] or "").strip()
        if not object_name or not equipment or equipment.casefold() == "марка":
            continue
        quantity, unit = row[3], row[4]
        detail = equipment
        if quantity is not None:
            detail += f"; количество: {quantity} {unit or ''}".rstrip()
        for offset, work_type in enumerate(row[5:], start=7):
            if work_type not in {"ТО", "ТО+ТР", "ТР"}:
                continue
            output.append(normalize_record({
                "planned_work_id": f"to-2026:object-{object_number}:r{row_no}:{offset}",
                "object_name_raw": object_name,
                "equipment_context": detail,
                "work_type": str(work_type),
                "planned_start": f"2026-{offset - 6:02d}",
                "planned_end": f"2026-{offset - 6:02d}",
                "notes": f"source_sheet={sheet.title}; visible heading says 2026",
            }, source_file=path.name, source_row=row_no, objects=objects))
    book.close()
    return output


def normalize_sources(source_dir: Path = SOURCE_DIR) -> list[dict]:
    objects = get_objects_data()
    ppr = source_dir / "График ППР АКМ на 2026г. РЭК.xlsx"
    monthly = source_dir / "График_ТО_АКМ_и_ДУ_на_2026г_РЭК_3_на_А4.xlsx"
    return [*normalize_ppr(ppr, objects), *normalize_monthly_schedule(monthly, objects)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows = normalize_sources(args.source_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    mapped = sum(row["object_id"] is not None for row in rows)
    print(f"Normalized {len(rows)} records; mapped={mapped}; unmapped={len(rows) - mapped}; output={args.output}")


if __name__ == "__main__":
    main()
