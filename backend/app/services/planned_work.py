"""Read-only, date-precision-preserving planned-work context.

Supplied schedules are normalized into the canonical CSV.  Unmapped source
rows are counted but never guessed onto a project object; an absent canonical
source is reported as ``not_provided``, not as proof that no work was planned.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app.services.reference_data import get_objects_data


MOSCOW = ZoneInfo("Europe/Moscow")
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "planned_work.normalized.csv"
FIELDS = (
    "planned_work_id", "source_file", "source_row", "object_id", "object_name_raw",
    "equipment_context", "work_type", "planned_start", "planned_end", "date_precision",
    "temporal_constraint_kind", "constraint_at", "source_time_text",
    "mapping_method", "mapping_confidence", "notes",
)


def _name_key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def parse_source_deadline(value: str) -> tuple[str, str] | None:
    """Parse source wording like 'до 900 13.01.2026' as a Moscow deadline."""
    text = str(value or "").strip()
    match = re.fullmatch(r"до\s+(\d{1,2})(\d{2})\s+(\d{2}\.\d{2}\.\d{4})", text,
                         flags=re.IGNORECASE)
    if not match:
        return None
    hour, minute, raw_date = match.groups()
    day, month, year = map(int, raw_date.split("."))
    deadline_date = date(year, month, day)
    hour_number, minute_number = int(hour), int(minute)
    if hour_number > 23 or minute_number > 59:
        raise ValueError(f"invalid source deadline: {text}")
    constraint_at = datetime.combine(deadline_date, time(hour_number, minute_number), tzinfo=MOSCOW)
    return constraint_at.isoformat(), text


def normalize_record(record: dict, *, source_file: str, source_row: int,
                     objects: list[dict] | None = None) -> dict:
    """Normalize one already-column-mapped source record without fuzzy matching."""
    objects = get_objects_data() if objects is None else objects
    object_names: dict[str, list[int]] = {}
    valid_ids = {item["object_id"] for item in objects}
    for item in objects:
        key = _name_key(str(item["object_name"]))
        object_names.setdefault(key, []).append(item["object_id"])

    raw_id = record.get("object_id")
    raw_name = str(record.get("object_name_raw") or "").strip() or None
    object_id = None
    mapping_method = "unmapped"
    confidence = "none"
    if raw_id not in (None, ""):
        try:
            candidate = int(raw_id)
        except (TypeError, ValueError) as error:
            raise ValueError("object_id must be an integer when provided") from error
        if candidate in valid_ids:
            object_id, mapping_method, confidence = candidate, "source_object_id", "exact"
        else:
            raise ValueError(f"source object_id {candidate} is not in the project registry")
    elif raw_name:
        matches = object_names.get(_name_key(raw_name), [])
        if len(matches) == 1:
            object_id, mapping_method, confidence = matches[0], "exact_normalized_name", "exact"
        elif len(matches) > 1:
            mapping_method, confidence = "ambiguous_exact_name", "ambiguous"

    start = str(record.get("planned_start") or "").strip()
    end = str(record.get("planned_end") or "").strip()
    if not start or not end:
        raise ValueError("planned_start and planned_end are required")
    precision = ("date_only" if _is_date(start) and _is_date(end) else
                 "month_only" if _is_month(start) and _is_month(end) else "timestamp")
    start_value, end_value = _parse_temporal(start), _parse_temporal(end)
    if end_value < start_value:
        raise ValueError("planned_end must not precede planned_start")
    constraint_kind = str(record.get("temporal_constraint_kind") or "none").strip()
    constraint_at = _optional(record.get("constraint_at"))
    source_time_text = _optional(record.get("source_time_text"))
    if constraint_kind == "deadline":
        if not constraint_at or not source_time_text:
            raise ValueError("deadline requires constraint_at and source_time_text")
        parsed_constraint = _parse_temporal(constraint_at)
        if parsed_constraint.isoformat() != constraint_at:
            constraint_at = parsed_constraint.isoformat()
    elif constraint_kind != "none":
        raise ValueError("temporal_constraint_kind must be 'none' or 'deadline'")
    elif constraint_at or source_time_text:
        raise ValueError("constraint fields require temporal_constraint_kind='deadline'")
    return {
        "planned_work_id": str(record.get("planned_work_id") or f"{Path(source_file).name}:{source_row}"),
        "source_file": Path(source_file).name,
        "source_row": int(source_row),
        "object_id": object_id,
        "object_name_raw": raw_name,
        "equipment_context": _optional(record.get("equipment_context")),
        "work_type": str(record.get("work_type") or "unspecified").strip(),
        "planned_start": start,
        "planned_end": end,
        "date_precision": precision,
        "temporal_constraint_kind": constraint_kind,
        "constraint_at": constraint_at,
        "source_time_text": source_time_text,
        "mapping_method": mapping_method,
        "mapping_confidence": confidence,
        "notes": _optional(record.get("notes")),
    }


def _optional(value):
    value = str(value or "").strip()
    return value or None


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return len(value) == 10
    except ValueError:
        return False


def _is_month(value: str) -> bool:
    if len(value) != 7 or value[4] != "-":
        return False
    try:
        datetime.strptime(value, "%Y-%m")
        return True
    except ValueError:
        return False


def _parse_temporal(value: str) -> datetime:
    if _is_month(value):
        year, month = map(int, value.split("-"))
        return datetime(year, month, 1, tzinfo=MOSCOW)
    if _is_date(value):
        day = date.fromisoformat(value)
        return datetime.combine(day, time.min, tzinfo=MOSCOW)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid planned-work date/time: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW)
    return parsed.astimezone(MOSCOW)


def _bounds(item: dict) -> tuple[datetime, datetime]:
    start = _parse_temporal(item["planned_start"])
    end = _parse_temporal(item["planned_end"])
    if item["date_precision"] == "date_only":
        end = datetime.combine(end.date() + timedelta(days=1), time.min, tzinfo=MOSCOW)
    elif item["date_precision"] == "month_only":
        next_month = date(end.year + (end.month == 12), 1 if end.month == 12 else end.month + 1, 1)
        end = datetime.combine(next_month, time.min, tzinfo=MOSCOW)
    return start, end


def read_canonical(path: Path | None = None) -> tuple[str, list[dict], int]:
    from fastapi import HTTPException

    path = path or Path(os.environ.get("TGOAT_PLANNED_WORK_FILE", DEFAULT_PATH))
    if not path.is_file():
        return "not_provided", [], 0
    rows: list[dict] = []
    source_rows = 0
    unresolved_rows = 0
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(FIELDS) <= set(reader.fieldnames):
            raise HTTPException(503, detail={"code": "planned_work_schema_invalid"})
        for number, source in enumerate(reader, start=2):
            source_rows += 1
            row = normalize_record(source, source_file=source.get("source_file") or path.name,
                                   source_row=source.get("source_row") or number)
            if row["object_id"] is not None:
                rows.append(row)
            else:
                unresolved_rows += 1
    return ("available" if source_rows else "not_provided"), rows, unresolved_rows


def query_planned_work(object_id: int, as_of: datetime, *, path: Path | None = None,
                       upcoming_days: int = 30, recent_days: int = 30) -> dict:
    source_status, rows, unmapped_records = read_canonical(path)
    now = as_of.astimezone(MOSCOW) if as_of.tzinfo else as_of.replace(tzinfo=MOSCOW)
    upcoming_limit = now + timedelta(days=upcoming_days)
    recent_limit = now - timedelta(days=recent_days)
    active, upcoming, recent = [], [], []
    for row in rows:
        if row["object_id"] != object_id:
            continue
        if row["temporal_constraint_kind"] == "deadline":
            deadline = _parse_temporal(row["constraint_at"])
            row["deadline_at"] = deadline.isoformat()
            if now < deadline <= upcoming_limit:
                upcoming.append(row)
            elif recent_limit <= deadline <= now:
                recent.append(row)
            continue
        start, end = _bounds(row)
        if start <= now < end:
            active.append(row)
        elif now < start <= upcoming_limit:
            upcoming.append(row)
        elif recent_limit <= end <= now:
            recent.append(row)
    key = lambda row: (_parse_temporal(row["planned_start"]), row["planned_work_id"])
    return {
        "object_id": object_id,
        "as_of": now.isoformat(),
        "source_status": source_status,
        "unmapped_records": unmapped_records,
        "active": sorted(active, key=key),
        "upcoming": sorted(upcoming, key=key),
        "recent": sorted(recent, key=key, reverse=True),
    }
