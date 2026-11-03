
import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from app.services.scheduler_service import SchedulerService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


def main():
    service = SchedulerService()

    result = service.create_generation_job(
        category=os.getenv(
            "SCHEDULER_CATEGORY",
            "science"
        ),
        region=os.getenv(
            "SCHEDULER_REGION",
            "US"
        ),
        limit=int(
            os.getenv(
                "SCHEDULER_VIDEO_LIMIT",
                "5"
            )
        ),
        image_mode=os.getenv(
            "SCHEDULER_IMAGE_MODE",
            "text"
        )
    )

    logging.info(
        "Generation job requested: %s",
        result
    )


if __name__ == "__main__":
    main()
