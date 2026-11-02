
from datetime import datetime, timedelta, timezone

from app.models.video import Video
from app.services.ranking_service import (
    calculate_score, rank_videos
)


NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def make_video(video_id, views, age_hours):
    return Video(
        video_id=video_id,
        title=f"Video {video_id}",
        category_id="28",
        channel="Test Channel",
        published_at=NOW - timedelta(hours=age_hours),
        duration_seconds=45,
        views=views,
        likes=0,
        comments=0,
        url=f"https://youtube.com/watch?v={video_id}"
    )


def test_recent_video_has_higher_score():
    recent = make_video("a", 10000, 2)
    old = make_video("b", 10000, 200)

    assert calculate_score(
        recent, NOW
    ) > calculate_score(old, NOW)


def test_rank_videos():
    recent = make_video("a", 10000, 2)
    old = make_video("b", 10000, 200)

    ranked = rank_videos([old, recent], NOW)

    assert ranked[0].video_id == "a"
    assert ranked[1].video_id == "b"


def test_zero_views():
    video = make_video("zero", 0, 1)

    assert calculate_score(video, NOW) >= 0
