"""Sparse technical-text diagnostics from the canonical event partitions."""

from __future__ import annotations

from pathlib import Path

import duckdb

from .build_risk_dataset import PROCESSED, _q

SPECIAL_CODES = ("-100", "255", "-127", "-3276")


def diagnostic_sql() -> str:
    files = "[" + ", ".join(_q(str(PROCESSED / f"events_{year}.parquet")) for year in range(2019, 2027)) + "]"
    special = ", ".join(_q(value) for value in SPECIAL_CODES)
    counts = ",\n       ".join(
        f"sum(CASE WHEN value = {_q(code)} THEN 1 ELSE 0 END) AS special_code_{code.replace('-', 'minus_')}_count"
        for code in SPECIAL_CODES
    )
    return f"""
      WITH filtered AS (
        SELECT timestamp, object_id, channel_id,
               trim(sensor_value) AS value,
               lower(trim(sensor_value)) = 'неисправен' AS malfunction_text
        FROM read_parquet({files})
        WHERE object_id IS NOT NULL AND channel_id IS NOT NULL
          AND (lower(trim(sensor_value)) = 'неисправен' OR trim(sensor_value) IN ({special}))
      )
      SELECT object_id, channel_id, date_trunc('hour', timestamp) AS hour_ts,
             sum(CASE WHEN malfunction_text THEN 1 ELSE 0 END) AS malfunction_text_count,
             max(CASE WHEN malfunction_text THEN timestamp END) AS last_malfunction_text_ts,
             {counts}
      FROM filtered GROUP BY object_id, channel_id, hour_ts
    """


def build_diagnostics(con: duckdb.DuckDBPyConnection, output: Path) -> dict:
    con.execute(f"COPY ({diagnostic_sql()}) TO {_q(str(output))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    row = con.execute(f"SELECT count(*), sum(malfunction_text_count), sum(special_code_minus_100_count + special_code_255_count + special_code_minus_127_count + special_code_minus_3276_count) FROM read_parquet({_q(str(output))})").fetchone()
    return {"rows": row[0], "malfunction_text_rows": row[1], "special_code_rows": row[2], "bytes": output.stat().st_size}
