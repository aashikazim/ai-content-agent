
import os
import logging

from dotenv import load_dotenv

from apscheduler.schedulers.blocking import (
    BlockingScheduler
)
from apscheduler.triggers.cron import CronTrigger

from app.services.scheduler_service import SchedulerService


load_dotenv(".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


def generate_content():
    logging.info(
        "Starting scheduled content generation"
    )

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
        "Scheduled generation response: %s",
        result
    )


def main():
    if os.getenv(
        "SCHEDULER_ENABLED",
        "false"
    ).lower() != "true":
        logging.warning(
            "Scheduler is disabled. "
            "Set SCHEDULER_ENABLED=true "
            "after completing manual tests."
        )
        return

    timezone = os.getenv(
        "SCHEDULER_TIMEZONE",
        "America/New_York"
    )

    scheduler = BlockingScheduler(
        timezone=timezone
    )

    scheduler.add_job(
        generate_content,
        trigger=CronTrigger(
            hour=int(
                os.getenv(
                    "SCHEDULER_HOUR",
                    "9"
                )
            ),
            minute=int(
                os.getenv(
                    "SCHEDULER_MINUTE",
                    "0"
                )
            ),
            timezone=timezone
        ),
        id="daily_content_generation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300
    )

    logging.info(
        "Content scheduler started. "
        "Timezone: %s",
        timezone
    )

    scheduler.start()


if __name__ == "__main__":
    main()
