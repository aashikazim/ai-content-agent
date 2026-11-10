"""Owner-facing unattended mode: one small file overlays the validated lane config.

Opt-in only (``enabled: true`` in config/unattended.yaml). It removes routine
dashboard steps, never the internal safeguards: publication uniqueness, publish
slots, daily caps, reconciliation holds, kill switches and quote-bound spending
all stay in the existing Governor/publishing/authorization code.
"""
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Minute = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
Platform = Literal["youtube", "facebook"]
PROVIDERS = {"ollama": "ollama", "openai": "openai", "claude": "anthropic", "anthropic": "anthropic"}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class Scripts(Model):
    provider: Literal["ollama", "openai", "claude", "anthropic"] = "ollama"
    model: str | None = Field(default=None, min_length=1, max_length=200)
    # A paid provider that is unauthorized, over budget or known-failed.
    if_unavailable: Literal["ollama", "skip"] = "ollama"

    @property
    def internal(self) -> str:
        return PROVIDERS[self.provider]


class Shorts(Model):
    per_day: int = Field(default=5, ge=5, le=10, strict=True)
    platforms: list[Platform] = Field(default_factory=lambda: ["youtube", "facebook"], min_length=1)
    first_slot: Minute = "09:00"
    last_slot: Minute = "21:00"

    @model_validator(mode="after")
    def spaced(self):
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("Platforms must be unique")
        if _minutes(self.last_slot) - _minutes(self.first_slot) < (self.per_day - 1) * 20:
            raise ValueError("Leave at least 20 minutes between Shorts publish slots")
        return self


class Series(Model):
    enabled: bool = Field(default=True, strict=True)
    platforms: list[Platform] = Field(default_factory=lambda: ["youtube", "facebook"], min_length=1)
    publish_slot: Minute = "19:00"
    genre: str = Field(default="mystery drama", min_length=3, max_length=100)
    # Generate and freeze a synthetic cast with local SDXL; no upload or click.
    auto_cast: bool = Field(default=True, strict=True)
    # Give up on a locally failed/stuck episode and continue the story in order.
    auto_skip_stuck_after_hours: int = Field(default=24, ge=1, le=168, strict=True)

    @model_validator(mode="after")
    def youtube(self):
        if "youtube" not in self.platforms or len(set(self.platforms)) != len(self.platforms):
            raise ValueError("Series need YouTube (episode order is verified there); Facebook is optional")
        return self


class UnattendedConfig(Model):
    enabled: bool = Field(default=False, strict=True)
    scripts: Scripts = Field(default_factory=Scripts)
    shorts: Shorts = Field(default_factory=Shorts)
    series: Series = Field(default_factory=Series)
    # Read-only platform status checks that turn uploaded into verified published.
    publication_check_minutes: int = Field(default=15, ge=5, le=240, strict=True)


def _minutes(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


def load_unattended(path: str | Path | None) -> UnattendedConfig | None:
    """Return the parsed file only when it exists and opts in; else None."""
    if not path or not Path(path).is_file():
        return None
    content = Path(path).read_bytes()
    if len(content) > 64 * 1024:
        raise ValueError("Unattended configuration exceeds 64 KiB")
    try:
        value = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ValueError("Invalid unattended YAML") from exc
    config = UnattendedConfig.model_validate(value)
    return config if config.enabled else None


def publish_slots(first: str, last: str, count: int) -> list[str]:
    start, end = _minutes(first), _minutes(last)
    step = (end - start) / (count - 1)
    values = [int(round((start + step * index) / 5) * 5) for index in range(count)]
    for index in range(1, count):  # Rounding must never merge two slots.
        values[index] = max(values[index], values[index - 1] + 5)
    return [f"{value // 60:02d}:{value % 60:02d}" for value in values]


def scaled_quotas(quotas: dict[str, int], count: int) -> dict[str, int]:
    """Keep the owner's category mix; largest remainders receive extra slots."""
    total = sum(quotas.values())
    exact = {key: value * count / total for key, value in quotas.items()}
    result = {key: int(value) for key, value in exact.items()}
    order = sorted(quotas, key=lambda key: (result[key] - exact[key], list(quotas).index(key)))
    for key in order[: count - sum(result.values())]:
        result[key] += 1
    return result


def apply_unattended(config: Any, unattended: UnattendedConfig) -> Any:
    """Overlay onto AutomationConfig and revalidate it with the strict schema."""
    from app.automation.config import AutomationConfig, ShortsLaneConfig, StoryLaneConfig
    value = config.model_dump(mode="json")
    caps = value["governor"]["daily_publish_cap"]
    needed = {"youtube": 0, "facebook": 0}
    for identifier, lane in config.lanes.items():
        target = value["lanes"][identifier]
        if isinstance(lane, ShortsLaneConfig) and lane.enabled:
            shorts = unattended.shorts
            target.update(daily_count=shorts.per_day, quotas=scaled_quotas(lane.quotas, shorts.per_day),
                publish_slots=publish_slots(shorts.first_slot, shorts.last_slot, shorts.per_day),
                platforms=list(shorts.platforms), approval_mode="auto_with_gates")
            for recipe in target["recipes"].values():
                recipe.update(script_provider=unattended.scripts.internal, script_model=unattended.scripts.model)
            for platform in shorts.platforms:
                needed[platform] += shorts.per_day
        elif isinstance(lane, StoryLaneConfig):
            series = unattended.series
            target.update(enabled=lane.enabled and series.enabled, platforms=list(series.platforms),
                publish_slot=series.publish_slot, approval_mode="auto_with_gates",
                series_start_mode="auto" if series.auto_cast else lane.series_start_mode)
            if target["enabled"]:
                for platform in series.platforms:
                    needed[platform] += 1
    for platform, count in needed.items():
        if count:
            caps[platform] = max(caps.get(platform, 0), count)
    return AutomationConfig.model_validate(value)


def active(settings: Any) -> UnattendedConfig | None:
    return getattr(settings, "unattended", None)


def stuck_cutoff(clock: Any, hours: int) -> str:
    from app.automation.clock import utc_now
    return (utc_now(clock) - timedelta(hours=hours)).isoformat()
