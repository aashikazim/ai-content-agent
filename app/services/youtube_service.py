
import os
from datetime import datetime

import isodate
from dotenv import load_dotenv
from googleapiclient.discovery import build

from app.models.video import Video

load_dotenv()


class YouTubeService:
    def __init__(self, client=None):
        if client is not None:
            self.client = client
        else:
            api_key = os.getenv("YOUTUBE_API_KEY")
            if not api_key:
                raise ValueError("YOUTUBE_API_KEY is missing")

            self.client = build(
                "youtube", "v3",
                developerKey=api_key
            )

    def get_popular_videos(
        self, region="US", category_id=None, limit=50
    ):
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")

        params = {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": region,
            "maxResults": limit,
        }

        if category_id:
            params["videoCategoryId"] = str(category_id)

        response = self.client.videos().list(
            **params
        ).execute()

        videos = []

        for item in response.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            details = item.get("contentDetails", {})

            duration = isodate.parse_duration(
                details.get("duration", "PT0S")
            ).total_seconds()

            published = datetime.fromisoformat(
                snippet["publishedAt"].replace(
                    "Z", "+00:00"
                )
            )

            videos.append(
                Video(
                    video_id=item["id"],
                    title=snippet["title"],
                    category_id=snippet.get(
                        "categoryId", ""
                    ),
                    channel=snippet["channelTitle"],
                    published_at=published,
                    duration_seconds=int(duration),
                    views=int(stats.get("viewCount", 0)),
                    likes=int(stats.get("likeCount", 0)),
                    comments=int(
                        stats.get("commentCount", 0)
                    ),
                    url=(
                        "https://www.youtube.com/watch?v="
                        + item["id"]
                    ),
                )
            )

        return videos
