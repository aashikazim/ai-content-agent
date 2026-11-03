
from datetime import date, timedelta

from googleapiclient.discovery import build

from app.services.youtube_publisher import YouTubePublisher


class YouTubeAnalyticsService:
    def __init__(
        self,
        youtube_client=None,
        analytics_client=None
    ):
        self.youtube_client = youtube_client
        self.analytics_client = analytics_client

    def get_youtube_client(self):
        if self.youtube_client is None:
            self.youtube_client = (
                YouTubePublisher().get_client()
            )

        return self.youtube_client

    def get_analytics_client(self):
        if self.analytics_client is None:
            youtube = self.get_youtube_client()

            # Reuse credentials from the authorized
            # YouTube client.
            credentials = youtube._http.credentials

            self.analytics_client = build(
                "youtubeAnalytics",
                "v2",
                credentials=credentials,
                cache_discovery=False
            )

        return self.analytics_client

    def get_public_metrics(self, video_id):
        response = (
            self.get_youtube_client()
            .videos()
            .list(
                part="statistics",
                id=video_id
            )
            .execute()
        )

        items = response.get("items", [])

        if not items:
            raise ValueError(
                f"YouTube video unavailable: {video_id}"
            )

        statistics = items[0].get(
            "statistics", {}
        )

        def optional_int(key):
            value = statistics.get(key)
            return (
                int(value)
                if value is not None
                else None
            )

        return {
            "views": optional_int("viewCount"),
            "likes": optional_int("likeCount"),
            "comments": optional_int("commentCount"),
            "watch_time_minutes": None,
            "average_view_duration": None
        }

    def get_watch_metrics(
        self,
        video_id,
        days=30
    ):
        end_date = date.today() - timedelta(
            days=1
        )

        start_date = end_date - timedelta(
            days=days - 1
        )

        response = (
            self.get_analytics_client()
            .reports()
            .query(
                ids="channel==MINE",
                startDate=start_date.isoformat(),
                endDate=end_date.isoformat(),
                metrics=(
                    "estimatedMinutesWatched,"
                    "averageViewDuration"
                ),
                filters=f"video=={video_id}"
            )
            .execute()
        )

        rows = response.get("rows", [])

        if not rows:
            return {
                "watch_time_minutes": None,
                "average_view_duration": None
            }

        row = rows[0]

        return {
            "watch_time_minutes": float(row[0]),
            "average_view_duration": float(row[1])
        }

    def collect(self, video_id, days=30):
        metrics = self.get_public_metrics(
            video_id
        )

        try:
            watch_metrics = self.get_watch_metrics(
                video_id,
                days=days
            )
            metrics.update(watch_metrics)

        except Exception as exc:
            # Public metrics can still be collected
            # when Analytics API access is unavailable.
            import logging
            logging.warning(
                "Watch metrics unavailable for %s: %s",
                video_id,
                exc
            )

        return metrics
