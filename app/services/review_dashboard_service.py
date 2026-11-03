
from pathlib import Path

from app.services.job_service import JobService
from app.services.content_storage import ContentStorage


class ReviewDashboardService:
    def __init__(
        self,
        jobs=None,
        content_storage=None
    ):
        self.jobs = jobs or JobService()
        self.content_storage = (
            content_storage or ContentStorage()
        )

    def list_pending(self):
        return self.jobs.list_jobs(
            status="pending_review"
        )

    def get_review_details(self, job_id):
        job = self.jobs.get_job(job_id)

        if not job:
            raise ValueError("Job not found")

        plan = None

        if job.get("plan_id"):
            plan = self.content_storage.get_plan(
                job["plan_id"]
            )

            if hasattr(plan, "model_dump"):
                plan = plan.model_dump()

        video_path = job.get("video_path")

        video_exists = bool(
            video_path
            and Path(video_path).is_file()
        )

        return {
            "job": job,
            "plan": plan,
            "video_exists": video_exists
        }

    def review(self, job_id, decision):
        if decision not in {
            "approved",
            "rejected"
        }:
            raise ValueError(
                "Invalid review decision"
            )

        details = self.get_review_details(
            job_id
        )

        job = details["job"]

        if job["status"] != "pending_review":
            raise ValueError(
                "Job is not awaiting review"
            )

        if decision == "approved":
            if not details["video_exists"]:
                raise ValueError(
                    "Cannot approve a job "
                    "without a rendered video"
                )

        self.jobs.review_job(
            job_id,
            decision
        )

        return self.jobs.get_job(job_id)
