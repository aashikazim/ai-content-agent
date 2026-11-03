"""A short-form script draft alongside the unchanged legacy content plan."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.content_plan import ContentPlan
from app.services.narration_service import NarrationService


class CharacterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    character_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    voice_notes: str = Field(default="", max_length=500)


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    scene_number: int = Field(ge=1, le=12)
    character_id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=1000)


class ProductionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    content_type: Literal["realistic_short", "short_drama", "animated_comedy", "news"]
    factual_status: Literal["fiction", "satire", "unverified"] = "unverified"
    outline: list[str] = Field(min_length=1, max_length=12)
    characters: list[CharacterProfile] = Field(default_factory=list, max_length=3)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=100)
    content_plan: ContentPlan

    @model_validator(mode="after")
    def validate_screenplay(self):
        if self.content_plan.review_status != "draft":
            raise ValueError("Generated scripts must remain drafts")
        ids = [character.character_id for character in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("Character IDs must be unique")
        if self.content_type == "short_drama" and not 2 <= len(ids) <= 3:
            raise ValueError("Short drama requires two or three fictional characters")
        if self.content_type == "short_drama" and self.factual_status != "fiction":
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
        if self.content_type == "short_drama" and not self.dialogue:
            raise ValueError("Short drama requires dialogue")
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
    schema["$defs"]["ContentPlan"]["properties"]["review_status"] = {
        "type": "string", "const": "draft"
    }
    return schema
