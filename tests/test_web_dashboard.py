"""Browser authentication and manual review/publishing integration checks.

Every database, credential, plan, and media fixture lives under pytest's
temporary directory. Platform upload methods are always replaced with mocks.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.models.content_plan import ContentPlan
from app.services.analytics_storage import AnalyticsStorage
from app.services.content_storage import ContentStorage
from app.services.job_service import JobService
from app.services.publishing_service import PublishingService
from app.services.publishing_storage import PublishingStorage
from tests.test_content_plan import valid_data


USERNAME = "studio-admin"
PASSWORD = "a-long-test-password-for-admin"


@pytest.fixture
def web(tmp_path, monkeypatch):
    from app.api import admin_routes
    from app.services.admin_auth_service import AdminAuthService
    from server import app

    db_path = tmp_path / "studio.db"
    credentials_path = tmp_path / "credentials" / "admin.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("ANALYTICS_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("CONTENT_PLANS_DIR", str(tmp_path / "plans"))
    monkeypatch.setenv("ADMIN_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    monkeypatch.setenv("CONTENT_API_KEY", "generation-only-key")
    monkeypatch.setenv("CONTENT_REVIEW_API_KEY", "review-only-key")
    monkeypatch.setenv("CONTENT_PUBLISH_API_KEY", "publish-only-key")
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "private")
    monkeypatch.setattr(routes, "DB_PATH", str(db_path))
    monkeypatch.setattr(
        routes, "get_storage",
        lambda: ContentStorage(db_path, tmp_path / "plans")
    )
    monkeypatch.setattr(
        admin_routes, "get_storage",
        lambda: ContentStorage(db_path, tmp_path / "plans")
    )
    real_execute_job = routes.execute_job
    execute = Mock()
    monkeypatch.setattr(routes, "execute_job", execute)
    auth = AdminAuthService(db_path=db_path, credentials_path=credentials_path)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, auth=auth, db_path=db_path, tmp_path=tmp_path,
            credentials_path=credentials_path, execute=execute,
            real_execute_job=real_execute_job,
        )


def sign_in(web):
    if not web.auth.configured:
        web.auth.configure_admin(USERNAME, PASSWORD)
    response = web.client.post(
        "/api/v1/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def seed_job(web, *, status="pending_review", video_exists=True):
    jobs = JobService(web.db_path)
    plans = ContentStorage(web.db_path, web.tmp_path / "plans")
    plan_id, _ = plans.save_plan(ContentPlan.model_validate(valid_data()))
    directory = web.tmp_path / "videos" / plan_id
    directory.mkdir(parents=True)
    video_path = directory / "final_video.mp4"
    if video_exists:
        video_path.write_bytes(b"test-video-fixture" * 8)
    job_id = jobs.create_job(category="science", image_mode="text")
    jobs.update_job(
        job_id, status, plan_id=plan_id, video_path=str(video_path)
    )
    return job_id, video_path


def use_mock_publisher(web, monkeypatch, *, outcome=None):
    publisher = Mock()
    if isinstance(outcome, Exception):
        publisher.upload.side_effect = outcome
    else:
        publisher.upload.return_value = outcome or {
            "video_id": "mock-platform-id", "status": "uploaded",
            "url": "https://www.youtube.com/watch?v=mock-platform-id",
        }
    service = PublishingService(
        jobs=JobService(web.db_path),
        content_storage=ContentStorage(web.db_path, web.tmp_path / "plans"),
        publications=PublishingStorage(web.db_path),
        youtube=publisher,
        facebook=publisher,
    )
    monkeypatch.setattr(routes, "PublishingService", lambda **kwargs: service)
    return publisher


def test_unconfigured_admin_has_no_web_registration(web):
    response = web.client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["authenticated"] is False
    response = web.client.post(
        "/api/v1/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert response.status_code == 503
    assert "admin_session" not in web.client.cookies
    assert web.client.post("/api/v1/auth/register", json={}).status_code in (404, 405)


def test_login_cookie_and_session_do_not_expose_password(web):
    csrf = sign_in(web)
    cookie = web.client.cookies.get("admin_session")
    assert cookie and cookie != csrf
    response = web.client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["username"] == USERNAME
    assert response.json()["csrf_token"] == csrf
    assert response.json()["authenticated"] is True
    assert PASSWORD not in response.text
    assert cookie not in response.text
    response = web.client.post(
        "/api/v1/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=strict" in header
    assert "path=/" in header
    assert "max-age=" in header or "expires=" in header


def test_incorrect_login_cannot_access_dashboard(web):
    web.auth.configure_admin(USERNAME, PASSWORD)
    response = web.client.post(
        "/api/v1/auth/login",
        json={"username": USERNAME, "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert "admin_session" not in web.client.cookies
    assert web.client.get("/api/v1/dashboard").status_code == 401


def test_cross_origin_login_rejected(web):
    web.auth.configure_admin(USERNAME, PASSWORD)
    response = web.client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://unrelated.example"},
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert response.status_code == 403
    assert "admin_session" not in web.client.cookies


def test_logout_requires_csrf_and_revokes_session(web):
    csrf = sign_in(web)
    cookie = web.client.cookies.get("admin_session")
    assert web.client.post("/api/v1/auth/logout").status_code == 403
    response = web.client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code in (200, 204)
    assert web.auth.get_session(cookie) is None
    assert web.client.get("/api/v1/dashboard").status_code == 401


def test_expired_cookie_cannot_read_or_approve(web):
    job_id, _ = seed_job(web)
    csrf = sign_in(web)
    with web.auth.connect() as conn:
        conn.execute("UPDATE admin_sessions SET expires_at = 0")
    response = web.client.get("/api/v1/auth/session")
    assert response.json()["authenticated"] is False
    assert web.client.get("/api/v1/dashboard").status_code == 401
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/review", headers={"X-CSRF-Token": csrf},
        json={"decision": "approved"},
    )
    assert response.status_code in (401, 403)
    assert JobService(web.db_path).get_job(job_id)["status"] == "pending_review"


def test_https_login_sets_secure_cookie(web):
    from server import app

    web.auth.configure_admin(USERNAME, PASSWORD)
    with TestClient(app, base_url="https://testserver") as secure_client:
        response = secure_client.post(
            "/api/v1/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
        )
        assert response.status_code == 200, response.text
        assert "; secure" in response.headers["set-cookie"].lower()
        assert secure_client.get("/api/v1/dashboard").status_code == 200


def test_forged_session_cannot_access_private_data(web):
    web.client.cookies.set("admin_session", "forged-session")
    assert web.client.get("/api/v1/dashboard").status_code == 401
    assert web.client.get("/api/v1/jobs/nonexistent/details").status_code == 401


def test_fresh_dashboard_is_empty_and_does_not_generate_or_publish(web):
    sign_in(web)
    response = web.client.get("/api/v1/dashboard")
    assert response.status_code == 200, response.text
    data = response.json()
    for key in (
        "total_jobs", "pending_review", "approved", "active_jobs",
        "failed_jobs", "publication_attempts", "reported_views",
    ):
        assert data["summary"][key] == 0
    assert data["jobs"] == []
    assert data["publications"] == []
    assert data["analytics"] == []
    assert data["platform_settings"]["youtube_privacy"] == "private"
    web.execute.assert_not_called()


def test_dashboard_reads_existing_records_and_latest_analytics(web):
    job_id, _ = seed_job(web, status="approved")
    pubs = PublishingStorage(web.db_path)
    publication_id = pubs.reserve_publication(job_id, "youtube")
    pubs.update(publication_id, "private", platform_video_id="platform-id")
    pubs.record_event(publication_id, "status_checked", "privacy=private")
    analytics = AnalyticsStorage(web.db_path)
    for views in (40, 75):
        analytics.save_snapshot(
            publication_id, "youtube", "platform-id", {"views": views}
        )
    sign_in(web)
    response = web.client.get("/api/v1/dashboard")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"]["total_jobs"] == 1
    assert data["summary"]["approved"] == 1
    assert data["summary"]["publication_attempts"] == 1
    assert data["summary"]["reported_views"] == 75
    assert len(data["analytics"]) == 1
    assert data["publications"][0]["events"][0]["event_type"] == "status_checked"
    assert data["jobs"][0]["title"] == valid_data()["title"]
    assert data["jobs"][0]["job_id"] == job_id
    assert JobService(web.db_path).get_job(job_id)["status"] == "approved"


def test_session_can_generate_without_an_api_key(web):
    csrf = sign_in(web)
    response = web.client.post(
        "/api/v1/jobs", headers={"X-CSRF-Token": csrf},
        json={"category": "science", "limit": 5, "image_mode": "text"},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert JobService(web.db_path).get_job(job_id)["status"] == "queued"
    web.execute.assert_called_once_with(job_id)


def test_workflow_initialization_failure_marks_accepted_job_failed(web, monkeypatch):
    monkeypatch.setattr(routes, "execute_job", web.real_execute_job)
    monkeypatch.setattr(
        routes, "ContentWorkflow", Mock(side_effect=RuntimeError("initialization failed"))
    )
    response = web.client.post(
        "/api/v1/jobs", headers={"X-API-Key": "generation-only-key"},
        json={"category": "science", "limit": 5, "image_mode": "text"},
    )
    assert response.status_code == 202, response.text
    job = JobService(web.db_path).get_job(response.json()["job_id"])
    assert job["status"] == "failed"
    assert "could not start" in job["error"].lower()


@pytest.mark.parametrize("csrf", [None, "incorrect-csrf"])
def test_session_mutations_require_correct_csrf(web, csrf):
    sign_in(web)
    headers = {"X-CSRF-Token": csrf} if csrf is not None else {}
    response = web.client.post(
        "/api/v1/jobs", headers=headers,
        json={"category": "science", "image_mode": "text"},
    )
    assert response.status_code == 403
    web.execute.assert_not_called()


@pytest.mark.parametrize("action,payload", [
    ("review", {"decision": "approved"}),
    ("publish", {"platform": "youtube", "dry_run": False, "confirmed": True}),
])
def test_review_and_publish_require_csrf_with_session(web, monkeypatch, action, payload):
    job_id, _ = seed_job(web, status="approved" if action == "publish" else "pending_review")
    publisher = use_mock_publisher(web, monkeypatch)
    sign_in(web)
    response = web.client.post(f"/api/v1/jobs/{job_id}/{action}", json=payload)
    assert response.status_code == 403
    publisher.upload.assert_not_called()
    assert PublishingStorage(web.db_path).list_publications() == []


def test_foreign_origin_cannot_approve_even_with_session_and_csrf(web):
    job_id, _ = seed_job(web)
    csrf = sign_in(web)
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/review",
        headers={"X-CSRF-Token": csrf, "Origin": "https://unrelated.example"},
        json={"decision": "approved"},
    )
    assert response.status_code == 403
    assert JobService(web.db_path).get_job(job_id)["status"] == "pending_review"


@pytest.mark.parametrize("header,value", [
    ("X-CSRF-Token", b"caf\xe9"),
    ("Origin", "http://["),
])
def test_malformed_browser_security_headers_are_rejected(web, header, value):
    job_id, _ = seed_job(web)
    csrf = sign_in(web)
    headers = {"X-CSRF-Token": csrf, header: value}
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/review", headers=headers,
        json={"decision": "approved"},
    )
    assert response.status_code == 403
    assert JobService(web.db_path).get_job(job_id)["status"] == "pending_review"


@pytest.mark.parametrize("action,payload", [
    ("review", {"decision": "approved"}),
    ("publish", {"platform": "youtube", "dry_run": False, "confirmed": True}),
])
def test_generation_key_cannot_review_or_publish(web, action, payload):
    job_id, _ = seed_job(web)
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/{action}",
        headers={"X-API-Key": "generation-only-key"}, json=payload,
    )
    assert response.status_code in (401, 403)
    assert JobService(web.db_path).get_job(job_id)["status"] == "pending_review"


def test_approval_is_manual_and_never_instantiates_publisher(web, monkeypatch):
    job_id, _ = seed_job(web)
    publisher_factory = Mock(side_effect=AssertionError("approval must not publish"))
    monkeypatch.setattr(routes, "PublishingService", publisher_factory)
    csrf = sign_in(web)
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/review", headers={"X-CSRF-Token": csrf},
        json={"decision": "approved"},
    )
    assert response.status_code == 200, response.text
    assert JobService(web.db_path).get_job(job_id)["status"] == "approved"
    publisher_factory.assert_not_called()
    assert PublishingStorage(web.db_path).list_publications() == []


def test_approval_requires_existing_video_but_rejection_does_not(web):
    job_id, _ = seed_job(web, video_exists=False)
    csrf = sign_in(web)
    url = f"/api/v1/jobs/{job_id}/review"
    response = web.client.post(
        url, headers={"X-CSRF-Token": csrf}, json={"decision": "approved"}
    )
    assert response.status_code == 409
    response = web.client.post(
        url, headers={"X-CSRF-Token": csrf}, json={"decision": "rejected"}
    )
    assert response.status_code == 200
    assert JobService(web.db_path).get_job(job_id)["status"] == "rejected"


def test_missing_plan_blocks_approval_but_allows_rejection(web):
    job_id, _ = seed_job(web)
    job = JobService(web.db_path).get_job(job_id)
    (web.tmp_path / "plans" / f"{job['plan_id']}.json").unlink()
    csrf = sign_in(web)
    url = f"/api/v1/jobs/{job_id}/review"
    response = web.client.post(
        url, headers={"X-CSRF-Token": csrf}, json={"decision": "approved"}
    )
    assert response.status_code == 409
    assert JobService(web.db_path).get_job(job_id)["status"] == "pending_review"
    response = web.client.post(
        url, headers={"X-CSRF-Token": csrf}, json={"decision": "rejected"}
    )
    assert response.status_code == 200, response.text
    assert JobService(web.db_path).get_job(job_id)["status"] == "rejected"


def test_review_key_remains_available_for_manual_api_clients(web):
    job_id, _ = seed_job(web)
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/review",
        headers={"X-Review-Key": "review-only-key"},
        json={"decision": "approved"},
    )
    assert response.status_code == 200, response.text


def test_cookie_authenticates_video_and_range_requests(web):
    job_id, video_path = seed_job(web)
    url = f"/api/v1/jobs/{job_id}/video"
    assert web.client.get(url).status_code == 401
    sign_in(web)
    response = web.client.get(url, headers={"Range": "bytes=0-9"})
    assert response.status_code == 206, response.text
    assert response.content == video_path.read_bytes()[:10]
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["content-range"].startswith("bytes 0-9/")


def test_details_show_scene_media_without_exposing_arbitrary_manifest_paths(web):
    from PIL import Image

    job_id, video_path = seed_job(web)
    image_path = video_path.parent / "scene_1.png"
    Image.new("RGB", (4, 4), "green").save(image_path)
    (video_path.parent / "manifest.json").write_text(json.dumps({
        "status": "rendered", "scenes": [{
            "scene_number": 1, "duration": 15,
            "image": str(image_path),
        }],
    }))
    sign_in(web)
    response = web.client.get(f"/api/v1/jobs/{job_id}/details")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["video_exists"] is True
    assert data["plan"]["title"] == valid_data()["title"]
    image_url = data["manifest"]["scenes"][0]["image_url"]
    assert image_url.startswith("/api/v1/")
    assert str(web.tmp_path) not in image_url
    image_response = web.client.get(image_url)
    assert image_response.status_code == 200
    assert image_response.headers["content-type"].startswith("image/")


@pytest.mark.parametrize("contents", [
    "not valid JSON", "[]", '{"scenes": null}',
    '{"scenes": [{"scene_number": []}]}',
])
def test_malformed_manifest_does_not_break_review_details(web, contents):
    job_id, video_path = seed_job(web)
    (video_path.parent / "manifest.json").write_text(contents)
    sign_in(web)
    response = web.client.get(f"/api/v1/jobs/{job_id}/details")
    assert response.status_code == 200, response.text
    assert response.json()["video_exists"] is True
    assert len(response.json()["manifest"]["scenes"]) == 2


@pytest.mark.parametrize("contents", [
    "not valid JSON", '["unexpected plan"]',
    '{"title": "Broken plan", "scenes": null}',
])
def test_malformed_plan_is_visible_without_breaking_dashboard(web, contents):
    job_id, _ = seed_job(web)
    job = JobService(web.db_path).get_job(job_id)
    with JobService(web.db_path).connect() as conn:
        json_path = conn.execute(
            "SELECT json_path FROM content_plans WHERE plan_id = ?", (job["plan_id"],)
        ).fetchone()[0]
    Path(json_path).write_text(contents)
    sign_in(web)
    response = web.client.get("/api/v1/dashboard")
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["total_jobs"] == 1
    response = web.client.get(f"/api/v1/jobs/{job_id}/details")
    assert response.status_code == 200, response.text
    assert response.json()["plan"] is None


def test_dry_run_never_uploads_and_creates_no_reservation(web, monkeypatch):
    job_id, _ = seed_job(web, status="approved")
    publisher = use_mock_publisher(web, monkeypatch)
    csrf = sign_in(web)
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/publish", headers={"X-CSRF-Token": csrf},
        json={"platform": "youtube"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "dry_run"
    publisher.upload.assert_not_called()
    assert PublishingStorage(web.db_path).list_publications() == []


def test_live_browser_upload_requires_explicit_confirmation(web, monkeypatch):
    job_id, _ = seed_job(web, status="approved")
    publisher = use_mock_publisher(web, monkeypatch)
    csrf = sign_in(web)
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/publish", headers={"X-CSRF-Token": csrf},
        json={"platform": "youtube", "dry_run": False},
    )
    assert response.status_code in (409, 422)
    publisher.upload.assert_not_called()
    assert PublishingStorage(web.db_path).list_publications() == []


def test_live_upload_rejects_unapproved_job(web, monkeypatch):
    job_id, _ = seed_job(web)
    publisher = use_mock_publisher(web, monkeypatch)
    csrf = sign_in(web)
    response = web.client.post(
        f"/api/v1/jobs/{job_id}/publish", headers={"X-CSRF-Token": csrf},
        json={"platform": "youtube", "dry_run": False, "confirmed": True},
    )
    assert response.status_code == 409
    publisher.upload.assert_not_called()


def test_duplicate_live_upload_is_blocked(web, monkeypatch):
    job_id, _ = seed_job(web, status="approved")
    publisher = use_mock_publisher(web, monkeypatch)
    csrf = sign_in(web)
    for expected in (200, 409):
        response = web.client.post(
            f"/api/v1/jobs/{job_id}/publish", headers={"X-CSRF-Token": csrf},
            json={"platform": "youtube", "dry_run": False, "confirmed": True},
        )
        assert response.status_code == expected, response.text
    publisher.upload.assert_called_once()
    assert publisher.upload.call_args.kwargs["privacy"] == "private"


def test_uncertain_upload_remains_reserved_and_is_not_retried(web, monkeypatch):
    job_id, _ = seed_job(web, status="approved")
    publisher = use_mock_publisher(web, monkeypatch, outcome=TimeoutError("timeout"))
    csrf = sign_in(web)
    url = f"/api/v1/jobs/{job_id}/publish"
    payload = {"platform": "youtube", "dry_run": False, "confirmed": True}
    response = web.client.post(url, headers={"X-CSRF-Token": csrf}, json=payload)
    assert response.status_code == 502
    record = PublishingStorage(web.db_path).get_by_job(job_id, "youtube")
    assert record["status"] == "needs_reconciliation"
    response = web.client.post(url, headers={"X-CSRF-Token": csrf}, json=payload)
    assert response.status_code == 409
    publisher.upload.assert_called_once()
    details = web.client.get(f"/api/v1/jobs/{job_id}/details").json()
    assert details["publications"][0]["status"] == "needs_reconciliation"
    assert any(
        event["event_type"] == "upload_outcome_uncertain"
        for event in details["publications"][0]["events"]
    )


def test_private_api_responses_are_not_cached(web):
    sign_in(web)
    response = web.client.get("/api/v1/dashboard")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_authorized_facebook_retry_requires_browser_live_confirmation(web, monkeypatch):
    job_id, _ = seed_job(web, status='approved')
    publisher = use_mock_publisher(web, monkeypatch, outcome={'video_id':'fb-fixture','status':'submitted'})
    storage = PublishingStorage(web.db_path)
    identifier = storage.reserve_publication(job_id,'facebook')
    storage.update(identifier,'needs_reconciliation')
    storage.authorize_retry(identifier, confirmed_not_uploaded=True, notes='Verified rejected start')
    csrf = sign_in(web)
    url = f'/api/v1/jobs/{job_id}/publish'
    response = web.client.post(url, headers={'X-CSRF-Token':csrf}, json={'platform':'facebook','dry_run':False})
    assert response.status_code == 409
    publisher.upload.assert_not_called()
    assert storage.get(identifier)['status'] == 'retry_authorized'
    response = web.client.post(url, headers={'X-CSRF-Token':csrf}, json={'platform':'facebook','dry_run':False,'confirmed':True})
    assert response.status_code == 200, response.text
    assert response.json()['publication_id'] == identifier
    assert web.client.post(url, headers={'X-CSRF-Token':csrf}, json={'platform':'facebook','dry_run':False,'confirmed':True}).status_code == 409
    publisher.upload.assert_called_once()


@pytest.mark.parametrize("path", ["/", "/review", "/publishing"])
def test_built_frontend_loads_at_browser_routes(web, monkeypatch, path):
    import server

    dist = web.tmp_path / "web-dist"
    dist.mkdir()
    markup = '<!doctype html><html><body><div id="root">Studio shell</div></body></html>'
    (dist / "index.html").write_text(markup)
    monkeypatch.setattr(server, "FRONTEND_DIST", dist)
    response = web.client.get(path)
    assert response.status_code == 200
    assert response.text == markup
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api", "/api/v1/nonexistent-resource"])
def test_unknown_api_paths_never_fall_back_to_frontend(web, monkeypatch, path):
    import server

    monkeypatch.setattr(server, "FRONTEND_DIST", web.tmp_path / "missing-build")
    response = web.client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found"


def test_missing_frontend_build_has_useful_setup_response(web, monkeypatch):
    import server

    monkeypatch.setattr(server, "FRONTEND_DIST", web.tmp_path / "missing-build")
    response = web.client.get("/")
    assert response.status_code == 503
    assert "npm ci" in response.text
    assert "npm run build" in response.text


def test_frontend_public_assets_are_confined_to_build_directory(web, monkeypatch):
    import server

    dist = web.tmp_path / "web-dist"
    dist.mkdir()
    (dist / "studio-mark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (web.tmp_path / "outside.svg").write_text("private fixture outside build")
    monkeypatch.setattr(server, "FRONTEND_DIST", dist)
    assert web.client.get("/studio-mark.svg").status_code == 200
    response = web.client.get("/%2e%2e/outside.svg")
    assert response.status_code == 404
    assert "private fixture" not in response.text
