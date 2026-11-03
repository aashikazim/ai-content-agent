
from unittest.mock import Mock

import pytest

from app.services.review_dashboard_service import (
    ReviewDashboardService
)


def test_approval_requires_video():
    jobs = Mock()
    content = Mock()

    jobs.get_job.return_value = {
        "job_id": "job-1",
        "status": "pending_review",
        "plan_id": None,
        "video_path": "missing_video.mp4"
    }

    service = ReviewDashboardService(
        jobs=jobs,
        content_storage=content
    )

    with pytest.raises(
        ValueError,
        match="without a rendered video"
    ):
        service.review(
            "job-1",
            "approved"
        )

    jobs.review_job.assert_not_called()


def test_approval_does_not_publish(tmp_path):
    jobs = Mock()
    content = Mock()

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"test-video")

    jobs.get_job.return_value = {
        "job_id": "job-1",
        "status": "pending_review",
        "plan_id": None,
        "video_path": str(video_path)
    }

    service = ReviewDashboardService(
        jobs=jobs,
        content_storage=content
    )

    service.review(
        "job-1",
        "approved"
    )

    jobs.review_job.assert_called_once_with(
        "job-1",
        "approved"
    )


def test_reject_pending_job():
    jobs = Mock()
    content = Mock()

    jobs.get_job.return_value = {
        "job_id": "job-1",
        "status": "pending_review",
        "plan_id": None,
        "video_path": None
    }

    service = ReviewDashboardService(
        jobs=jobs,
        content_storage=content
    )

    service.review(
        "job-1",
        "rejected"
    )

    jobs.review_job.assert_called_once_with(
        "job-1",
        "rejected"
    )


def test_cannot_review_twice():
    jobs = Mock()
    content = Mock()

    jobs.get_job.return_value = {
        "job_id": "job-1",
        "status": "approved",
        "plan_id": None,
        "video_path": None
    }

    service = ReviewDashboardService(
        jobs=jobs,
        content_storage=content
    )

    with pytest.raises(
        ValueError,
        match="not awaiting review"
    ):
        service.review(
            "job-1",
            "rejected"
        )
