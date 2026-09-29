import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EVENT_SOURCE_TIMEZONE_NAME = os.getenv("EVENT_SOURCE_TIMEZONE", "Europe/Moscow")
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://localhost:5173,"
    "http://127.0.0.1:3000,http://127.0.0.1:5173"
)
CORS_ALLOWED_ORIGINS = [
    value.strip()
    for value in os.getenv("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    if value.strip()
]

try:
    EVENT_SOURCE_TIMEZONE = ZoneInfo(EVENT_SOURCE_TIMEZONE_NAME)
except ZoneInfoNotFoundError as error:
    raise RuntimeError(f"Unknown EVENT_SOURCE_TIMEZONE: {EVENT_SOURCE_TIMEZONE_NAME}") from error


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize API input; naive input is interpreted as source-local time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=EVENT_SOURCE_TIMEZONE)
    return value.astimezone(timezone.utc)
