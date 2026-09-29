"""Public Moscow *city-wide* weather forecast adapter (Open-Meteo).

Not customer data and never a historical-as_of observation. Every record has
an explicit retrieval/issuance instant. No forecast from a later download is
presented as known at an earlier replay time.
"""
from __future__ import annotations
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen, Request

# City-center coordinates for city-level weather only, NOT any collector object.
CITY_WEATHER_URL = ('https://api.open-meteo.com/v1/forecast?latitude=55.7558&longitude=37.6173'
                    '&hourly=temperature_2m,relative_humidity_2m,precipitation,pressure_msl&timezone=UTC&forecast_days=3')


def fetch_public_forecast(*, url: str = CITY_WEATHER_URL, now: datetime | None = None) -> list[dict]:
    issued = now or datetime.now(timezone.utc)
    if issued.tzinfo is None:
        raise ValueError('issued_at must be timezone-aware')
    issued = issued.astimezone(timezone.utc)
    with urlopen(Request(url, headers={'User-Agent': 'TGOAT-Hackathon/1.0'}), timeout=15) as stream:
        response = json.load(stream)
    hourly = response['hourly']
    timestamps, temperature, humidity, precipitation, pressure = (hourly[k] for k in ('time','temperature_2m','relative_humidity_2m','precipitation','pressure_msl'))
    if not len(timestamps) == len(temperature) == len(humidity) == len(precipitation) == len(pressure):
        raise ValueError('Public weather response arrays have inconsistent lengths')
    rows = []
    for instant, degrees, humid, mm, hpa in zip(timestamps, temperature, humidity, precipitation, pressure):
        valid = datetime.fromisoformat(instant.replace('Z', '+00:00'))
        if valid.tzinfo is None: valid = valid.replace(tzinfo=timezone.utc)  # requested timezone UTC
        # Download is a forecast issuance, even when the hourly slot precedes
        # retrieval. Never pass it off as independently measured observations.
        if valid < issued - timedelta(hours=1):
            continue
        rows.append({'kind':'forecast', 'issued_at':issued.isoformat(),
                     'valid_at':valid.astimezone(timezone.utc).isoformat(),
                     'provider':'Open-Meteo public API',
                     'source_kind':'city_wide_public_forecast_retrieved_at_issued_at',
                     'temperature_c': degrees, 'relative_humidity_percent': humid,
                     'pressure_hpa': hpa, 'precipitation_mm': mm})
    return rows


def append_forecast(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use append-only issuance snapshots; readers filter by issued_at <= as_of.
    with path.open('a', encoding='utf-8') as output:
        for row in records:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True)+'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--interval', type=int, default=0, help='seconds, zero=once')
    args = parser.parse_args()
    if args.interval < 0: parser.error('negative interval')
    while True:
        try:
            items = fetch_public_forecast()
            append_forecast(args.output, items)
            print(f'Public city forecast appended: {len(items)}', flush=True)
        except Exception as error:
            print(f'Public forecast unavailable: {type(error).__name__}: {error}', flush=True)
            if not args.interval: raise SystemExit(1)
        if not args.interval: break
        time.sleep(max(60, args.interval))

if __name__=='__main__': main()
