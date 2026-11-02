
import argparse
import os

from dotenv import load_dotenv
from app.agents.trend_agent import (
    TrendAgent, CATEGORIES
)
from app.services.storage_service import StorageService

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="AI Content Creator Trend Agent"
    )

    parser.add_argument(
        "--region",
        default=os.getenv("YOUTUBE_REGION", "US")
    )
    parser.add_argument(
        "--category",
        choices=list(CATEGORIES),
        default="all"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20
    )
    parser.add_argument(
        "--short-form",
        action="store_true"
    )

    args = parser.parse_args()

    storage = StorageService(
        os.getenv("DATABASE_PATH", "data/trends.db")
    )
    agent = TrendAgent(storage=storage)

    videos = agent.discover(
        region=args.region,
        category=args.category,
        limit=args.limit,
        short_form=args.short_form
    )

    print(f"\nDiscovered {len(videos)} videos\n")

    for index, video in enumerate(videos, 1):
        print(f"{index}. {video.title}")
        print(f"   Channel: {video.channel}")
        print(f"   Views: {video.views:,}")
        print(f"   Duration: {video.duration_seconds}s")
        print(f"   Score: {video.score:.4f}")
        print(f"   URL: {video.url}\n")

    print(
        f"Total stored videos: {storage.count_videos()}"
    )


if __name__ == "__main__":
    main()
