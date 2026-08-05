"""UTC datetime utilities — single source of truth for the whole package."""

from datetime import datetime, timezone
from typing import Optional


def to_naive_utc(value) -> datetime:
    """Convert any timestamp representation to a naive UTC datetime.

    Accepts: ISO string, int/float epoch seconds, or datetime (aware or naive-UTC).
    Strips tzinfo so callers can compare datetimes without mixing aware/naive.
    """
    if value is None:
        raise ValueError("to_naive_utc received None")

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def parse_optional_dt(value) -> Optional[datetime]:
    """Parse an API datetime field to naive UTC, or return None if missing."""
    if not value:
        return None
    return to_naive_utc(value)


def utc_timestamp(value: datetime) -> int:
    """Convert a datetime to Unix seconds, treating naive values as UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def now_utc() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)
