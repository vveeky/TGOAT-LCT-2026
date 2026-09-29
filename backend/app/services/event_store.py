from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re

import duckdb
import polars as pl

from backend.app.config import EVENT_SOURCE_TIMEZONE, as_utc
from backend.app.processed_schema import PROCESSED_SCHEMA
from backend.app.services.reference_data import get_reference_meta, ReferenceDataUnavailableError
from scripts.normalization import PIPELINE_VERSION, metadata_path

ROOT = Path(__file__).resolve().parents[3]
PROCESSED_DIR = ROOT / "ml/data/processed"
YEARS = range(2019, 2027)


class HistoryStorageUnavailableError(RuntimeError):
    pass


def get_event_files() -> list[tuple[int, Path]]:
    return [
        (year, PROCESSED_DIR / f"events_{year}.parquet")
        for year in YEARS
        if (PROCESSED_DIR / f"events_{year}.parquet").is_file()
    ]


def _manifest_error(year: int, path: Path) -> tuple[str | None, str | None]:
    manifest = metadata_path(path)
    if not manifest.is_file():
        return "missing", f"events_{year}: manifest is missing"
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        required = {"pipeline_version", "year", "source", "reference_sha256", "rows", "limit", "partial"}
        missing = required - metadata.keys()
        if missing:
            return "corrupt", f"events_{year}: manifest missing keys {sorted(missing)}"
        if metadata["pipeline_version"] != PIPELINE_VERSION or metadata["year"] != year:
            return "corrupt", f"events_{year}: incompatible manifest version/year"
        if metadata["partial"] is True or metadata["limit"] is not None:
            return "partial", f"events_{year}: partial manifest"
        if not isinstance(metadata["rows"], int) or metadata["rows"] <= 0:
            return "corrupt", f"events_{year}: invalid manifest row count"
        source = metadata["source"]
        if (not isinstance(source, dict) or set(source) != {"size", "mtime_ns"}
                or any(type(source[key]) is not int or source[key] < 0 for key in source)):
            return "corrupt", f"events_{year}: invalid source fingerprint"
        manifest_hash = metadata["reference_sha256"]
        if not isinstance(manifest_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_hash):
            return "corrupt", f"events_{year}: invalid reference_sha256"
        current_hash = get_reference_meta().get("source_sha256")
        if manifest_hash != current_hash:
            return "corrupt", f"events_{year}: reference_sha256 does not match current reference data"
        if not isinstance(current_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", current_hash):
            return "corrupt", f"events_{year}: invalid manifest fingerprints"
        actual_rows = pl.scan_parquet(path).select(pl.len()).collect().item()
        if actual_rows != metadata["rows"]:
            return "corrupt", f"events_{year}: manifest row count does not match Parquet"
        return None, None
    except (OSError, ValueError, TypeError, ReferenceDataUnavailableError,
            json.JSONDecodeError, pl.exceptions.PolarsError) as error:
        return "corrupt", f"events_{year}: invalid manifest: {error}"


def history_storage_available() -> bool:
    return get_history_storage_health()["status"] == "available"


HISTORY_SCHEMA = PROCESSED_SCHEMA


def get_history_storage_health() -> dict:
    expected = list(YEARS)
    files = get_event_files()
    available = [year for year, _ in files]
    if not files:
        return {"status": "unavailable", "expected_years": expected,
                "available_years": [], "detail": "no event partitions"}
    try:
        signature = tuple((year, str(path), path.stat().st_size, path.stat().st_mtime_ns)
                          for year, path in files)
    except OSError as error:
        return {"status": "corrupt", "expected_years": expected,
                "available_years": available, "detail": str(error)}
    error = _history_schema_error(signature)
    if error:
        return {"status": "corrupt", "expected_years": expected,
                "available_years": available, "detail": error}
    manifest_states = [_manifest_error(year, path) for year, path in files]
    missing_years = sorted(set(expected) - set(available))
    for status in ("corrupt", "missing"):
        details = [detail for state, detail in manifest_states if state == status]
        if details:
            return {"status": status, "expected_years": expected,
                    "available_years": available, "detail": "; ".join(details)}
    partial_details = [detail for state, detail in manifest_states if state == "partial"]
    if missing_years or partial_details:
        details = partial_details
        if missing_years:
            details.append(f"missing years: {missing_years}")
        return {"status": "partial", "expected_years": expected,
                "available_years": available, "detail": "; ".join(details)}
    status = "available" if available == expected else "partial"
    detail = None if status == "available" else f"missing years: {sorted(set(expected) - set(available))}"
    return {"status": status, "expected_years": expected,
            "available_years": available, "detail": detail}


@lru_cache(maxsize=16)
def _history_schema_error(signature: tuple) -> str | None:
    try:
        for year, path_string, _, _ in signature:
            schema = pl.scan_parquet(path_string).collect_schema()
            missing = set(HISTORY_SCHEMA) - set(schema.names())
            unexpected = set(schema.names()) - set(HISTORY_SCHEMA)
            wrong = {name: (str(dtype), str(schema[name])) for name, dtype in HISTORY_SCHEMA.items()
                     if name in schema and schema[name] != dtype}
            if missing or unexpected or wrong:
                raise ValueError(
                    f"events_{year}: missing={sorted(missing)}, unexpected={sorted(unexpected)}, "
                    f"wrong_types={wrong}"
                )
        return None
    except (OSError, ValueError, pl.exceptions.PolarsError) as error:
        return str(error)


def _source_naive(value: datetime) -> datetime:
    return as_utc(value).astimezone(EVENT_SOURCE_TIMEZONE).replace(tzinfo=None)


def _utc_iso(source_naive: datetime | None) -> str | None:
    if source_naive is None:
        return None
    return source_naive.replace(tzinfo=EVENT_SOURCE_TIMEZONE).astimezone(timezone.utc).isoformat()


def select_event_files(
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
) -> list[tuple[int, Path]]:
    files = get_event_files()
    if from_timestamp is None and to_timestamp is None:
        return files
    first = _source_naive(from_timestamp).year if from_timestamp else min(year for year, _ in files)
    last = _source_naive(to_timestamp).year if to_timestamp else max(year for year, _ in files)
    return [(year, path) for year, path in files if first <= year <= last]


def _read_history_files(files, channel_id, from_timestamp, to_timestamp, limit, alarm_only=False, offset=0):
    if not files:
        return []
    files_sql = ", ".join(
        f"'{path.as_posix().replace(chr(39), chr(39) * 2)}'" for _, path in files
    )
    where = ["channel_id = ?"]
    params: list[object] = [channel_id]
    if from_timestamp is not None:
        where.append("timestamp >= ?")
        params.append(_source_naive(from_timestamp))
    if to_timestamp is not None:
        where.append("timestamp <= ?")
        params.append(_source_naive(to_timestamp))
    if alarm_only:
        where.append('is_alarm = true')
    params.extend([limit, offset])
    with duckdb.connect() as connection:
        return connection.execute(
            f"""SELECT timestamp, sensor_value, is_alarm, source_year
            FROM read_parquet([{files_sql}]) WHERE {' AND '.join(where)}
            ORDER BY timestamp DESC, sensor_value, is_alarm, source_year LIMIT ? OFFSET ?""",
            params,
        ).fetchall()


def get_channel_history(channel_id, from_timestamp=None, to_timestamp=None, limit=200, alarm_only=False, offset=0):
    all_files = get_event_files()
    health = get_history_storage_health()
    if health["status"] != "available":
        raise HistoryStorageUnavailableError(
            f"Event history storage is {health['status']}: {health.get('detail') or ''}".strip()
        )

    if from_timestamp is not None or to_timestamp is not None or offset or alarm_only:
        rows = _read_history_files(
            select_event_files(from_timestamp, to_timestamp), channel_id,
            from_timestamp, to_timestamp, limit, alarm_only, offset,
        )
    else:
        rows = []
        # Annual partitions do not overlap after pipeline validation, so once a
        # newer partition fills the limit, older years cannot enter the result.
        for annual_file in reversed(all_files):
            rows.extend(_read_history_files([annual_file], channel_id, None, None, limit))
            rows.sort(key=lambda row: row[0], reverse=True)
            rows = rows[:limit]
            if len(rows) >= limit:
                break

    return [
        {"timestamp": _utc_iso(row[0]), "value": row[1], "is_alarm": row[2], "source_year": row[3]}
        for row in rows
    ]


def get_last_event_times(channel_ids: list[int]) -> dict[int, str]:
    if not channel_ids or not history_storage_available():
        return {}
    remaining = set(channel_ids)
    result: dict[int, str] = {}
    for _, path in reversed(get_event_files()):
        if not remaining:
            break
        ids = sorted(remaining)
        placeholders = ",".join("?" for _ in ids)
        with duckdb.connect() as connection:
            rows = connection.execute(
                f"""SELECT channel_id, max(timestamp) FROM read_parquet(?)
                WHERE channel_id IN ({placeholders}) GROUP BY channel_id""",
                [str(path), *ids],
            ).fetchall()
        for channel_id, timestamp in rows:
            result[channel_id] = _utc_iso(timestamp)
            remaining.discard(channel_id)
    return result


def get_latest_channel_events(channel_ids: list[int], at_or_before: datetime | None = None) -> dict[int, dict]:
    if not channel_ids or not history_storage_available():return {}
    remaining=set(channel_ids);result={}
    for _,path in reversed(select_event_files(to_timestamp=at_or_before)):
        if not remaining:break
        ids=sorted(remaining);marks=','.join('?' for _ in ids)
        time_clause=' AND timestamp <= ?' if at_or_before is not None else ''
        params=[str(path),*ids]
        if at_or_before is not None:params.append(_source_naive(at_or_before))
        with duckdb.connect() as connection:
            rows=connection.execute(f'''SELECT channel_id,timestamp,sensor_value,is_alarm FROM read_parquet(?)
              WHERE channel_id IN ({marks}){time_clause}
              QUALIFY ROW_NUMBER() OVER(PARTITION BY channel_id ORDER BY timestamp DESC,sensor_value,is_alarm)=1''',params).fetchall()
        for channel_id,timestamp,value,alarm in rows:
            result[channel_id]={'timestamp':_utc_iso(timestamp),'value':value,'alarm':alarm}
            remaining.discard(channel_id)
    return result
