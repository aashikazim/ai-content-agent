
from unittest.mock import Mock

import pytest

from app.models.content_plan import ContentPlan
from app.services.job_service import JobService
from app.services.content_storage import ContentStorage
from app.services.publishing_storage import PublishingStorage
from app.services.publishing_service import PublishingService
from tests.test_content_plan import valid_data


@pytest.fixture
def publishing_setup(tmp_path):
    db_path = tmp_path / "test.db"

    jobs = JobService(db_path)
    content = ContentStorage(
        db_path=db_path,
        output_dir=tmp_path / "plans"
    )
    publications = PublishingStorage(db_path)

    plan = ContentPlan.model_validate(valid_data())
    plan_id, _ = content.save_plan(plan)

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"test-video")

    job_id = jobs.create_job(
        category="science"
    )

    jobs.update_job(
        job_id,
        "pending_review",
        plan_id=plan_id,
        video_path=str(video_path)
    )

    youtube = Mock()
    youtube.upload.return_value = {
        "platform": "youtube",
        "video_id": "test-video-id",
        "url": (
            "https://www.youtube.com/watch?v="
            "test-video-id"
        ),
        "status": "uploaded"
    }

    service = PublishingService(
        jobs=jobs,
        content_storage=content,
        publications=publications,
        youtube=youtube
    )

    return jobs, job_id, service, youtube


def test_reject_unapproved_job(publishing_setup):
    jobs, job_id, service, youtube = publishing_setup

    with pytest.raises(ValueError):
        service.publish(
            job_id,
            "youtube",
            dry_run=False
        )

    youtube.upload.assert_not_called()


def test_dry_run_does_not_upload(publishing_setup):
    jobs, job_id, service, youtube = publishing_setup

    jobs.review_job(job_id, "approved")

    result = service.publish(
        job_id,
        "youtube",
        dry_run=True
    )

    assert result["status"] == "dry_run"
    youtube.upload.assert_not_called()


def test_approved_job_uploads(publishing_setup):
    jobs, job_id, service, youtube = publishing_setup

    jobs.review_job(job_id, "approved")

    result = service.publish(
        job_id,
        "youtube",
        dry_run=False
    )

    assert result["status"] == "uploaded"
    assert result["video_id"] == "test-video-id"
    youtube.upload.assert_called_once()


def test_duplicate_upload_blocked(publishing_setup):
    jobs, job_id, service, youtube = publishing_setup

    jobs.review_job(job_id, "approved")

    service.publish(
        job_id,
        "youtube",
        dry_run=False
    )

    with pytest.raises(ValueError):
        service.publish(
            job_id,
            "youtube",
            dry_run=False
        )

    assert youtube.upload.call_count == 1
