"""Long-form plan variant; the legacy ContentPlan keeps its 30–60s contract."""
from pydantic import Field, model_validator

from app.models.content_plan import ContentPlan, Scene


class LongContentPlan(ContentPlan):
    scenes: list[Scene] = Field(min_length=2, max_length=600)

    @model_validator(mode='after')
    def validate_scenes(self):
        if [scene.scene_number for scene in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError('Scene numbers must be sequential')
        if not 120 <= self.total_duration <= 1800:
            raise ValueError('Long drama duration must be 120–1800 seconds')
        return self


def parse_content_plan(value):
    """Read saved plans without changing existing short-form validation."""
    if isinstance(value, dict) and value.get('plan_kind') == 'clip_studio':
        from app.services.clip_studio.review import ClipReviewPlan
        return ClipReviewPlan.model_validate(value)
    try:
        return ContentPlan.model_validate(value)
    except ValueError:
        return LongContentPlan.model_validate(value)
