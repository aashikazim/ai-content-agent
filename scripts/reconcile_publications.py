
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)

from app.services.publishing_storage import PublishingStorage
from app.services.youtube_publisher import YouTubePublisher
from app.services.facebook_publisher import FacebookPublisher


def reconcile():
    storage = PublishingStorage(
        os.getenv(
            "DATABASE_PATH",
            "data/trends.db"
        )
    )

    records = storage.list_publications(
        statuses=[
            "uploaded",
            "submitted",
            "processing",
            "needs_reconciliation"
        ]
    )

    youtube = None
    facebook = None

    for record in records:
        publication_id = record["publication_id"]
        platform = record["platform"]
        video_id = record["platform_video_id"]

        if not video_id:
            print(
                f"{publication_id}: "
                "No platform ID; manual review required"
            )
            continue

        try:
            if platform == "youtube":
                if youtube is None:
                    youtube = YouTubePublisher()

                result = youtube.get_video_status(
                    video_id
                )

                new_status = result["status"]
                external_status = result[
                    "external_status"
                ]

            elif platform == "facebook":
                if facebook is None:
                    facebook = FacebookPublisher()

                result = facebook.get_status(
                    video_id
                )

                # Keep the raw Meta status until
                # a verified status mapping is added.
                new_status = record["status"]
                external_status = str(
                    result.get("status", {})
                )

            else:
                continue

            now = datetime.now(
                timezone.utc
            ).isoformat()

            with storage.connect() as conn:
                conn.execute("""
                    UPDATE publications
                    SET status = ?,
                        external_status = ?,
                        last_checked_at = ?,
                        updated_at = ?
                    WHERE publication_id = ?
                """, (
                    new_status,
                    external_status,
                    now,
                    now,
                    publication_id
                ))

            storage.record_event(
                publication_id,
                "status_checked",
                external_status
            )

            print(
                f"{platform}: {video_id} "
                f"-> {new_status}"
            )

        except Exception as exc:
            print(
                f"{publication_id}: "
                f"status check failed: "
                f"{type(exc).__name__}: {exc}"
            )


if __name__ == "__main__":
    reconcile()
