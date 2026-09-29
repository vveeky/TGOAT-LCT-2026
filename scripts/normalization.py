from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.app.processed_schema import PROCESSED_NON_NULL_COLUMNS, PROCESSED_SCHEMA

PIPELINE_VERSION = "3"
EXPECTED_SCHEMA = PROCESSED_SCHEMA
NON_NULL_COLUMNS = PROCESSED_NON_NULL_COLUMNS


def _reference_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths: digest.update(path.read_bytes())
    return digest.hexdigest()


def source_fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def metadata_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".meta.json")


def load_references(channels_path: Path, objects_path: Path):
    channels = (pl.read_csv(channels_path).rename({
        "ид_канала_данных": "channel_id", "тип_инж_системы": "system_type",
        "тип_датчика": "sensor_type", "тег_инженерной_системы": "system_tag",
        "название_датчика": "sensor_name", "ид_объект": "object_id",
    }).with_columns(pl.col("channel_id").cast(pl.Int64), pl.col("object_id").cast(pl.Int64),
                    pl.lit(True).alias("channel_reference_found")))
    objects = (pl.read_csv(objects_path).rename({
        "ид_объект": "object_id", "иерархия_уровень": "object_level",
        "родитель": "parent_id", "вид_объекта": "object_type",
        "диспетчерское_название_объекта": "object_name",
    }).with_columns(pl.col("object_id").cast(pl.Int64),
                    pl.col("parent_id").cast(pl.Int64, strict=False),
                    pl.lit(True).alias("object_reference_found")))
    duplicate_channels = channels.group_by("channel_id").len().filter(pl.col("len") > 1)
    duplicate_objects = objects.group_by("object_id").len().filter(pl.col("len") > 1)
    if duplicate_channels.height:
        raise ValueError("Duplicate channel_id in channels reference")
    if duplicate_objects.height:
        raise ValueError("Duplicate object_id in objects reference")
    return channels.lazy(), objects.lazy()


def canonicalize_events(events: pl.LazyFrame, year: int,
                        channels_path: Path, objects_path: Path) -> pl.LazyFrame:
    """Apply the canonical v3 event contract to batch or future stream records."""
    events = (events.filter(
        pl.col("ид_события").is_null() | (pl.col("ид_события") != "ид_события")
    ).rename({
        "ид_события": "event_id_raw", "ид_канала_данных": "channel_id_raw",
        "дата": "date", "время": "time", "тревожное": "alarm_raw",
        "значение_датчика": "sensor_value",
    }).with_columns(
        pl.col("event_id_raw").cast(pl.Int64, strict=False).alias("event_id"),
        pl.col("channel_id_raw").cast(pl.Int64, strict=False).alias("channel_id"),
        pl.concat_str([pl.col("date"), pl.lit(" "), pl.col("time")])
        .str.strptime(pl.Datetime, format="%Y-%m-%d %H:%M:%S", strict=False).alias("timestamp"),
        pl.col("alarm_raw").str.to_lowercase().str.strip_chars().alias("alarm_normalized"),
        pl.lit(year, dtype=pl.Int32).alias("source_year"),
    ).with_columns(
        pl.when(pl.col("alarm_normalized").is_in(["true", "t", "1"]))
        .then(True).when(pl.col("alarm_normalized").is_in(["false", "f", "0"]))
        .then(False).otherwise(None).alias("is_alarm"),
        (~pl.col("alarm_normalized").is_in(["true", "t", "1", "false", "f", "0"]))
        .fill_null(True).alias("invalid_alarm"),
    ).drop("alarm_normalized"))
    channels, objects = load_references(channels_path, objects_path)
    return (events.join(channels, on="channel_id", how="left")
            .join(objects, on="object_id", how="left").with_columns(
        pl.col("channel_reference_found").fill_null(False),
        pl.col("object_reference_found").fill_null(False),
        pl.col("channel_id").is_null().alias("invalid_channel_id"),
        pl.col("event_id").is_null().alias("invalid_event_id"),
        pl.col("timestamp").is_null().alias("invalid_timestamp"),
    ))


def build_pipeline(input_path: Path, year: int, limit: int | None,
                   channels_path: Path, objects_path: Path) -> pl.LazyFrame:
    events = pl.scan_csv(input_path, schema_overrides={
        "ид_события": pl.String, "ид_канала_данных": pl.String, "дата": pl.String,
        "время": pl.String, "тревожное": pl.String, "значение_датчика": pl.String,
    })
    if limit is not None: events = events.head(limit)
    return canonicalize_events(events, year, channels_path, objects_path)


def validate_parquet(path: Path, expected_year: int) -> dict:
    frame = pl.scan_parquet(path)
    schema = frame.collect_schema()
    missing = set(EXPECTED_SCHEMA) - set(schema.names())
    unexpected = set(schema.names()) - set(EXPECTED_SCHEMA)
    if missing: raise ValueError(f"Parquet schema is missing: {sorted(missing)}")
    if unexpected: raise ValueError(f"Parquet schema has unexpected columns: {sorted(unexpected)}")
    wrong_types = {
        name: {"expected": str(dtype), "actual": str(schema[name])}
        for name, dtype in EXPECTED_SCHEMA.items() if schema[name] != dtype
    }
    if wrong_types:
        raise ValueError(f"Parquet schema has wrong dtypes: {wrong_types}")
    stats = frame.select(
        pl.len().alias("rows"), pl.col("source_year").n_unique().alias("source_year_count"),
        pl.col("source_year").min().alias("source_year"),
        (pl.col("timestamp").is_not_null() & (pl.col("timestamp").dt.year() != expected_year))
        .sum().alias("wrong_year_rows"), pl.col("invalid_alarm").sum().alias("invalid_alarm_rows"),
        *[pl.col(name).null_count().alias(f"null_{name}") for name in sorted(NON_NULL_COLUMNS)],
    ).collect().row(0, named=True)
    if stats["rows"] <= 0: raise ValueError("Parquet contains no rows")
    if stats["source_year_count"] != 1 or stats["source_year"] != expected_year:
        raise ValueError("source_year does not match requested year")
    if stats["wrong_year_rows"]:
        raise ValueError(f"{stats['wrong_year_rows']} timestamps do not match requested year")
    invalid_nulls = {name: stats[f"null_{name}"] for name in NON_NULL_COLUMNS
                     if stats[f"null_{name}"]}
    if invalid_nulls:
        raise ValueError(f"Parquet has nulls in non-null columns: {invalid_nulls}")
    return stats


def normalize_file(input_path: Path, output_path: Path, year: int,
                   channels_path: Path, objects_path: Path, limit: int | None = None) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pipeline = build_pipeline(input_path, year, limit, channels_path, objects_path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        pipeline.sink_parquet(temp_path, compression="zstd")
        stats = validate_parquet(temp_path, year)
        os.replace(temp_path, output_path)
        metadata = {"pipeline_version": PIPELINE_VERSION, "year": year,
                    "source": source_fingerprint(input_path),
                    "reference_sha256": _reference_hash([channels_path, objects_path]),
                    "rows": stats["rows"], "invalid_alarm_rows": stats["invalid_alarm_rows"],
                    "limit": limit, "partial": limit is not None}
        meta_path = metadata_path(output_path)
        meta_temp = meta_path.with_suffix(meta_path.suffix + ".tmp")
        meta_temp.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(meta_temp, meta_path)
        return metadata
    finally:
        temp_path.unlink(missing_ok=True)


def output_is_current(input_path: Path, output_path: Path, year: int,
                      channels_path: Path, objects_path: Path) -> tuple[bool, str]:
    meta_path = metadata_path(output_path)
    if not output_path.is_file() or not meta_path.is_file(): return False, "output or metadata missing"
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        stats = validate_parquet(output_path, year)
    except (OSError, ValueError, json.JSONDecodeError, pl.exceptions.PolarsError) as error:
        return False, f"invalid output: {error}"
    expected = {"pipeline_version": PIPELINE_VERSION, "year": year,
                "source": source_fingerprint(input_path),
                "reference_sha256": _reference_hash([channels_path, objects_path])}
    for key, value in expected.items():
        if metadata.get(key) != value: return False, f"stale {key}"
    if metadata.get("partial") is not False or metadata.get("limit") is not None:
        return False, "partial output"
    if metadata.get("rows") != stats["rows"]:
        return False, "row count does not match metadata"
    return True, "current"
