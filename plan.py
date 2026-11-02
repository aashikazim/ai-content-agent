
import argparse
import os
import sys

from dotenv import load_dotenv

from app.agents.trend_agent import TrendAgent, CATEGORIES
from app.agents.content_agent import ContentAgent
from app.services.storage_service import StorageService
from app.services.content_storage import ContentStorage

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="Generate an AI video content plan"
    )

    parser.add_argument(
        "--category",
        choices=[*CATEGORIES, "mixed"],
        default="mixed"
    )
    parser.add_argument(
        "--region",
        default=os.getenv("YOUTUBE_REGION", "US")
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

    db_path = os.getenv(
        "DATABASE_PATH", "data/trends.db"
    )

    discovery = TrendAgent(
        storage=StorageService(db_path)
    )

    # "mixed" uses the Day 1 all-category chart.
    discovery_category = (
        "all" if args.category == "mixed"
        else args.category
    )

    print("Discovering trending videos...")

    videos = discovery.discover(
        region=args.region,
        category=discovery_category,
        limit=args.limit,
        short_form=args.short_form
    )

    if not videos:
        print(
            "No candidate videos found. "
            "Try without --short-form."
        )
        return 1

    print(f"Found {len(videos)} candidates.")
    print("Generating content with local Ollama...")

    agent = ContentAgent()

    plan = agent.create_plan(
        videos,
        category=args.category,
        language=os.getenv(
            "CONTENT_LANGUAGE", "English"
        )
    )

    storage = ContentStorage(db_path=db_path)
    plan_id, path = storage.save_plan(plan)

    print("\nContent plan generated!")
    print(f"Plan ID: {plan_id}")
    print(f"Topic: {plan.topic}")
    print(f"Title: {plan.title}")
    print(f"Duration: {plan.total_duration}s")
    print(f"Scenes: {len(plan.scenes)}")
    print(f"Review status: {plan.review_status}")
    print(f"Saved to: {path}")

    for scene in plan.scenes:
        print(
            f"\nScene {scene.scene_number} "
            f"({scene.duration_seconds}s)"
        )
        print(f"Narration: {scene.narration}")
        print(f"Visual: {scene.visual_prompt}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
