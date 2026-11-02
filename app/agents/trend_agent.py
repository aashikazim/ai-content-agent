
from app.services.youtube_service import YouTubeService
from app.services.ranking_service import rank_videos
from app.services.storage_service import StorageService


# Common YouTube video category IDs
CATEGORIES = {
    "all": None,
    "entertainment": "24",
    "education": "27",
    "science": "28",
    "gaming": "20",
    "news": "25",
    "sports": "17",
    "music": "10",
}


class TrendAgent:
    def __init__(self, youtube=None, storage=None):
        self.youtube = youtube or YouTubeService()
        self.storage = storage or StorageService()

    def discover(
        self,
        region="US",
        category="all",
        limit=20,
        short_form=False
    ):
        if category not in CATEGORIES:
            raise ValueError(
                f"Unknown category: {category}"
            )

        videos = self.youtube.get_popular_videos(
            region=region,
            category_id=CATEGORIES[category],
            limit=limit
        )

        if short_form:
            videos = [
                video for video in videos
                if 0 < video.duration_seconds <= 180
            ]

        ranked = rank_videos(videos)
        self.storage.save_videos(ranked)

        return ranked
