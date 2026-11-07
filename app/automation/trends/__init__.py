"""Topic signals only: no article scraping or third-party video ingestion."""
from datetime import datetime
from typing import Any, Protocol
from pydantic import BaseModel, ConfigDict, Field, field_validator


class TrendSignal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: str = Field(min_length=1, max_length=50)
    external_id: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=500)
    category_hint: str | None = None
    score: float = Field(default=0, ge=0, allow_inf_nan=False)
    velocity: float = Field(default=0, ge=0, allow_inf_nan=False)
    timestamp: datetime
    url: str = ''
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator('timestamp')
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None: raise ValueError('Signal time must be aware')
        return value


class TrendSource(Protocol):
    name: str
    def collect(self, region: str) -> list[TrendSignal]: ...
