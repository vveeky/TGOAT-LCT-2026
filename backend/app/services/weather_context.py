"""Optional city-wide weather evidence with historical issuance-time constraints.

Public weather records are not mapped to invented object coordinates. Forecasts
may describe future valid_at, but must have actually been issued by as_of.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping

from backend.app.config import as_utc


def _stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('weather timestamps must be timezone-aware')
    return as_utc(value)


def weather_context_at(records: Iterable[Mapping], as_of: datetime,
                       *, locale: str = 'Москва (общегородской контекст)') -> dict:
    as_of = _stamp(as_of)
    observations, forecasts = [], []
    for item in records:
        kind = item.get('kind')
        if kind not in ('observation', 'forecast'):
            raise ValueError('weather kind must be observation or forecast')
        issued_at = _stamp(item['issued_at'])
        valid_at = _stamp(item['valid_at'])
        if issued_at > as_of:
            continue  # Future issuance is NOT known at historical as_of.
        if kind == 'observation' and valid_at > as_of:
            continue  # Future observed weather is a leak, not a forecast.
        payload = {'kind': kind, 'issued_at': issued_at.isoformat(),
                   'valid_at': valid_at.isoformat(), 'provider': item.get('provider'),
                   'precipitation_mm': item.get('precipitation_mm'),
                   'temperature_c': item.get('temperature_c'),
                   'relative_humidity_percent': item.get('relative_humidity_percent'),
                   'pressure_hpa': item.get('pressure_hpa'),
                   'source_kind': item.get('source_kind', 'external')}
        (observations if kind == 'observation' else forecasts).append(payload)
    observations.sort(key=lambda row: row['valid_at'], reverse=True)
    forecasts.sort(key=lambda row: (row['issued_at'], row['valid_at']), reverse=True)
    return {'status': 'available' if (observations or forecasts) else 'unavailable',
            'as_of': as_of.isoformat(), 'geographic_scope': locale,
            'observations': observations[:24], 'forecasts': forecasts[:24],
            'object_coordinates_used': False}


def load_weather_context(path: str | Path | None, as_of: datetime) -> dict:
    """Read verified operator-configured JSONL; no automatic synthetic weather."""
    if not path:
        return weather_context_at([], as_of)
    source = Path(path)
    if not source.is_file():
        return {'status': 'unavailable', 'reason': 'configured_weather_source_missing',
                'geographic_scope': 'Москва (общегородской контекст)',
                'observations': [], 'forecasts': [], 'object_coordinates_used': False}
    rows = []
    with source.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip(): rows.append(json.loads(line))
    return weather_context_at(rows, as_of)
