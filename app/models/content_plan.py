
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Scene(BaseModel):
    scene_number: int = Field(ge=1)
    duration_seconds: int = Field(ge=1, le=60)
    narration: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)
    on_screen_text: str = ""
    visual_type: Literal[
        "generated_image",
        "text_card",
        "diagram"
    ] = "generated_image"


class ContentPlan(BaseModel):
    topic: str = Field(min_length=1)
    category: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    hashtags: list[str] = Field(min_length=1, max_length=10)
    hook: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    language: str = "English"
    scenes: list[Scene] = Field(min_length=1, max_length=12)
    source_video_ids: list[str] = Field(default_factory=list)
    review_status: Literal[
        "draft", "approved", "rejected"
    ] = "draft"

    @model_validator(mode="after")
    def validate_scenes(self):
        numbers = [
            scene.scene_number for scene in self.scenes
        ]

        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(
                "Scene numbers must be sequential"
            )

        total = sum(
            scene.duration_seconds
            for scene in self.scenes
        )

        if not 30 <= total <= 60:
            raise ValueError(
                "Video duration must be 30-60 seconds"
            )

        return self

    @property
    def total_duration(self):
        return sum(
            scene.duration_seconds
            for scene in self.scenes
        )
