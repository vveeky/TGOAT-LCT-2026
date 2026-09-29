"""Server-authoritative upper bound for canonical telemetry reads."""
from fastapi import HTTPException, Request
import os
from datetime import datetime
from backend.app.config import as_utc


def telemetry_upper_bound(request: Request):
    runtime = getattr(request.app.state, 'replay_runtime', None)
    if runtime is not None:
        return min(as_utc(runtime.clock.now()), as_utc(runtime.end_at))
    if getattr(request.app.state, 'runtime_mode', 'normal') == 'replay':
        # A missing replay clock must not silently expose the full history.
        raise HTTPException(503, 'Replay telemetry clock is unavailable')
    if os.environ.get('TGOAT_SIMULATED_LIVE_MODE', '0') == '1':
        database = getattr(request.app.state, 'operational_database', None)
        if database is None:
            raise HTTPException(503, 'Simulated live operational database is unavailable')
        from backend.app.services.live_status import current_simulated_watermark
        progress = current_simulated_watermark(database)
        if not progress:
            raise HTTPException(503, 'Simulated live ingestion watermark is unavailable')
        return datetime.fromisoformat(progress['processed_at'])
    return None
