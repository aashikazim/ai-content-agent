
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.agents.trend_agent import TrendAgent
from app.models.video import Video


def make_video(video_id, duration):
    return Video(
        video_id=video_id,
        title="Test Video",
        category_id="24",
        channel="Test Channel",
        published_at=datetime.now(timezone.utc),
        duration_seconds=duration,
        views=1000,
        likes=100,
        comments=10,
        url=f"https://youtube.com/watch?v={video_id}"
    )


def test_short_form_filter():
    youtube = Mock()
    storage = Mock()

    youtube.get_popular_videos.return_value = [
        make_video("short", 45),
        make_video("long", 600)
    ]

    agent = TrendAgent(
        youtube=youtube,
        storage=storage
    )

    results = agent.discover(
        category="entertainment",
        short_form=True
    )

    assert len(results) == 1
    assert results[0].video_id == "short"

    youtube.get_popular_videos.assert_called_once_with(
        region="US",
        category_id="24",
        limit=20
    )

    storage.save_videos.assert_called_once()


def test_invalid_category():
    agent = TrendAgent(
        youtube=Mock(),
        storage=Mock()
    )

    with pytest.raises(ValueError):
        agent.discover(category="invalid")
