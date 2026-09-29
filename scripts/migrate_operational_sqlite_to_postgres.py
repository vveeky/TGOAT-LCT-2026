"""Idempotently import operational SQLite stores into PostgreSQL, read-only.

The SQLite sources are never modified. The importer uses an explicit dependency
order, preserves primary keys, verifies every imported row, then validates PG
foreign keys and identity sequences before returning a reconciliation report.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from backend.app.services.postgres_storage import PostgreSQLDatabase


TABLES = (
    "prediction_history", "forecast_cards", "forecast_card_predictions",
    "signal_work", "signal_decisions", "maintenance_requests", "maintenance_results",
    "maintenance_events", "signal_command_receipts", "maintenance_command_receipts",
    "signal_task_dispositions", "recommendations", "operational_notifications",
    "operational_notification_receipts", "operational_prediction_reviews",
    "operational_prediction_outcomes", "operational_cycles",
)
SOURCE_BY_TABLE = {
    "recommendations": "recommendations",
    "operational_cycles": "cycles",
}
TIMESTAMP_COLUMNS = {
    "snapshot_ts", "generated_at", "created_at", "updated_at", "source_freshness_ts",
    "first_positive_snapshot_ts", "last_positive_snapshot_ts", "committed_at", "applied_at",
    "finished_at", "started_at", "assigned_at", "accepted_at", "submitted_at", "timestamp",
    "read_at",
}
JSON_COLUMNS = {"result_json", "basis_json"}
BOOLEAN_COLUMNS = {"risk_flag"}


def _timestamp(value: Any):
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _convert(column: str, value: Any):
    if value is None:
        return None
    if column in TIMESTAMP_COLUMNS:
        return _timestamp(value)
    if column in BOOLEAN_COLUMNS:
        return bool(value)
    if column in JSON_COLUMNS:
        if column == "basis_json" and isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = [part for part in value.split(",") if part]
        elif isinstance(value, str):
            parsed = json.loads(value)
        else:
            parsed = value
        return Jsonb(parsed)
    return value


def _source(path: Path):
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def validate_sources(*, history: Path, recommendations: Path | None = None,
                     cycles: Path | None = None) -> None:
    """Validate every configured SQLite database before any PG-side mutation.

    An optional table may be absent in a valid source database; an explicitly
    configured source database itself may never be missing or unreadable.
    Unspecified optional sources reuse the required history database.
    """
    paths = {"history": history, "recommendations": recommendations or history,
             "cycles": cycles or history}
    opened: dict[str, sqlite3.Connection] = {}
    try:
        for source_name, path in paths.items():
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"SQLite {source_name} source does not exist: {path}")
            if not path.stat().st_mode & 0o444:
                raise PermissionError(f"SQLite {source_name} source is not readable: {path}")
            key = str(path.resolve())
            if key not in opened:
                try:
                    opened[key] = _source(path)
                    opened[key].execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
                except (sqlite3.Error, OSError) as error:
                    raise ValueError(f"SQLite {source_name} source is not a readable SQLite database: {path}") from error
    finally:
        for connection in set(opened.values()):
            connection.close()


def _source_tables(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _primary_key(connection, table):
    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in sorted((c for c in columns if c["pk"]), key=lambda c: c["pk"])]


def import_sources(database: PostgreSQLDatabase, *, history: Path, recommendations: Path | None = None,
                   cycles: Path | None = None) -> dict[str, Any]:
    validate_sources(history=history, recommendations=recommendations, cycles=cycles)
    paths = {"history": history, "recommendations": recommendations or history, "cycles": cycles or history}
    opened: dict[str, sqlite3.Connection] = {}
    try:
        for source_name, path in paths.items():
            if path and path.exists():
                # Identical configured paths share a single read-only handle.
                key=str(path.resolve())
                if key not in opened:
                    opened[key]=_source(path)
        source_for = {name: opened[str(path.resolve())] for name, path in paths.items() if path and path.exists()}
        report: dict[str, Any] = {"source_paths": {key: str(value) if value else None for key, value in paths.items()},
                                  "tables": {}}
        with database.unit_of_work() as uow:
            conn = uow.connection
            for table in TABLES:
                source_name = SOURCE_BY_TABLE.get(table, "history")
                source = source_for.get(source_name)
                if source is None or table not in _source_tables(source):
                    report["tables"][table] = {"source_rows": 0, "postgres_rows": None, "imported": 0, "status": "absent"}
                    continue
                source.execute("PRAGMA foreign_keys=ON")
                fk_issue = source.execute("PRAGMA foreign_key_check").fetchone()
                if fk_issue:
                    raise RuntimeError(f"SQLite source {table} has foreign-key violation: {tuple(fk_issue)}")
                source_rows = source.execute(f"SELECT * FROM {table}").fetchall()
                source_columns = list(source_rows[0].keys()) if source_rows else [
                    row["name"] for row in source.execute(f"PRAGMA table_info({table})")
                ]
                target_info = conn.execute("""SELECT column_name FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position""",(table,)).fetchall()
                target_columns = {row[0] for row in target_info}
                columns = [column for column in source_columns if column in target_columns]
                if not columns:
                    report["tables"][table] = {"source_rows": len(source_rows), "postgres_rows": 0, "imported": 0, "status": "no compatible columns"}
                    continue
                primary_key = _primary_key(source, table)
                if not primary_key or any(key not in columns for key in primary_key):
                    raise RuntimeError(f"Cannot preserve primary key for SQLite table {table}")
                imported = 0
                placeholders = ",".join(["%s"] * len(columns))
                column_sql = ",".join(f'"{column}"' for column in columns)
                insert = f"INSERT INTO \"{table}\" ({column_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                predicate = " AND ".join(f'"{key}" IS NOT DISTINCT FROM %s' for key in primary_key)
                for raw in source_rows:
                    converted = {column: _convert(column, raw[column]) for column in columns}
                    values = tuple(converted[column] for column in columns)
                    before = conn.execute(f'SELECT 1 FROM "{table}" WHERE {predicate}',
                                          tuple(converted[key] for key in primary_key)).fetchone()
                    conn.execute(insert, values)
                    actual = conn.cursor().execute(
                        f'SELECT {column_sql} FROM "{table}" WHERE {predicate}',
                        tuple(converted[key] for key in primary_key)).fetchone()
                    if actual is None:
                        raise RuntimeError(f"Imported {table} row disappeared for key {tuple(raw[key] for key in primary_key)}")
                    # DB-native JSON/timestamps can be compared after normalization.
                    expected = tuple(converted[column].obj if isinstance(converted[column], Jsonb) else converted[column]
                                     for column in columns)
                    actual_values = tuple(actual)
                    if actual_values != expected:
                        raise RuntimeError(f"Existing PostgreSQL row conflicts with SQLite {table} key {tuple(raw[key] for key in primary_key)}")
                    imported += int(before is None)
                count = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                if count < len(source_rows):
                    raise RuntimeError(f"PostgreSQL row count is smaller than source for {table}")
                report["tables"][table] = {"source_rows": len(source_rows), "postgres_rows": count,
                                           "imported": imported, "status": "reconciled"}
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            for table in ("signal_decisions", "maintenance_requests", "maintenance_results",
                          "maintenance_events", "operational_notifications", "operational_prediction_reviews",
                          "operational_prediction_outcomes"):
                row = conn.execute("SELECT pg_get_serial_sequence(%s,'id')", (table,)).fetchone()
                if row and row[0]:
                    conn.execute(f"SELECT setval(%s,COALESCE((SELECT MAX(id) FROM \"{table}\"),1),"
                                 f"EXISTS(SELECT 1 FROM \"{table}\"))", (row[0],))
        return report
    finally:
        for connection in set(opened.values()):
            connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history",type=Path,required=True)
    parser.add_argument("--recommendations",type=Path)
    parser.add_argument("--cycles",type=Path)
    parser.add_argument("--database-url",default=None)
    args=parser.parse_args()
    # Source validation is deliberately before opening/migrating PostgreSQL:
    # invalid explicit inputs must not cause even schema-side partial work.
    validate_sources(history=args.history, recommendations=args.recommendations, cycles=args.cycles)
    import os
    url=args.database_url or os.environ.get("TGOAT_OPERATIONAL_DATABASE_URL")
    database=PostgreSQLDatabase(url or "")
    database.open()
    try:
        database.migrate()
        print(json.dumps(import_sources(database,history=args.history,recommendations=args.recommendations,
            cycles=args.cycles),ensure_ascii=False,indent=2,default=str))
    finally:
        database.close()


if __name__ == "__main__":
    main()
