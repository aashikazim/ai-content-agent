
import pytest
from pydantic import ValidationError

from app.models.content_plan import ContentPlan


def valid_data():
    return {
        "topic": "Science topic",
        "category": "science",
        "title": "An Original Science Video",
        "description": "An educational short video.",
        "hashtags": ["#Science"],
        "hook": "Have you ever wondered why?",
        "target_audience": "General audience",
        "scenes": [
            {
                "scene_number": 1,
                "duration_seconds": 15,
                "narration": "Introduction",
                "visual_prompt": "Science diagram"
            },
            {
                "scene_number": 2,
                "duration_seconds": 30,
                "narration": "Explanation",
                "visual_prompt": "Animated explanation"
            }
        ]
    }


def test_valid_plan():
    plan = ContentPlan.model_validate(
        valid_data()
    )

    assert plan.total_duration == 45
    assert plan.review_status == "draft"


def test_invalid_duration():
    data = valid_data()
    data["scenes"][1]["duration_seconds"] = 5

    with pytest.raises(ValidationError):
        ContentPlan.model_validate(data)


def test_invalid_scene_numbers():
    data = valid_data()
    data["scenes"][1]["scene_number"] = 3

    with pytest.raises(ValidationError):
        ContentPlan.model_validate(data)
