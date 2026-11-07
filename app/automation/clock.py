"""Explicit UTC time source for deterministic simulated-day tests."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware instant; callers must not use host-local time."""
        ...


def utc_now(clock: Clock) -> datetime:
    value = clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def local_day(clock: Clock, timezone_name: str) -> date:
    return utc_now(clock).astimezone(ZoneInfo(timezone_name)).date()


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FrozenClock:
    instant: datetime

    def __post_init__(self) -> None:
        utc_now(self)

    def now(self) -> datetime:
        return self.instant
