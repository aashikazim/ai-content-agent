
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.agents.content_agent import ContentAgent
from app.models.video import Video
from app.models.content_plan import ContentPlan
from tests.test_content_plan import valid_data


def test_agent_generates_plan():
    video = Video(
        video_id="test123",
        title="A Science Trend",
        category_id="28",
        channel="Test Channel",
        published_at=datetime.now(timezone.utc),
        duration_seconds=50,
        views=100000,
        likes=5000,
        comments=100,
        url="https://youtube.com/watch?v=test123",
        score=3.5
    )

    mock_llm = Mock()
    mock_llm.generate_plan.return_value = (
        ContentPlan.model_validate(valid_data())
    )

    agent = ContentAgent(llm=mock_llm)
    plan = agent.create_plan(
        [video], category="science"
    )

    assert plan.total_duration == 45
    assert plan.source_video_ids == ["test123"]
    assert plan.review_status == "draft"

    mock_llm.generate_plan.assert_called_once()


def test_empty_video_list():
    agent = ContentAgent(llm=Mock())

    with pytest.raises(ValueError):
        agent.create_plan([])
