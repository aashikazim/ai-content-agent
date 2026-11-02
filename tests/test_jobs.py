
import pytest

from app.services.job_service import JobService


def test_create_and_update_job(tmp_path):
    jobs = JobService(
        tmp_path / "jobs.db"
    )

    job_id = jobs.create_job(
        category="science",
        image_mode="text"
    )

    job = jobs.get_job(job_id)

    assert job["status"] == "queued"
    assert job["category"] == "science"

    jobs.update_job(
        job_id,
        "planning"
    )

    assert (
        jobs.get_job(job_id)["status"]
        == "planning"
    )


def test_review_requires_rendering(tmp_path):
    jobs = JobService(
        tmp_path / "jobs.db"
    )

    job_id = jobs.create_job()

    with pytest.raises(ValueError):
        jobs.review_job(
            job_id, "approved"
        )


def test_approve_completed_job(tmp_path):
    jobs = JobService(
        tmp_path / "jobs.db"
    )

    job_id = jobs.create_job()

    jobs.update_job(
        job_id,
        "pending_review",
        video_path="video.mp4"
    )

    job = jobs.review_job(
        job_id, "approved"
    )

    assert job["status"] == "approved"

    with pytest.raises(ValueError):
        jobs.review_job(
            job_id, "rejected"
        )
