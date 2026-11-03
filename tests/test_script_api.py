import json
from unittest.mock import Mock

import pytest

from app.api import routes
from app.models.generation_config import GenerationConfig
from app.services.content_storage import ContentStorage
from app.services.job_service import JobService
from app.services.openai_script_service import OpenAIScriptService
from app.services.script_generation_service import ScriptGenerationService
from app.workflows.content_workflow import ContentWorkflow
from tests.test_generation_api import generation_api, sign_in
from tests.test_openai_scripts import production, completed


@pytest.fixture
def scripts(generation_api, monkeypatch):
    api = generation_api
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-phase-two-private-key")
    calls = Mock()
    monkeypatch.setattr(OpenAIScriptService, "check_model", lambda self: None)
    monkeypatch.setattr(OpenAIScriptService, "available_models", lambda self: ["gpt-6.1-sol", "gpt-6-sol"])
    def generate(provider, prompt):
        calls(prompt)
        provider.last_usage = completed()["usage"]
        provider.last_response_id = "resp_fixture"
        return production("short_drama" if "Content type: short_drama" in prompt else "realistic_short")
    monkeypatch.setattr(OpenAIScriptService, "generate_production_plan", generate)
    api.script_calls = calls
    api.admin_headers = sign_in(api)
    return api


def request_body(content_type="realistic_short", purpose="script"):
    config = GenerationConfig(script_provider="openai", script_model="gpt-6.1-sol",
                              source_mode="topic", topic="A fictional original story", content_type=content_type)
    return {"configuration": config.model_dump(mode="json"), "category": "science",
            "region": "US", "limit": 5, "purpose": purpose}


def authorize(api, body):
    response = api.client.post("/api/v1/generation/script-quotes", headers=api.admin_headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["maximum_cost_usd"] == "0.21"
    api.script_calls.assert_not_called()
    return response.json()["quote_id"]


def test_script_endpoints_need_admin_session_and_csrf(generation_api):
    api = generation_api
    for path in ("/generation/scripts", "/generation/openai-models"):
        assert api.client.get("/api/v1" + path, headers=api.api_headers).status_code == 401
    headers = sign_in(api)
    body = {"configuration": GenerationConfig().model_dump(mode="json")}
    assert api.client.post("/api/v1/generation/scripts", json=body).status_code == 403
    assert api.client.post("/api/v1/generation/script-quotes", json=body).status_code == 403
    api.publisher.assert_not_called()


def test_paid_script_missing_confirmation_is_not_submitted(scripts):
    api = scripts
    body = request_body()
    body["quote_id"] = authorize(api, body)
    response = api.client.post("/api/v1/generation/scripts", headers=api.admin_headers, json=body)
    assert response.status_code == 422
    api.script_calls.assert_not_called()


def test_paid_script_draft_history_and_revision(scripts):
    api = scripts
    body = request_body("short_drama")
    body.update(quote_id=authorize(api, body), confirmed=True)
    response = api.client.post("/api/v1/generation/scripts", headers=api.admin_headers, json=body)
    assert response.status_code == 202, response.text
    identifier = response.json()["attempt_id"]
    result = api.client.get(f"/api/v1/generation/scripts/{identifier}").json()
    assert result["status"] == "completed"
    assert len(result["production_plan"]["characters"]) == 2
    assert result["production_plan"]["content_plan"]["review_status"] == "draft"
    assert result["calculated_cost_usd"] == "0.022"
    assert result["actual_cost_usd"] is None
    api.publisher.assert_not_called()
    assert JobService(api.db_path).list_jobs() == []
    assert api.script_calls.call_count == 1
    assert api.client.post("/api/v1/generation/scripts", headers=api.admin_headers, json=body).status_code == 422
    assert api.script_calls.call_count == 1
    revised = request_body("short_drama")
    revised.update(parent_attempt_id=identifier, revision_instructions="Make the ending warmer")
    quote = api.client.post("/api/v1/generation/script-quotes", headers=api.admin_headers, json=revised)
    assert quote.status_code == 200
    revised.update(quote_id=quote.json()["quote_id"], confirmed=True)
    response = api.client.post("/api/v1/generation/scripts", headers=api.admin_headers, json=revised)
    assert response.status_code == 202
    assert api.script_calls.call_count == 2
    assert api.client.get("/api/v1/generation/scripts").json()["attempts"][0]["parent_attempt_id"] == identifier


def test_paid_script_local_video_workflow_and_no_youtube_for_topic(scripts, monkeypatch):
    api = scripts
    body = request_body(purpose="video")
    quote = authorize(api, body)
    response = api.client.post("/api/v1/jobs", headers=api.admin_headers, json={
        "category": "science", "limit": 5, "generation_config": body["configuration"],
        "script_quote_id": quote, "script_cost_confirmed": True,
    })
    assert response.status_code == 202, response.text
    identifier = response.json()["job_id"]
    trend = Mock()
    renderer = Mock()
    output = api.tmp_path / "fixture.mp4"
    output.write_bytes(b"fixture")
    renderer.generate.return_value = output
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    workflow = ContentWorkflow(job_service=JobService(api.db_path), trend_agent=trend,
        content_storage=ContentStorage(api.db_path, api.tmp_path / "plans"),
        video_agent_factory=lambda mode: renderer)
    workflow.run(identifier)
    assert JobService(api.db_path).get_job(identifier)["status"] == "pending_review"
    trend.discover.assert_not_called()
    assert api.script_calls.call_count == 1
    api.publisher.assert_not_called()


def test_generator_key_cannot_start_paid_job(scripts):
    api = scripts
    api.client.cookies.clear()
    response = api.client.post("/api/v1/jobs", headers=api.api_headers, json={"generation_config": request_body()["configuration"]})
    assert response.status_code == 403
    api.script_calls.assert_not_called()


def test_local_topic_video_without_cloud_or_youtube_keys(generation_api, monkeypatch):
    api = generation_api
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    response = api.client.post("/api/v1/jobs", headers=api.api_headers, json={
        "generation_config": GenerationConfig(source_mode="topic", topic="A local explanation").model_dump(mode="json")
    })
    assert response.status_code == 202, response.text
    assert JobService(api.db_path).get_job(response.json()["job_id"])["generation_config"]["topic"] == "A local explanation"
