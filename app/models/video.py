
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Video:
    video_id: str
    title: str
    category_id: str
    channel: str
    published_at: datetime
    duration_seconds: int
    views: int
    likes: int
    comments: int
    url: str
    score: float = 0.0
