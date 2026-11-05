"""Versioned contracts for ordered clip items and future timeline editing."""
import re
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    video_model: Literal['ltx', 'wan', 'hybrid'] = 'ltx'
    planner_provider: Literal['anthropic', 'openai'] = 'anthropic'
    planner_model: str = Field(min_length=1, max_length=100)
    clip_count: int = Field(default=3, ge=1, le=12, strict=True)
    duration_seconds: int = Field(default=5, ge=2, le=300, strict=True)
    aspect_ratio: Literal['16:9', '9:16', '1:1'] = '16:9'
    seed: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    backend: Literal['native', 'comfyui'] = 'native'
    continuity: Literal['anchored', 'connected'] = 'anchored'
    cast_package_id: UUID | None = None
    paid_planning_confirmed: bool = Field(default=False, strict=True)
    narration: Literal['model', 'source', 'character_tts'] = 'model'
    whisper_model: Literal['tiny', 'base', 'small', 'medium'] = 'base'
    reattach_audio: bool = Field(default=False, strict=True)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    request_id: UUID
    instruction: str = Field(min_length=1, max_length=5000)
    source_kind: Literal['youtube', 'video', 'images']
    source_url: str | None = Field(default=None, max_length=2048)
    rights_confirmed: bool = Field(default=False, strict=True)
    settings: Settings

    @field_validator('instruction')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Enter an instruction')
        return value.strip()

    @model_validator(mode='after')
    def validate_source(self):
        if self.rights_confirmed is not True:
            raise ValueError('Confirm that you have rights to use this source')
        if self.source_kind == 'youtube':
            self.source_url = youtube_url(self.source_url or '')
        elif self.source_url:
            raise ValueError('URLs apply only to YouTube sources')
        return self


def youtube_url(value):
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or url.username or url.password or url.port not in (None, 443):
            raise ValueError
        host = (url.hostname or '').lower()
        if host == 'youtu.be':
            identifier = url.path.removeprefix('/')
        elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
            identifier = parse_qs(url.query).get('v', [''])[0] if url.path == '/watch' else url.path.removeprefix('/shorts/') if url.path.startswith('/shorts/') else ''
        else:
            raise ValueError
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Use an HTTPS YouTube video or Shorts link; playlists and other hosts are unsupported') from None
    return 'https://www.youtube.com/watch?v=' + identifier


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    character_id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=1000)


class PlannedClip(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    source_start: float | None = Field(..., strict=True, ge=0, allow_inf_nan=False)
    source_end: float | None = Field(..., strict=True, ge=0, allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=150)
    generation_prompt: str = Field(min_length=1, max_length=6000)
    negative_prompt: str = Field(max_length=2000)
    style_notes: str = Field(max_length=2000)
    recommended_model: Literal['ltx', 'wan', 'hybrid']
    duration_seconds: int = Field(ge=2, le=300, strict=True)
    reference_mode: Literal['video-to-video', 'image-to-video']

    character_ids: list[str] = Field(default_factory=list, max_length=3)
    transition: Literal['cut', 'continue'] = 'cut'
    dialogue: list['DialogueLine'] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def range_is_valid(self):
        if (self.source_start is None) != (self.source_end is None):
            raise ValueError('Supply both source timestamps or neither for images')
        if self.source_start is not None and not (0 <= self.source_start < self.source_end < float('inf')):
            raise ValueError('Source range must be finite, positive and ordered')
        if self.reference_mode == 'video-to-video' and self.source_start is None:
            raise ValueError('Video references require source timestamps')
        return self


class ClipPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    clips: list[PlannedClip] = Field(min_length=1, max_length=12)
