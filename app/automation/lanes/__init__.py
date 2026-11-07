"""Lane contract; concrete recipes are implemented in later approved phases."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from app.automation.config import AnyLaneConfig


@dataclass(frozen=True)
class LaneContext:
    lane_id: str
    plan_date: date
    now: datetime
    config: AnyLaneConfig


@dataclass(frozen=True)
class PlannedItem:
    slot: int
    title: str


@runtime_checkable
class Lane(Protocol):
    """Director plans/produces; only a later Governor consumes publishable IDs.

    plan_day is pure: no network calls, durable claims, jobs or publishing.
    produce implementations must use atomic storage reservations and existing
    quote/coordinator paths. Neither method may approve or publish.
    """

    def plan_day(self, context: LaneContext) -> tuple[PlannedItem, ...]: ...

    def produce(self, context: LaneContext, item: PlannedItem) -> str: ...

    def publishable(self, context: LaneContext) -> tuple[str, ...]: ...

    def describe(self) -> str: ...
