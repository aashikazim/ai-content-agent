
from unittest.mock import Mock

import pytest

from app.services.scheduler_service import SchedulerService


def test_scheduler_creates_generation_job():
    session = Mock()

    response = Mock()
    response.json.return_value = {
        "job_id": "job-123",
        "status": "queued"
    }

    session.post.return_value = response

    service = SchedulerService(
        api_url="http://127.0.0.1:8000",
        api_key="test-key",
        session=session
    )

    result = service.create_generation_job(
        category="science",
        region="US",
        limit=5,
        image_mode="text"
    )

    assert result["job_id"] == "job-123"

    session.post.assert_called_once()

    args, kwargs = session.post.call_args

    assert args[0].endswith("/api/v1/jobs")
    assert kwargs["headers"]["X-API-Key"] == "test-key"
    assert kwargs["json"]["category"] == "science"


def test_scheduler_rejects_invalid_limit():
    service = SchedulerService(
        api_key="test-key",
        session=Mock()
    )

    with pytest.raises(ValueError):
        service.create_generation_job(
            limit=0
        )


def test_scheduler_requires_api_key():
    service = SchedulerService(
        api_key="",
        session=Mock()
    )

    with pytest.raises(ValueError):
        service.create_generation_job()
