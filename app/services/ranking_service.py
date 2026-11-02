
import math
from datetime import datetime, timezone


def calculate_score(video, now=None):
    now = now or datetime.now(timezone.utc)

    age_hours = max(
        (now - video.published_at).total_seconds()
        / 3600,
        1.0
    )

    views_per_hour = video.views / age_hours

    engagement = (
        (video.likes + video.comments)
        / max(video.views, 1)
    )

    recency = 1 / (1 + age_hours / 24)

    score = (
        0.5 * math.log10(1 + views_per_hour)
        + 0.3 * (100 * engagement)
        + 0.2 * recency
    )

    return round(score, 4)


def rank_videos(videos, now=None):
    for video in videos:
        video.score = calculate_score(video, now)

    return sorted(
        videos,
        key=lambda video: video.score,
        reverse=True
    )
