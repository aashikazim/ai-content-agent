
import os
import logging

from dotenv import load_dotenv

from app.services.publishing_storage import PublishingStorage
from app.services.analytics_storage import AnalyticsStorage
from app.services.youtube_analytics import YouTubeAnalyticsService


load_dotenv(".env")

logging.basicConfig(level=logging.INFO)


def main():
    db_path = os.getenv(
        "ANALYTICS_DATABASE_PATH",
        "data/trends.db"
    )

    publications = PublishingStorage(
        db_path
    )

    analytics = AnalyticsStorage(
        db_path
    )

    youtube = YouTubeAnalyticsService()

    records = publications.list_publications(
        statuses=[
            "uploaded",
            "processing",
            "private",
            "published"
        ]
    )

    collected = 0
    skipped = 0

    for record in records:
        if record["platform"] != "youtube":
            skipped += 1
            continue

        video_id = record["platform_video_id"]

        if not video_id:
            skipped += 1
            continue

        try:
            metrics = youtube.collect(
                video_id,
                days=int(
                    os.getenv(
                        "ANALYTICS_LOOKBACK_DAYS",
                        "30"
                    )
                )
            )

            analytics.save_snapshot(
                publication_id=record[
                    "publication_id"
                ],
                platform="youtube",
                platform_video_id=video_id,
                metrics=metrics
            )

            collected += 1

            print(
                f"Collected {video_id}: "
                f"{metrics.get('views')} views"
            )

        except Exception:
            logging.exception(
                "Analytics collection failed "
                "for video %s",
                video_id
            )

    print(
        f"Complete: {collected} collected, "
        f"{skipped} skipped"
    )


if __name__ == "__main__":
    main()
