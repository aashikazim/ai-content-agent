
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.agents.video_agent import VideoGenerationAgent
from app.models.content_plan import ContentPlan
from app.services.content_storage import ContentStorage
from app.services.image_service import ImageService
from app.services.tts_service import TTSService
from app.services.video_renderer import VideoRenderer

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="Render a local AI-generated video"
    )

    parser.add_argument(
        "--plan-id",
        required=True,
        help="Content plan ID from Day 2"
    )

    parser.add_argument(
        "--image-mode",
        choices=["diffusion", "text"],
        default="diffusion"
    )

    args = parser.parse_args()

    storage = ContentStorage(
        db_path=os.getenv(
            "DATABASE_PATH", "data/trends.db"
        )
    )

    plan_data = storage.get_plan(args.plan_id)
    plan = ContentPlan.model_validate(plan_data)

    output_dir = (
        Path("data/videos") / args.plan_id
    )

    image_service = ImageService(
        mode=args.image_mode
    )

    tts_service = TTSService(
        voice=os.getenv("TTS_VOICE", "en-us")
    )

    renderer = VideoRenderer(
        width=int(
            os.getenv("VIDEO_WIDTH", "1080")
        ),
        height=int(
            os.getenv("VIDEO_HEIGHT", "1920")
        ),
        fps=int(
            os.getenv("VIDEO_FPS", "30")
        )
    )

    agent = VideoGenerationAgent(
        image_service=image_service,
        tts_service=tts_service,
        renderer=renderer
    )

    print(f"Rendering: {plan.title}")
    print(f"Scenes: {len(plan.scenes)}")
    print(f"Duration: {plan.total_duration}s")

    output_path = agent.generate(
        plan, output_dir
    )

    print("\nVideo generated successfully!")
    print(f"Output: {output_path}")
    print("Status: Draft — requires review")

    return 0


if __name__ == "__main__":
    sys.exit(main())
