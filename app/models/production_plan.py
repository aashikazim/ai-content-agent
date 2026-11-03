"""A short-form script draft alongside the unchanged legacy content plan."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.content_plan import ContentPlan
from app.models.long_content_plan import LongContentPlan
from app.services.narration_service import NarrationService


class CharacterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    character_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    voice_notes: str = Field(default="", max_length=500)


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    scene_number: int = Field(ge=1, le=240)
    character_id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=1000)


class PlannedShot(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    shot_number: int = Field(ge=1, le=240)
    scene_number: int = Field(ge=1, le=240)
    duration_seconds: int = Field(ge=2, le=30, strict=True)
    action: str = Field(min_length=1, max_length=1500)
    camera: str = Field(min_length=1, max_length=500)
    spoken_text: str = Field(default="", max_length=1500)
    character_ids: list[str] = Field(default_factory=list, max_length=3)


class ProductionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    content_type: Literal["realistic_short", "short_drama", "animated_comedy", "news", "long_drama"]
    factual_status: Literal["fiction", "satire", "unverified"] = "unverified"
    outline: list[str] = Field(min_length=1, max_length=240)
    characters: list[CharacterProfile] = Field(default_factory=list, max_length=3)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=1200)
    shots: list[PlannedShot] = Field(default_factory=list, max_length=240)
    content_plan: ContentPlan | LongContentPlan

    @model_validator(mode="after")
    def validate_screenplay(self):
        if self.content_plan.review_status != "draft":
            raise ValueError("Generated scripts must remain drafts")
        long = self.content_type == 'long_drama'
        if long != isinstance(self.content_plan, LongContentPlan):
            raise ValueError('Only long_drama may use a long-form content plan')
        if not long and (len(self.shots) > 12 or len(self.outline) > 12 or len(self.dialogue) > 100):
            raise ValueError('Short-form production limits exceeded')
        ids = [character.character_id for character in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("Character IDs must be unique")
        if self.content_type in {"short_drama", "long_drama"} and not 2 <= len(ids) <= 3:
            raise ValueError("Short drama requires two or three fictional characters")
        if self.content_type in {"short_drama", "long_drama"} and self.factual_status != "fiction":
            raise ValueError("Drama must be labelled fiction")
        if self.content_type == "news" and self.factual_status != "unverified":
            raise ValueError("News drafts require research and cannot claim factual verification")
        if self.content_type == "animated_comedy" and self.factual_status not in {"fiction", "satire"}:
            raise ValueError("Animated comedy must be labelled fiction or satire")
        scenes = {scene.scene_number: scene for scene in self.content_plan.scenes}
        for line in self.dialogue:
            if line.character_id not in ids or line.scene_number not in scenes:
                raise ValueError("Dialogue must reference an existing character and scene")
            if line.text.casefold() not in scenes[line.scene_number].narration.casefold():
                raise ValueError("All dialogue must be included in its scene's spoken narration")
        if self.content_type in {"short_drama", "long_drama"} and not self.dialogue:
            raise ValueError("Short drama requires dialogue")
        if self.shots:
            if [shot.shot_number for shot in self.shots] != list(range(1, len(self.shots) + 1)):
                raise ValueError("Shot numbers must be sequential")
            if [shot.scene_number for shot in self.shots] != sorted(shot.scene_number for shot in self.shots):
                raise ValueError("Shots must follow scene order")
            for shot in self.shots:
                if shot.scene_number not in scenes or not set(shot.character_ids).issubset(ids):
                    raise ValueError("Shots must reference existing scenes and characters")
            import re
            words = lambda text: re.findall(r"\w+", text.casefold())
            for number, scene in scenes.items():
                selected = [shot for shot in self.shots if shot.scene_number == number]
                if sum(shot.duration_seconds for shot in selected) != scene.duration_seconds:
                    raise ValueError("Shot durations must cover each scene exactly")
                if words(" ".join(shot.spoken_text for shot in selected)) != words(scene.narration):
                    raise ValueError("Shot speech must preserve the complete scene narration in order")
        return self

    def validate_configuration(self, config):
        if self.content_type != config.content_type:
            raise ValueError("The script does not match the selected content type")
        if self.content_plan.total_duration != config.duration_seconds:
            raise ValueError("The script does not match the selected duration")
        if len(self.content_plan.scenes) > config.max_shots:
            raise ValueError("The script exceeds the selected scene limit")
        if NarrationService().validate_plan(self.content_plan):
            raise ValueError("The script exceeds scene narration word budgets; request a shorter revision")
        if config.video_provider in {"google", "runway"}:
            if config.video_provider == "runway":
                from app.services.runway_models import validate
                for shot in self.shots:
                    validate(config, shot.duration_seconds)
            elif any(not 3 <= shot.duration_seconds <= 10 for shot in self.shots):
                raise ValueError("Gemini shots must be 3–10 seconds")
            if self.content_type == "animated_comedy" and not self.characters:
                raise ValueError("Animated character video requires at least one designed character")
            if config.narration == "model_audio" and any(NarrationService().count_words(shot.spoken_text) > NarrationService().max_words(shot.duration_seconds) for shot in self.shots):
                raise ValueError("Shot dialogue exceeds its spoken word budget")
            if not self.shots:
                raise ValueError("Cloud video needs a shot plan; request a script revision with model-supported shots")
            if len(self.shots) > config.max_shots:
                raise ValueError("The shot plan exceeds the selected shot limit")
        return self


def strict_production_schema():
    """Responses strict schemas require every property and closed objects."""
    schema = ProductionPlan.model_json_schema()

    def close(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                close(child)
        elif isinstance(value, list):
            for child in value:
                close(child)

    close(schema)
    for name in ('ContentPlan', 'LongContentPlan'):
        schema["$defs"][name]["properties"]["review_status"] = {"type": "string", "const": "draft"}
    return schema
