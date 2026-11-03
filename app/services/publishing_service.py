
import os
from pathlib import Path

from app.services.job_service import JobService
from app.services.content_storage import ContentStorage
from app.services.publishing_storage import PublishingStorage
from app.services.youtube_publisher import YouTubePublisher
from app.services.facebook_publisher import FacebookPublisher


class PublishingService:
    def __init__(
        self,
        jobs=None,
        content_storage=None,
        publications=None,
        youtube=None,
        facebook=None
    ):
        db_path = os.getenv(
            "DATABASE_PATH", "data/trends.db"
        )

        self.jobs = (
            jobs if jobs is not None
            else JobService(db_path)
        )

        self.content_storage = (
            content_storage
            if content_storage is not None
            else ContentStorage(db_path)
        )

        self.publications = (
            publications
            if publications is not None
            else PublishingStorage(db_path)
        )

        self.youtube = youtube
        self.facebook = facebook

    def publish(
        self,
        job_id,
        platform,
        dry_run=True
    ):
        if platform not in {"youtube", "facebook"}:
            raise ValueError(
                "Unsupported publishing platform"
            )

        job = self.jobs.get_job(job_id)

        if job is None:
            raise KeyError(
                f"Job not found: {job_id}"
            )

        if job["status"] != "approved":
            raise ValueError(
                "Only approved jobs can be published"
            )

        if not job["plan_id"] or not job["video_path"]:
            raise ValueError(
                "Job has no completed video"
            )

        video_path = Path(job["video_path"])

        if not video_path.is_file():
            raise FileNotFoundError(video_path)

        plan = self.content_storage.get_plan(
            job["plan_id"]
        )

        title = plan["title"]
        hashtags = plan.get("hashtags", [])

        description = (
            plan["description"]
            + "\n\n"
            + " ".join(hashtags)
        )

        if dry_run:
            return {
                "job_id": job_id,
                "platform": platform,
                "status": "dry_run",
                "video_path": str(video_path),
                "title": title,
                "description": description
            }

        existing = self.publications.get_by_job(
            job_id, platform
        )

        if existing:
            raise ValueError(
                "A publishing attempt already exists "
                "for this job and platform. "
                "Inspect its status before retrying."
            )

        publication_id = self.publications.create(
            job_id, platform
        )

        self.publications.update(
            publication_id,
            "uploading"
        )

        try:
            if platform == "youtube":
                publisher = (
                    self.youtube
                    if self.youtube is not None
                    else YouTubePublisher()
                )

                result = publisher.upload(
                    video_path,
                    title,
                    description,
                    tags=[
                        tag.lstrip("#")
                        for tag in hashtags
                    ],
                    privacy=os.getenv(
                        "YOUTUBE_PRIVACY_STATUS",
                        "private"
                    )
                )

            else:
                publisher = (
                    self.facebook
                    if self.facebook is not None
                    else FacebookPublisher()
                )

                result = publisher.upload(
                    video_path,
                    title,
                    description
                )

            self.publications.update(
                publication_id,
                result["status"],
                platform_video_id=result["video_id"],
                url=result.get("url")
            )

            return {
                "publication_id": publication_id,
                **result
            }

        except Exception as exc:
            self.publications.update(
                publication_id,
                "needs_reconciliation",
                error=str(exc)
            )
            raise
