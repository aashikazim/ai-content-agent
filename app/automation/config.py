"""Load explicit automation configuration without loading credentials or services."""

import os
import re
import string
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Minute = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
PositiveFloat = Annotated[float, Field(gt=0, strict=True, allow_inf_nan=False)]
ApprovalMode = Literal["manual", "auto_with_gates"]
Platform = Literal["youtube", "facebook"]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class NightlyWindow(ConfigModel):
    start: Minute = "00:30"
    end: Minute = "08:30"

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.start == self.end:
            raise ValueError("GPU window must have distinct start and end times")
        return self


class GPUConfig(ConfigModel):
    degradation_order: list[Literal['shorten_shorts','drop_wildcard','drop_lowest_priority']] = Field(default_factory=lambda:['shorten_shorts','drop_wildcard','drop_lowest_priority'])
    nightly_window: NightlyWindow = Field(default_factory=NightlyWindow)
    seed_seconds_per_video_second: dict[str, PositiveFloat] = Field(
        default_factory=lambda: {"ltx_t2v": 29.0, "ltx_i2v": 20.0}
    )

    @field_validator("seed_seconds_per_video_second")
    @classmethod
    def required_seeds(cls, value: dict[str, float]) -> dict[str, float]:
        if not {"ltx_t2v", "ltx_i2v"} <= value.keys():
            raise ValueError("Both LTX seed modes are required; seeds are not new measurements")
        return value


class Recipe(ConfigModel):
    content_type: Literal["short"] = "short"
    style: Literal["comedy_satire", "news_explainer", "realistic_drama", "animation"]
    duration_s: int = Field(default=45, ge=30, le=60, strict=True)
    aspect: Literal["9:16"] = "9:16"
    provider: Literal["local_cinematic"] = "local_cinematic"
    model: Literal["ltx-2.5", "wan2.2-animate-2", "ltx-wan-hybrid"] = "ltx-2.5"
    input_mode: Literal["text", "image"] = "text"
    # Paid providers still need an owner standing authorization and quote.
    script_provider: Literal["ollama", "openai", "anthropic"] = "ollama"
    script_model: str | None = Field(default=None, min_length=1, max_length=200)
    narration: Literal["local_tts", "model_audio", "character_tts"] = "local_tts"
    captions: Literal["asr", "aligned"] = "asr"
    cast_mode: Literal["none", "approved"] = "none"
    source_bundle: Literal["auto"] | None = None
    speech_profile_id: str | None = Field(default=None,pattern=r"^[A-Za-z0-9_-]{1,80}$")
    title_template: str = Field(default='{title}',min_length=1,max_length=200)
    hashtag_templates: list[str] = Field(default_factory=lambda:['#Shorts','#{category}'],min_length=1,max_length=10)

    @model_validator(mode="after")
    def references_required(self) -> Self:
        for template in [self.title_template,*self.hashtag_templates]:
            for _,field,spec,conversion in string.Formatter().parse(template):
                if field is not None and (field not in {'title','category'} or spec or conversion):
                    raise ValueError('Templates support only plain title/category placeholders')
        if self.model != "ltx-2.5" and (self.input_mode != "image" or self.cast_mode != "approved"):
            raise ValueError("Wan/hybrid recipes require image input and approved cast")
        if self.narration == "character_tts" and self.cast_mode != "approved":
            raise ValueError("Character speech requires approved cast")
        if self.model == "wan2.2-animate-2" and self.narration == "model_audio":
            raise ValueError("Wan does not generate audio")
        if self.style == "news_explainer" and self.source_bundle != "auto":
            raise ValueError("News recipes require an auto-compiled, unreviewed source bundle")
        return self


class LaneConfig(ConfigModel):
    enabled: bool = Field(default=True, strict=True)
    approval_mode: ApprovalMode = "manual"
    platforms: list[Platform] = Field(default_factory=list)

    @field_validator("platforms")
    @classmethod
    def unique_platforms(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("Platforms must be unique")
        return values


class ShortsLaneConfig(LaneConfig):
    kind: Literal["topic_shorts"]
    daily_count: int = Field(default=5, ge=5, le=24, strict=True)
    quotas: dict[Identifier, Annotated[int, Field(ge=0, strict=True)]]
    publish_slots: list[Minute]
    cooldown_days: int = Field(default=14, ge=1, le=365, strict=True)
    recipes: dict[Identifier, Recipe]

    @model_validator(mode="after")
    def complete_slots(self) -> Self:
        if not self.platforms:
            raise ValueError("Shorts require at least one platform")
        if sum(self.quotas.values()) != self.daily_count:
            raise ValueError("Quota total must equal daily_count")
        if set(self.quotas) != set(self.recipes):
            raise ValueError("Every quota category must have exactly one matching recipe")
        if len(self.publish_slots) != self.daily_count or len(set(self.publish_slots)) != self.daily_count:
            raise ValueError("Provide one distinct publish slot per daily Short")
        return self


class EpisodeDuration(ConfigModel):
    min: int = Field(default=300, ge=300, le=600, strict=True)
    max: int = Field(default=600, ge=300, le=600, strict=True)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.min > self.max:
            raise ValueError("Episode minimum must not exceed maximum")
        return self


class PlannerConfig(ConfigModel):
    # Paid providers stay fail-closed until quote-bound standing consent is built.
    provider: Literal["ollama"] = "ollama"
    model: str | None = Field(default=None, min_length=1, max_length=200)


class EpisodeRender(ConfigModel):
    provider: Literal["local_cinematic"] = "local_cinematic"
    model: Literal["ltx-2.5", "wan2.2-animate-2", "ltx-wan-hybrid"] = "ltx-2.5"
    input_mode: Literal["image"] = "image"
    continuity: Literal["anchored", "connected"] = "connected"

    @model_validator(mode="after")
    def wan_carry(self) -> Self:
        if self.model == "wan2.2-animate-2" and self.continuity == "connected":
            raise ValueError("Wan-only cannot use connected LTX carry")
        return self


class StoryLaneConfig(LaneConfig):
    kind: Literal["serialized_story"]
    episodes_per_series: int = Field(default=30, ge=30, le=30, strict=True)
    episode_duration_s: EpisodeDuration = Field(default_factory=EpisodeDuration)
    buffer_ahead_episodes: int = Field(default=3, ge=0, le=29, strict=True)
    platforms: list[Platform] = Field(default_factory=lambda: ["youtube"])
    publish_slot: Minute = "19:00"
    next_series_gap_days: int = Field(default=1, ge=0, le=365, strict=True)
    series_start_mode: Literal["manual_cast_approval", "auto"] = "manual_cast_approval"
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    episode_render: EpisodeRender = Field(default_factory=EpisodeRender)

    @model_validator(mode="after")
    def youtube_required(self) -> Self:
        # Episode order is verified against YouTube; Facebook is an optional
        # long-video (non-Reels) addition used only by the opt-in Governor.
        if "youtube" not in self.platforms:
            raise ValueError("Episodes require YouTube; Facebook long video is optional")
        return self


class CustomLaneConfig(LaneConfig):
    kind: Literal["custom"]
    enabled: bool = Field(default=False, strict=True)
    recipe: dict[str, str] = Field(default_factory=dict)


AnyLaneConfig = Annotated[
    ShortsLaneConfig | StoryLaneConfig | CustomLaneConfig, Field(discriminator="kind")
]


class HoldRule(ConfigModel):
    kind: Literal["keyword", "regex", "llm_judge"]
    expression: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_regex(self) -> Self:
        if self.kind == "regex":
            try:
                re.compile(self.expression)
            except re.error as exc:
                raise ValueError("Invalid hold-rule regular expression") from exc
        return self


class GovernorConfig(ConfigModel):
    hold_rules: list[HoldRule] = Field(default_factory=list, max_length=100)
    wer_max: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    duplicate_window_days: int = Field(default=30, ge=1, le=365, strict=True)
    daily_publish_cap: dict[Platform, PositiveInt] = Field(
        default_factory=lambda: {"youtube": 10, "facebook": 8}
    )


class AutomationConfig(ConfigModel):
    timezone: str = "America/New_York"
    gpu: GPUConfig = Field(default_factory=GPUConfig)
    lanes: dict[Identifier, AnyLaneConfig] = Field(min_length=1, max_length=20)
    governor: GovernorConfig = Field(default_factory=GovernorConfig)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class AutomationSettings(ConfigModel):
    enabled: bool = Field(default=False, strict=True)
    governor_enabled: bool = Field(default=False, strict=True)
    governor_dry_run: bool = Field(default=True, strict=True)
    plan_hour: int = Field(default=0, ge=0, le=23, strict=True)
    plan_minute: int = Field(default=5, ge=0, le=59, strict=True)
    trend_interval_hours: int = Field(default=3, ge=1, le=24, strict=True)
    kill_switch_file: Path = Path("data/automation/KILL")
    daily_budget_usd: Decimal = Field(default=Decimal("0"), ge=0, allow_inf_nan=False)
    # No webhook client exists in Phase 1. Hide the future secret-bearing URL in repr.
    alert_webhook_url: str = Field(default="", repr=False)
    config: AutomationConfig
    # Parsed config/unattended.yaml when its opt-in flag is true; else None.
    unattended: Any = None


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently changing owner intent."""


def _mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise ValueError("Configuration keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_config(path: str | Path = "config/lanes.yaml") -> AutomationConfig:
    with Path(path).open("rb") as source:
        content = source.read(128 * 1024 + 1)
    if len(content) > 128 * 1024:
        raise ValueError("Lane configuration exceeds 128 KiB")
    # No aliases/anchors: bound parsing and avoid recursive/ambiguous recipes.
    try:
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
               for token in yaml.scan(content)):
            raise ValueError("YAML anchors and aliases are not supported")
        value = yaml.load(content, Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid lane YAML") from exc
    return AutomationConfig.model_validate(value)


def _boolean(env: Mapping[str, str], key: str, default: bool) -> bool:
    if key not in env:
        return default
    value = env[key].strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{key} must be true or false")
    return value == "true"


def load_settings(
    path: str | Path | None = None, *, environ: Mapping[str, str] | None = None
) -> AutomationSettings:
    """Explicit load; never reads .env, contacts providers or initializes SQLite."""
    env = os.environ if environ is None else environ
    config = load_config(path or env.get("AUTOMATION_LANES_FILE", "config/lanes.yaml"))
    if env.get("AUTOMATION_TIMEZONE"):
        config = AutomationConfig.model_validate(
            config.model_dump() | {"timezone": env["AUTOMATION_TIMEZONE"]}
        )
    from app.automation.unattended import apply_unattended, load_unattended
    # Two explicit opt-ins: this variable names the file AND the file says enabled.
    unattended = load_unattended(env.get("AUTOMATION_UNATTENDED_FILE") or None)
    if unattended is not None:
        config = apply_unattended(config, unattended)
    return AutomationSettings(
        enabled=_boolean(env, "AUTOMATION_ENABLED", False),
        governor_enabled=_boolean(env, "AUTOMATION_GOVERNOR_ENABLED", False),
        governor_dry_run=_boolean(env, "AUTOMATION_GOVERNOR_DRY_RUN", True),
        plan_hour=int(env.get("AUTOMATION_PLAN_HOUR", "0")),
        plan_minute=int(env.get("AUTOMATION_PLAN_MINUTE", "5")),
        trend_interval_hours=int(env.get("AUTOMATION_TREND_INTERVAL_HOURS", "3")),
        kill_switch_file=Path(env.get("AUTOMATION_KILL_SWITCH_FILE", "data/automation/KILL")),
        # Pydantic supplies a consistent ValidationError for malformed/nonfinite money.
        daily_budget_usd=env.get("AUTOMATION_DAILY_BUDGET_USD", "0"),
        alert_webhook_url=env.get("AUTOMATION_ALERT_WEBHOOK_URL", ""),
        config=config,
        unattended=unattended,
    )
