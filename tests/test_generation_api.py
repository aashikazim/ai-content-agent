"""Generation configuration API checks use only temporary data and mock work."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import admin_routes, routes
from app.models.content_plan import ContentPlan
from app.models.generation_config import GenerationConfig
from app.services.admin_auth_service import AdminAuthService
from app.services.content_storage import ContentStorage
from app.services.generation_storage import GenerationStorage
from app.services.job_service import JobService
from app.services.provider_catalog import GOOGLE_MODEL, KLING_MODEL, OPENAI_MODEL
from app.services.publishing_storage import PublishingStorage
from tests.test_content_plan import valid_data


@pytest.fixture
def generation_api(tmp_path, monkeypatch):
    from server import app

    db_path = tmp_path / "generation.db"
    credentials_path = tmp_path / "credentials" / "admin.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("ANALYTICS_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("CONTENT_PLANS_DIR", str(tmp_path / "plans"))
    monkeypatch.setenv("ADMIN_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    monkeypatch.setenv("CONTENT_API_KEY", "generation-test-key")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("IMAGE_MODEL_ID", "stable-diffusion-v1-5/stable-diffusion-v1-5")
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "KLING_API_KEY", "RUNWAYML_API_SECRET", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(routes, "DB_PATH", str(db_path))
    monkeypatch.setattr(
        routes, "get_storage", lambda: ContentStorage(db_path, tmp_path / "plans")
    )
    monkeypatch.setattr(
        admin_routes, "get_storage", lambda: ContentStorage(db_path, tmp_path / "plans")
    )
    execute = Mock()
    publisher = Mock(side_effect=AssertionError("Generation must never construct a publisher"))
    monkeypatch.setattr(routes, "execute_job", execute)
    monkeypatch.setattr(routes, "PublishingService", publisher)
    auth = AdminAuthService(db_path=db_path, credentials_path=credentials_path)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, auth=auth, db_path=db_path, tmp_path=tmp_path,
            execute=execute, publisher=publisher,
            api_headers={"X-API-Key": "generation-test-key"},
        )


def sign_in(api):
    api.auth.configure_admin("generation-admin", "a-long-temporary-admin-password")
    response = api.client.post("/api/v1/auth/login", json={
        "username": "generation-admin", "password": "a-long-temporary-admin-password",
    })
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def configuration(**changes):
    return GenerationConfig(**changes).model_dump(mode="json")


def assert_no_generation(api):
    api.execute.assert_not_called()
    api.publisher.assert_not_called()
    assert JobService(api.db_path).list_jobs() == []


@pytest.mark.parametrize("path", [
    "/api/v1/generation/catalog", "/api/v1/generation/configurations",
])
def test_generation_reads_require_authentication(generation_api, path):
    api = generation_api
    assert api.client.get(path).status_code == 401
    assert api.client.get(path, headers=api.api_headers).status_code == 200


def test_catalog_distinguishes_credentials_adapter_and_model_access(generation_api, monkeypatch):
    api = generation_api
    dummy_secrets = ["dummy-openai-private-value", "dummy-gemini-private-value", "dummy-kling-private-value", "dummy-runway-private-value", "dummy-claude-private-value"]
    for name, value in zip(("OPENAI_API_KEY", "GEMINI_API_KEY", "KLING_API_KEY", "RUNWAYML_API_SECRET", "ANTHROPIC_API_KEY"), dummy_secrets):
        monkeypatch.setenv(name, value)
    response = api.client.get("/api/v1/generation/catalog", headers=api.api_headers)
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["phase"] == 6
    providers = catalog["script_providers"][1:] + [p for p in catalog["video_providers"] if p['id'] in {'google','runway'}]
    assert all(item["configured"] is True for item in providers)
    assert providers[0]["implemented"] is True
    assert providers[0]["executable"] is True
    assert all(item["implemented"] is True for item in providers[1:])
    assert all(item["executable"] is True for item in providers[1:])
    assert all(item["account_access"] == "unverified" for item in providers)
    assert all(value not in response.text for value in dummy_secrets)
    assert_no_generation(api)


def test_browser_catalog_and_local_preflight_need_no_api_key(generation_api, monkeypatch):
    api = generation_api
    headers = sign_in(api)
    monkeypatch.delenv("CONTENT_API_KEY")
    assert api.client.get("/api/v1/generation/catalog").status_code == 200
    response = api.client.post("/api/v1/generation/preflight", headers=headers,
                               json={"configuration": configuration()})
    assert response.status_code == 200
    data = response.json()
    assert data["executable"] is True
    assert data["errors"] == []
    assert data["estimated_cost_usd"] is None
    assert data["paid_request"] is False
    assert any("still images" in warning for warning in data["warnings"])
    assert_no_generation(api)


@pytest.mark.parametrize("path,payload", [
    ("/api/v1/generation/preflight", {"configuration": {}}),
    ("/api/v1/generation/configurations", {"configuration": {}}),
    ("/api/v1/jobs", {"category": "science"}),
])
def test_browser_generation_mutations_require_csrf(generation_api, path, payload):
    api = generation_api
    sign_in(api)
    assert api.client.post(path, json=payload).status_code == 403
    assert api.client.post(path, json=payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    # A generation API key does not bypass CSRF for an authenticated browser.
    assert api.client.post(path, json=payload, headers=api.api_headers).status_code == 403
    assert_no_generation(api)


def test_configuration_drafts_round_trip_without_generating(generation_api):
    api = generation_api
    headers = sign_in(api)
    response = api.client.get("/api/v1/generation/configurations")
    assert response.json() == {"configurations": []}
    config = configuration(
        content_type="long_drama", script_provider="openai", script_model=OPENAI_MODEL,
        video_provider="google", video_model=GOOGLE_MODEL, fps=24,
        duration_seconds=1200, source_mode="script", supplied_script="An original drama script.",
        captions="timed", narration="model_audio", require_motion=True,
        character_reference_ids=[str(uuid4())], max_cost_usd="15.25",
    )
    response = api.client.post("/api/v1/generation/configurations", headers=headers,
                               json={"configuration": config})
    assert response.status_code == 201
    saved = response.json()
    UUID(saved["config_id"])
    assert saved["config"] == config
    assert api.client.get("/api/v1/generation/configurations").json() == {"configurations": [saved]}
    assert_no_generation(api)


def test_legacy_job_request_keeps_202_contract_and_marks_compatibility_snapshot(generation_api):
    api = generation_api
    response = api.client.post("/api/v1/jobs", headers=api.api_headers, json={
        "category": "science", "region": "US", "limit": 5, "image_mode": "text",
    })
    assert response.status_code == 202
    result = response.json()
    assert set(result) == {"job_id", "status"}
    assert result["status"] == "queued"
    api.execute.assert_called_once_with(result["job_id"])
    job = api.client.get(f"/api/v1/jobs/{result['job_id']}", headers=api.api_headers).json()
    assert job["generation_config_source"] == "legacy"
    assert job["generation_config"] == configuration()
    assert job["category"] == "science"
    assert job["region"] == "US"
    assert job["video_limit"] == 5
    api.publisher.assert_not_called()


def test_explicit_job_configuration_survives_job_list_and_dashboard_details(generation_api):
    api = generation_api
    headers = sign_in(api)
    config = configuration(duration_seconds=30, resolution="720p", aspect_ratio="16:9",
                           fps=24, visual_style="sketch", max_shots=3, max_cost_usd="0")
    response = api.client.post("/api/v1/jobs", headers=headers, json={
        "category": "science", "generation_config": config,
    })
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    api.execute.assert_called_once_with(job_id)
    job = api.client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["generation_config"] == config
    assert job["generation_config_source"] == "configured"
    assert JobService(api.db_path).list_jobs()[0]["generation_config"] == config
    overview = api.client.get("/api/v1/dashboard").json()
    assert overview["jobs"][0]["generation_config"] == config
    details = api.client.get(f"/api/v1/jobs/{job_id}/details").json()
    assert details["job"]["generation_config"] == config
    assert details["plan"] is None
    assert details["publications"] == []
    api.publisher.assert_not_called()


def test_nested_image_mode_is_authoritative_when_legacy_field_is_omitted(generation_api):
    api = generation_api
    config = configuration(image_mode="diffusion")
    response = api.client.post("/api/v1/jobs", headers=api.api_headers,
                               json={"generation_config": config})
    assert response.status_code == 202
    job = JobService(api.db_path).get_job(response.json()["job_id"])
    assert job["image_mode"] == "diffusion"
    assert job["generation_config"]["image_mode"] == "diffusion"


def test_conflicting_legacy_and_nested_image_modes_are_rejected(generation_api):
    api = generation_api
    response = api.client.post("/api/v1/jobs", headers=api.api_headers, json={
        "image_mode": "text", "generation_config": configuration(image_mode="diffusion"),
    })
    assert response.status_code == 422
    assert "conflicts" in response.json()["detail"]
    assert_no_generation(api)


@pytest.mark.parametrize("changes", [
    {"video_provider": "google", "video_model": GOOGLE_MODEL, "fps": 24},
    {"video_provider": "kling", "video_model": KLING_MODEL},
    {"video_provider": "runway", "video_model": "gen4.5"},
])
def test_cloud_models_never_queue_or_execute_even_with_credentials_and_confirmation(generation_api, monkeypatch, changes):
    api = generation_api
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "KLING_API_KEY", "RUNWAYML_API_SECRET", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "dummy-key-for-guard-test")
    config = configuration(**changes, paid_generation_confirmed=True)
    response = api.client.post("/api/v1/generation/preflight", headers=api.api_headers,
                               json={"configuration": config})
    assert response.status_code == 200
    assert response.json()["executable"] is (changes["video_provider"] in {"google", "runway"})
    assert response.json()["paid_request"] is False
    if changes['video_provider'] in {'google', 'runway'}:
        assert response.json()['submission_route'] == 'script_then_video_quote'
    response = api.client.post("/api/v1/jobs", headers=api.api_headers,
                               json={"generation_config": config})
    assert response.status_code == 422
    assert ("completed script" if changes["video_provider"] in {"google", "runway"} else "retired") in response.json()["detail"]
    assert_no_generation(api)


@pytest.mark.parametrize("changes", [
    {"content_type": "short_drama"}, {"content_type": "animated_comedy"},
    {"content_type": "long_drama"}, {"content_type": "news"},
    {"character_reference_ids": [str(uuid4())]}, {"captions": "timed"},
    {"require_motion": True}, {"narration": "model_audio"}, {"duration_seconds": 1200},
])
def test_planned_features_are_draftable_but_cannot_create_jobs(generation_api, changes):
    api = generation_api
    config = configuration(**changes)
    response = api.client.post("/api/v1/generation/configurations", headers=api.api_headers,
                               json={"configuration": config})
    assert response.status_code == 201
    response = api.client.post("/api/v1/jobs", headers=api.api_headers,
                               json={"generation_config": config})
    assert response.status_code == 422
    assert_no_generation(api)


@pytest.mark.parametrize("changes", [
    {"video_model": "unverified-model"}, {"script_model": "unverified-model"},
    {"image_model": "unverified-model"},
    {"video_provider": "google", "video_model": GOOGLE_MODEL, "fps": 24, "aspect_ratio": "1:1"},
    {"video_provider": "google", "video_model": GOOGLE_MODEL, "fps": 30},
    {"source_mode": "topic", "topic": " "}, {"source_mode": "script", "supplied_script": " "},
])
def test_unsupported_model_pairings_and_source_inputs_reject_drafts_and_jobs(generation_api, changes):
    api = generation_api
    config = configuration(**changes)
    for path, payload in [
        ("/api/v1/generation/configurations", {"configuration": config}),
        ("/api/v1/jobs", {"generation_config": config}),
    ]:
        assert api.client.post(path, headers=api.api_headers, json=payload).status_code == 422
    assert GenerationStorage(api.db_path).list_drafts() == []
    assert_no_generation(api)


@pytest.mark.parametrize("path,outer", [
    ("/api/v1/generation/preflight", "configuration"),
    ("/api/v1/generation/configurations", "configuration"),
    ("/api/v1/jobs", "generation_config"),
])
def test_credential_fields_are_rejected_without_echoing_or_storing_values(generation_api, path, outer):
    api = generation_api
    dummy_secret = "dummy-credential-value-must-stay-private"
    response = api.client.post(path, headers=api.api_headers,
                               json={outer: {"OPENAI_API_KEY": dummy_secret}})
    assert response.status_code == 422
    assert dummy_secret not in response.text
    assert GenerationStorage(api.db_path).list_drafts() == []
    assert_no_generation(api)


def test_existing_jobs_plans_publication_history_and_admin_sessions_survive(generation_api):
    api = generation_api
    plan_storage = ContentStorage(api.db_path, api.tmp_path / "plans")
    plan_id, _ = plan_storage.save_plan(ContentPlan.model_validate(valid_data()))
    with sqlite3.connect(api.db_path) as conn:
        # This is the schema before generation configuration tables existed.
        conn.execute("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY, category TEXT NOT NULL, region TEXT NOT NULL,
                video_limit INTEGER NOT NULL, image_mode TEXT NOT NULL,
                status TEXT NOT NULL, plan_id TEXT, video_path TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO jobs VALUES (
                'historical-job', 'science', 'US', 5, 'text', 'approved', ?, NULL,
                NULL, 'historical-created', 'historical-updated'
            )
        """, (plan_id,))
    publications = PublishingStorage(api.db_path)
    publication_id = publications.reserve_publication("historical-job", "youtube")
    publications.update(publication_id, "needs_reconciliation", error="Historical timeout")
    publications.record_event(publication_id, "upload_outcome_uncertain", "Historical event")
    headers = sign_in(api)
    tables = ("jobs", "content_plans", "publications", "publication_events", "admin_sessions")
    with sqlite3.connect(api.db_path) as conn:
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    # Saving a draft initializes the new tables in a populated legacy database.
    response = api.client.post("/api/v1/generation/configurations", headers=headers,
                               json={"configuration": configuration()})
    assert response.status_code == 201
    job = JobService(api.db_path).get_job("historical-job")
    assert job["generation_config"] is None
    assert job["generation_config_source"] == "legacy"
    assert job["status"] == "approved"
    assert api.client.get("/api/v1/auth/session").json()["authenticated"] is True
    with sqlite3.connect(api.db_path) as conn:
        after = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    assert after == before
    assert plan_storage.get_plan(plan_id)["title"] == valid_data()["title"]
    assert publications.get(publication_id)["status"] == "needs_reconciliation"
    api.execute.assert_not_called()
    api.publisher.assert_not_called()
