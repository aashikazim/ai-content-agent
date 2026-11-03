
import os
import sqlite3
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
            "DATABASE_PATH",
            "data/trends.db"
        )

        self.jobs = (
            jobs
            if jobs is not None
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

        # Publishers are created only when needed.
        # This allows dry runs without platform credentials.
        self.youtube = youtube
        self.facebook = facebook

    def publish(
        self,
        job_id,
        platform,
        dry_run=True
    ):
        # ----------------------------------
        # 1. Validate the platform
        # ----------------------------------
        if platform not in {"youtube", "facebook"}:
            raise ValueError(
                f"Unsupported platform: {platform}"
            )

        # ----------------------------------
        # 2. Load and validate the job
        # ----------------------------------
        job = self.jobs.get_job(job_id)

        if job is None:
            raise KeyError(
                f"Job not found: {job_id}"
            )

        if job["status"] != "approved":
            raise ValueError(
                "Only approved jobs can be published. "
                f"Current status: {job['status']}"
            )

        if not job.get("plan_id"):
            raise ValueError(
                "Job does not have a content plan"
            )

        if not job.get("video_path"):
            raise ValueError(
                "Job does not have a rendered video"
            )

        # ----------------------------------
        # 3. Validate the video file
        # ----------------------------------
        video_path = Path(job["video_path"])

        if not video_path.is_file():
            raise FileNotFoundError(
                f"Video file not found: {video_path}"
            )

        # ----------------------------------
        # 4. Load the content plan
        # ----------------------------------
        plan = self.content_storage.get_plan(
            job["plan_id"]
        )

        if plan is None:
            raise ValueError(
                "Content plan not found"
            )

        # Support a dictionary or Pydantic model.
        if hasattr(plan, "model_dump"):
            plan = plan.model_dump()
        if platform == 'facebook' and sum(scene['duration_seconds'] for scene in plan.get('scenes',[])) > 60:
            raise ValueError('Long-form Facebook uploads are not supported by this Reels integration; use YouTube for this drama')

        title = plan["title"]

        hashtags = plan.get("hashtags", [])

        description = (
            plan.get("description", "")
            + "\n\n"
            + " ".join(hashtags)
        ).strip()

        # ----------------------------------
        # 5. Dry run: validate without upload
        # ----------------------------------
        if dry_run:
            return {
                "job_id": job_id,
                "platform": platform,
                "status": "dry_run",
                "video_path": str(video_path),
                "title": title,
                "description": description,
                "message": (
                    "Validation completed. "
                    "No video was uploaded."
                )
            }

        # ----------------------------------
        # 6. Check for existing publication
        # ----------------------------------
        existing = self.publications.get_by_job(
            job_id,
            platform
        )

        if existing and (existing['status'] != 'retry_authorized' or platform != 'facebook' or existing['platform_video_id']):
            raise ValueError(
                "A publishing attempt already exists "
                f"for job {job_id} on {platform}. "
                f"Current publication status: "
                f"{existing['status']}. "
                "Inspect the record before retrying."
            )

        # ----------------------------------
        # 7. Atomically reserve publication
        # ----------------------------------
        try:
            publication_id = (self.publications.reserve_authorized_retry(existing['publication_id'])
                              if existing else self.publications.reserve_publication(job_id, platform))

        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "A concurrent publishing request "
                "already reserved this job/platform. "
                "Duplicate upload prevented."
            ) from exc

        # ----------------------------------
        # 8. Mark publication as uploading
        # ----------------------------------
        try:
            self.publications.update(
                publication_id,
                status="uploading"
            )

            self.publications.record_event(
                publication_id,
                "upload_started",
                f"platform={platform}"
            )

            # ------------------------------
            # 9. Upload to selected platform
            # ------------------------------
            if platform == "youtube":
                publisher = (
                    self.youtube
                    if self.youtube is not None
                    else YouTubePublisher()
                )

                tags = [
                    str(tag).lstrip("#")
                    for tag in hashtags
                ]

                result = publisher.upload(
                    video_path=video_path,
                    title=title,
                    description=description,
                    tags=tags,
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
                    video_path=video_path,
                    title=title,
                    description=description
                )

            # ------------------------------
            # 10. Validate platform response
            # ------------------------------
            video_id = result.get("video_id")

            if not video_id:
                raise RuntimeError(
                    "Platform did not return a "
                    "video ID. Upload outcome "
                    "must be checked manually."
                )

            # Keep 'uploaded' or 'submitted'.
            # Do not assume public publication.
            upload_status = result.get(
                "status",
                "uploaded"
                if platform == "youtube"
                else "submitted"
            )

            # ------------------------------
            # 11. Save successful upload
            # ------------------------------
            self.publications.update(
                publication_id,
                status=upload_status,
                platform_video_id=video_id,
                url=result.get("url"),
                error=None
            )

            self.publications.record_event(
                publication_id,
                "upload_accepted",
                (
                    f"platform={platform};"
                    f"video_id={video_id};"
                    f"status={upload_status}"
                )
            )

            return {
                "publication_id": publication_id,
                "job_id": job_id,
                "platform": platform,
                "video_id": video_id,
                "url": result.get("url"),
                "status": upload_status,
                "message": (
                    "Upload accepted by platform. "
                    "Processing and visibility "
                    "must be verified separately."
                )
            }

        # ----------------------------------
        # 12. Handle uncertain outcomes
        # ----------------------------------
        except Exception as exc:
            self.publications.update(
                publication_id,
                status="needs_reconciliation",
                error=str(exc)
            )

            self.publications.record_event(
                publication_id,
                "upload_outcome_uncertain",
                type(exc).__name__
            )

            raise
