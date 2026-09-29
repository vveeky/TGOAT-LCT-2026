"""Shared SQLite connection helpers for file and named-memory databases."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_sqlite(path: str | Path, **kwargs) -> sqlite3.Connection:
    database = str(path)
    return sqlite3.connect(database, uri=database.startswith("file:"), **kwargs)
