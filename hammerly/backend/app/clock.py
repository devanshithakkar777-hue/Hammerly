"""Single source of time so tests can control it."""
from datetime import datetime, timedelta, timezone

_offset = timedelta(0)


def now() -> datetime:
    return datetime.now(timezone.utc) + _offset


def advance(seconds: float) -> None:
    global _offset
    _offset += timedelta(seconds=seconds)


def reset() -> None:
    global _offset
    _offset = timedelta(0)
