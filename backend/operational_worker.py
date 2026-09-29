"""Dedicated operational ML worker entrypoint.

Run as a separate process (for example, one systemd/container worker), never
as part of a multi-worker FastAPI process.
"""

from backend.app.services.runtime_ml import build_operational_cycle_service
from backend.app.services.operational_cycle import OperationalForecastWorker


def main() -> None:
    service = build_operational_cycle_service()
    OperationalForecastWorker(service).run_forever()


if __name__ == "__main__":
    main()
