
from app.services.narration_service import NarrationService
from app.models.content_plan import ContentPlan


def test_word_budget():
    service = NarrationService(
        words_per_minute=160
    )

    assert service.max_words(6) == 14
    assert service.max_words(10) == 24
    assert service.max_words(15) == 36


def test_duration_estimation():
    service = NarrationService(
        words_per_minute=160
    )

    text = " ".join(["hello"] * 16)

    assert service.count_words(text) == 16
    assert service.estimate_duration(text) == 6.0


def test_long_narration_detected():
    plan = ContentPlan.model_validate({
        "topic": "Science",
        "category": "science",
        "title": "Science Video",
        "description": "A science video",
        "hashtags": ["#Science"],
        "hook": "Interesting science",
        "target_audience": "General audience",
        "scenes": [
            {
                "scene_number": 1,
                "duration_seconds": 6,
                "narration": " ".join(
                    ["word"] * 25
                ),
                "visual_prompt": "Science illustration"
            },
            {
                "scene_number": 2,
                "duration_seconds": 24,
                "narration": "A short explanation.",
                "visual_prompt": "Another illustration"
            }
        ]
    })

    service = NarrationService()

    issues = service.validate_plan(plan)

    assert len(issues) == 1
    assert issues[0].scene_number == 1
    assert issues[0].actual_words == 25
    assert issues[0].allowed_words == 14
