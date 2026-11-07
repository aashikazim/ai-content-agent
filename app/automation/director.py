"""Planning-only skeleton. No service, queue, publisher or model imports."""

from dataclasses import dataclass
from datetime import date
from typing import Literal, Mapping

from app.automation.clock import Clock, SystemClock, local_day, utc_now
from app.automation.config import AutomationSettings
from app.automation.lanes import Lane, LaneContext, PlannedItem


@dataclass(frozen=True)
class LanePreview:
    lane_id: str
    status: Literal["automation_disabled", "lane_disabled", "not_implemented", "preview"]
    description: str
    items: tuple[PlannedItem, ...] = ()


@dataclass(frozen=True)
class DayPreview:
    plan_date: date
    timezone: str
    lanes: tuple[LanePreview, ...]


class Director:
    def __init__(
        self, settings: AutomationSettings, *, clock: Clock | None = None,
        lanes: Mapping[str, Lane] | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock if clock is not None else SystemClock()
        # Explicit kind registry, not dynamic imports from YAML.
        self.lanes = dict(lanes or {})

    def plan_day(self, plan_date: date | None = None) -> DayPreview:
        """Inert by default; pure previews only for explicitly registered recipes."""
        timezone_name = self.settings.config.timezone
        day = plan_date if plan_date is not None else local_day(self.clock, timezone_name)
        results = []
        for lane_id, config in self.settings.config.lanes.items():
            lane = self.lanes.get(config.kind)
            if not self.settings.enabled:
                status, description, items = "automation_disabled", "Automation is disabled", ()
            elif not config.enabled:
                status, description, items = "lane_disabled", "Lane is disabled", ()
            elif lane is None:
                status, description, items = "not_implemented", "Recipe is not implemented in Phase 1", ()
            else:
                context = LaneContext(lane_id, day, utc_now(self.clock), config)
                status, description, items = "preview", lane.describe(), lane.plan_day(context)
            results.append(LanePreview(lane_id, status, description, items))
        return DayPreview(day, timezone_name, tuple(results))

    def run_day(self, plan_date: date | None = None) -> None:
        """Fail closed even if env flags are enabled; dispatch belongs to Phase 3."""
        raise NotImplementedError("Phase 1 does not dispatch generation, approve or publish")
