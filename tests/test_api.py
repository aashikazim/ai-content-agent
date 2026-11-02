
from fastapi.testclient import TestClient

from app.api import routes
from app.services.job_service import JobService
from server import app


def test_health():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_job(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"

    monkeypatch.setenv(
        "CONTENT_API_KEY",
        "test-secret-key"
    )

    monkeypatch.setattr(
        routes,
        "get_jobs",
        lambda: JobService(db_path)
    )

    # Do not execute the real background workflow.
    monkeypatch.setattr(
        routes,
        "execute_job",
        lambda job_id: None
    )

    client = TestClient(app)

    response = client.post(
        "/api/v1/jobs",
        headers={
            "X-API-Key": "test-secret-key"
        },
        json={
            "category": "science",
            "region": "US",
            "limit": 10,
            "image_mode": "text"
        }
    )

    assert response.status_code == 202

    job_id = response.json()["job_id"]

    assert (
        JobService(db_path)
        .get_job(job_id)["status"]
        == "queued"
    )


def test_api_rejects_missing_key(
    monkeypatch
):
    monkeypatch.setenv(
        "CONTENT_API_KEY",
        "test-secret-key"
    )

    client = TestClient(app)

    response = client.get(
        "/api/v1/jobs/nonexistent"
    )

    assert response.status_code == 401
