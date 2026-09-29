"""Read committed simulated-source watermark without loading a historical fixture."""
from __future__ import annotations
from psycopg.rows import dict_row


def current_simulated_watermark(database):
    # This is a normal-mode optional simulator, never a wall-clock time source.
    with database.connection() as connection:
        row = connection.cursor(row_factory=dict_row).execute('''
            SELECT source_id,source_digest,baseline_at,processed_at,end_at,processed_boundaries,
                   updated_at FROM simulated_live_progress
            ORDER BY updated_at DESC,source_id LIMIT 1''').fetchone()
        if row is None:
            return None
        return {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in row.items()}
