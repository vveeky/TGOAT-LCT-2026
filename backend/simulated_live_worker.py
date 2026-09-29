"""Run normal PostgreSQL inference from a pinned event source at 5-minute ticks.

Example: TGOAT_OPERATIONAL_DATABASE_URL=... python -m backend.simulated_live_worker
--once processes exactly one next historical 5-minute boundary. Default wall
interval 300s corresponds to a real 5-minute feed; --wall-interval 5 speeds up
only demo source arrival. This is NOT an external customer endpoint.
"""
import argparse
import os
import time
from backend.app.services.runtime_ml import build_postgres_operational_runtime
from backend.app.services.simulated_live import SimulatedLiveOperationalService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--wall-interval', type=float, default=300.0)
    parser.add_argument('--max-boundaries', type=int, default=0, help='0 means until source ends')
    args = parser.parse_args()
    if args.wall_interval < 0.1:
        parser.error('--wall-interval must be >= 0.1 seconds')
    runtime = build_postgres_operational_runtime()
    try:
        service = SimulatedLiveOperationalService(runtime['database'], runtime)
        counter = 0
        while True:
            status = service.process_one()
            print(status, flush=True)
            if args.once or status['status'] == 'completed':
                break
            counter += 1
            if args.max_boundaries and counter >= args.max_boundaries:
                break
            time.sleep(args.wall_interval)
    finally:
        runtime['database'].close()


if __name__ == '__main__':
    main()
