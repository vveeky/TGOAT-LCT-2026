-- Demo-only simulated incoming telemetry, with a durable authoritative watermark.
-- Replay sessions remain isolated SQLite; this table belongs to the normal PG contour.
CREATE TABLE IF NOT EXISTS simulated_live_progress (
    source_id TEXT PRIMARY KEY,
    source_digest CHAR(64) NOT NULL,
    baseline_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    processed_boundaries BIGINT NOT NULL DEFAULT 0 CHECK (processed_boundaries >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (processed_at >= baseline_at AND processed_at <= end_at)
);
CREATE TABLE IF NOT EXISTS simulated_live_events (
    source_id TEXT NOT NULL REFERENCES simulated_live_progress(source_id),
    source_sequence BIGINT NOT NULL CHECK (source_sequence >= 0),
    timestamp TIMESTAMPTZ NOT NULL,
    object_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    payload JSONB NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_id, source_sequence)
);
CREATE INDEX IF NOT EXISTS idx_simulated_live_events_time ON simulated_live_events(source_id, timestamp);
