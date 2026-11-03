"""Versioned, credential-free settings shared by all generation interfaces."""

import os
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_IMAGE_MODEL = "stable-diffusion-v1-5/stable-diffusion-v1-5"


class GenerationConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, revalidate_instances="always",
        validate_default=True,
    )

    schema_version: Literal[1] = 1
    content_type: Literal[
        "realistic_short", "short_drama", "animated_comedy", "long_drama", "news"
    ] = "realistic_short"
    script_provider: Literal["ollama", "openai"] = "ollama"
    script_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        min_length=1, max_length=200,
    )
    video_provider: Literal["local", "google", "kling", "runway"] = "local"
    video_model: str = Field(default="local-pipeline", min_length=1, max_length=200)
    video_input_mode: Literal["text", "image"] = "text"
    start_image_reference_id: UUID | None = None
    shot_image_reference_ids: dict[int, UUID] = Field(default_factory=dict, max_length=240)
    image_mode: Literal["text", "diffusion"] = "text"
    image_model: str = Field(
        default_factory=lambda: os.getenv("IMAGE_MODEL_ID", DEFAULT_IMAGE_MODEL),
        min_length=1, max_length=200,
    )
    duration_seconds: int = Field(default=45, ge=30, le=1200, strict=True)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    visual_style: Literal[
        "realistic", "cinematic", "2d_cartoon", "3d_animation",
        "comic_book", "caricature", "sketch",
    ] = "cinematic"
    resolution: Literal["720p", "1080p"] = "1080p"
    fps: Literal[24, 30] = 30
    narration: Literal["local_tts", "model_audio"] = "local_tts"
    source_mode: Literal["trends", "topic", "script"] = "trends"
    topic: str = Field(default="", max_length=1000)
    supplied_script: str = Field(default="", max_length=20000)
    character_reference_ids: list[UUID] = Field(default_factory=list, max_length=3)
    captions: Literal["scene_text", "timed"] = "scene_text"
    max_shots: int = Field(default=12, ge=1, le=240, strict=True)
    max_cost_usd: Decimal = Field(default=Decimal("10"), ge=0, le=1000)
    require_motion: bool = Field(default=False, strict=True)
    paid_generation_confirmed: bool = Field(default=False, strict=True)

    @field_validator("script_model", "video_model", "image_model")
    @classmethod
    def validate_model_identifier(cls, value):
        if any(character.isspace() or not character.isprintable() for character in value):
            raise ValueError("Model identifiers cannot contain whitespace or control characters")
        return value

    @model_validator(mode="after")
    def validate_reference_ids(self):
        limit = 240 if self.content_type == 'long_drama' else 12
        if self.max_shots > limit or len(self.shot_image_reference_ids) > limit:
            raise ValueError(f'This content type supports at most {limit} shots')
        if any(not 1 <= key <= limit for key in self.shot_image_reference_ids):
            raise ValueError(f"Shot image keys must be shot numbers 1–{limit}")
        if len(set(self.character_reference_ids)) != len(self.character_reference_ids):
            raise ValueError("Character reference IDs must be unique")
        return self

    @classmethod
    def from_legacy(cls, image_mode="text"):
        """Map an existing job request without enabling any new provider."""
        return cls(image_mode=image_mode)

    @property
    def output_dimensions(self) -> tuple[int, int]:
        short, long = (720, 1280) if self.resolution == "720p" else (1080, 1920)
        if self.aspect_ratio == "16:9":
            return long, short
        if self.aspect_ratio == "1:1":
            return short, short
        return short, long
