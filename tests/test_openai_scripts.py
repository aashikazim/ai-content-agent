import json
import sqlite3
import time
from unittest.mock import Mock

import pytest
import requests

from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan, strict_production_schema
from app.services.generation_storage import GenerationStorage
from app.services.job_service import JobService
from app.services.openai_script_service import OpenAIScriptService, PaidScriptError
from app.services.script_generation_service import ScriptGenerationService
from tests.test_content_plan import valid_data


def production(content_type="realistic_short"):
    data = {"schema_version": 1, "content_type": content_type,
            "factual_status": "unverified", "outline": ["Hook", "Explanation"],
            "characters": [], "dialogue": [], "content_plan": valid_data()}
    if content_type == "short_drama":
        data.update(factual_status="fiction", characters=[
            {"character_id": "actor_a", "name": "Alex", "description": "Fictional explorer", "voice_notes": "Calm"},
            {"character_id": "actor_b", "name": "Sam", "description": "Fictional explorer", "voice_notes": "Curious"},
        ], dialogue=[{"scene_number": 1, "character_id": "actor_a", "text": "Introduction"},
                     {"scene_number": 2, "character_id": "actor_b", "text": "Explanation"}])
    return ProductionPlan.model_validate(data)


def response(body, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    return result


def completed():
    return {"id": "resp_fixture", "status": "completed", "usage": {
        "input_tokens": 1000, "output_tokens": 2000,
        "output_tokens_details": {"reasoning_tokens": 500}},
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": production().model_dump_json()}]}]}


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-private-do-not-reflect")
    config = GenerationConfig(script_provider="openai", script_model="gpt-6.1-sol",
                              source_mode="topic", topic="An original story")
    return config


def test_responses_adapter_strict_json_usage_and_no_secret_in_payload(cloud):
    session = Mock()
    session.post.return_value = response(completed())
    adapter = OpenAIScriptService(session=session)
    result = adapter.generate_production_plan("Write the requested script")
    assert result.content_plan.review_status == "draft"
    payload = session.post.call_args.kwargs["json"]
    assert payload["model"] == "gpt-6.1-sol"
    assert payload["text"]["format"]["strict"] is True
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 8000
    assert "test-private" not in json.dumps(payload)
    session.post.assert_called_once()
    assert adapter.last_usage["output_tokens_details"]["reasoning_tokens"] == 500


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503, 408])
def test_cloud_errors_are_sanitized_and_never_retried(cloud, status):
    session = Mock()
    session.post.return_value = response({"error": "test-private-do-not-reflect"}, status)
    with pytest.raises(PaidScriptError) as error:
        OpenAIScriptService(session=session).generate_production_plan("Fixture")
    assert "test-private" not in str(error.value)
    session.post.assert_called_once()


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError()])
def test_transport_uncertainty_never_retries(cloud, failure):
    session = Mock()
    session.post.side_effect = failure
    with pytest.raises(PaidScriptError) as error:
        OpenAIScriptService(session=session).generate_production_plan("Fixture")
    assert error.value.uncertain is True
    session.post.assert_called_once()


@pytest.mark.parametrize("change", ["incomplete", "refusal", "invalid"])
def test_incomplete_refusal_invalid_output_retain_usage(cloud, change):
    body = completed()
    if change == "incomplete":
        body["status"] = "incomplete"
    elif change == "refusal":
        body["output"][0]["content"] = [{"type": "refusal", "refusal": "Provider message"}]
    else:
        body["output"][0]["content"][0]["text"] = "{}"
    session = Mock()
    session.post.return_value = response(body)
    adapter = OpenAIScriptService(session=session)
    with pytest.raises(PaidScriptError):
        adapter.generate_production_plan("Fixture")
    assert adapter.last_usage["input_tokens"] == 1000
    session.post.assert_called_once()


def test_model_access_is_checked_without_generation(cloud):
    session = Mock()
    session.get.return_value = response({"data": [{"id": "gpt-6-sol"}, {"id": "embedding"}]})
    adapter = OpenAIScriptService(session=session)
    assert adapter.available_models() == ["gpt-6-sol"]
    with pytest.raises(PaidScriptError, match="unavailable"):
        adapter.check_model()
    session.post.assert_not_called()


def test_missing_key_does_not_send_anything(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    session = Mock()
    with pytest.raises(PaidScriptError, match="not configured"):
        OpenAIScriptService(session=session).generate_production_plan("Fixture")
    session.post.assert_not_called()


def test_every_strict_object_is_closed_and_all_fields_required():
    schema = strict_production_schema()
    def check(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            assert "default" not in value
            for child in value.values(): check(child)
        elif isinstance(value, list):
            for child in value: check(child)
    check(schema)


@pytest.mark.parametrize("bad", ["actor", "scene", "duration", "verified", "approval"])
def test_production_draft_validation(bad):
    data = production("short_drama").model_dump(mode="json")
    if bad == "actor": data["dialogue"][0]["character_id"] = "missing"
    if bad == "scene": data["dialogue"][0]["scene_number"] = 12
    if bad == "verified": data["factual_status"] = "unverified"
    if bad == "approval": data["content_plan"]["review_status"] = "approved"
    if bad == "duration":
        plan = ProductionPlan.model_validate(data)
        with pytest.raises(ValueError, match="duration"):
            plan.validate_configuration(GenerationConfig(content_type="short_drama", duration_seconds=30))
    else:
        with pytest.raises(ValueError): ProductionPlan.model_validate(data)


class FakeProvider:
    calls = 0
    fail = False
    last_usage = completed()["usage"]
    last_response_id = "resp_fixture"
    def __init__(self, model): self.model = model
    def check_model(self): pass
    def generate_production_plan(self, prompt):
        type(self).calls += 1
        if self.fail: raise PaidScriptError("Uncertain fixture", uncertain=True)
        return production()


def service(tmp_path):
    FakeProvider.calls = 0
    FakeProvider.fail = False
    return ScriptGenerationService(tmp_path / "test.db", openai_factory=FakeProvider)


def test_quote_single_use_binding_and_cached_result(cloud, tmp_path):
    scripts = service(tmp_path)
    request = scripts.request(cloud)
    quote = scripts.quote(request, "admin")
    identifier = scripts.submit(request, "admin", quote_id=quote["quote_id"], confirmed=True)
    scripts.run(identifier)
    scripts.run(identifier)
    assert FakeProvider.calls == 1
    result = scripts.get(identifier)
    assert result["status"] == "completed"
    assert result["reasoning_tokens"] == 500
    assert result["calculated_cost_usd"] == "0.022"
    assert result["actual_cost_usd"] is None
    with pytest.raises(ValueError, match="used"):
        scripts.submit(request, "admin", quote_id=quote["quote_id"], confirmed=True)


@pytest.mark.parametrize("change", ["configuration", "actor", "expiry", "confirmation"])
def test_paid_authorization_rejects_changes_before_request(cloud, tmp_path, change):
    scripts = service(tmp_path)
    request = scripts.request(cloud)
    quote = scripts.quote(request, "admin")
    if change == "configuration": request["configuration"]["topic"] = "Changed topic"
    if change == "expiry":
        with scripts.storage.connect() as conn:
            conn.execute("UPDATE script_generation_quotes SET expires_at = 0")
    with pytest.raises(ValueError):
        scripts.submit(request, "other" if change == "actor" else "admin",
                       quote_id=quote["quote_id"], confirmed=change != "confirmation")
    assert FakeProvider.calls == 0
    assert scripts.list_attempts() == []


def test_uncertain_attempt_retains_authorization_and_does_not_resubmit(cloud, tmp_path):
    scripts = service(tmp_path)
    request = scripts.request(cloud)
    quote = scripts.quote(request, "admin")
    identifier = scripts.submit(request, "admin", quote_id=quote["quote_id"], confirmed=True)
    FakeProvider.fail = True
    scripts.run(identifier)
    scripts.run(identifier)
    assert FakeProvider.calls == 1
    assert scripts.get(identifier)["status"] == "needs_reconciliation"
    with pytest.raises(ValueError): scripts.submit(request, "admin", quote_id=quote["quote_id"], confirmed=True)


def test_job_creation_is_atomic_when_paid_confirmation_missing(cloud, tmp_path):
    jobs = JobService(tmp_path / "test.db")
    with pytest.raises(ValueError, match="confirmation"):
        jobs.create_job(generation_config=cloud, actor="admin")
    assert jobs.list_jobs() == []
    assert GenerationStorage(jobs.db_path).get_for_job("missing") is None


def test_local_script_draft_needs_no_cloud_key_or_quote(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    FakeProvider.fail = False
    scripts = ScriptGenerationService(tmp_path / "local.db", local_factory=FakeProvider)
    request = scripts.request(GenerationConfig(source_mode="topic", topic="A local idea"))
    identifier = scripts.submit(request, "admin")
    scripts.run(identifier)
    assert scripts.get(identifier)["status"] == "completed"


def test_interrupted_attempt_is_visible_for_reconciliation(cloud, tmp_path):
    scripts = service(tmp_path)
    request = scripts.request(cloud)
    quote = scripts.quote(request, "admin")
    identifier = scripts.submit(request, "admin", quote_id=quote["quote_id"], confirmed=True)
    with scripts.storage.connect() as conn:
        conn.execute("UPDATE script_generation_attempts SET updated_at = ?", (int(time.time()) - 600,))
    assert scripts.get(identifier)["status"] == "needs_reconciliation"
    scripts.run(identifier)
    assert FakeProvider.calls == 0


def test_active_paid_request_blocks_another_and_reconciliation_requires_human(cloud, tmp_path):
    scripts = service(tmp_path)
    request = scripts.request(cloud)
    quote = scripts.quote(request, 'admin')
    identifier = scripts.submit(request, 'admin', quote_id=quote['quote_id'], confirmed=True)
    second_quote = scripts.quote(request, 'admin')
    with pytest.raises(ValueError, match='already active'):
        scripts.submit(request, 'admin', quote_id=second_quote['quote_id'], confirmed=True)
    FakeProvider.fail = True
    scripts.run(identifier)
    with pytest.raises(ValueError, match='reconciliation'):
        scripts.submit(request, 'admin', quote_id=second_quote['quote_id'], confirmed=True)
    with pytest.raises(ValueError, match='Confirm'):
        scripts.reconcile(identifier, 'admin', 'Inspected usage', False)
    reconciled = scripts.reconcile(identifier, 'admin', 'Inspected provider usage', True)
    assert reconciled['status'] == 'failed'
    assert reconciled['response_id'] == 'resp_fixture'
    scripts.submit(request, 'admin', quote_id=second_quote['quote_id'], confirmed=True)
    assert FakeProvider.calls == 1


def test_script_schema_upgrade_preserves_existing_rows(tmp_path):
    path = tmp_path / 'old.db'
    scripts = service(tmp_path)
    # A pre-cost-tracking schema must gain the additive column without data loss.
    with scripts.storage.connect() as conn:
        conn.execute('ALTER TABLE script_generation_attempts DROP COLUMN calculated_cost_usd')
        conn.execute("INSERT INTO script_generation_attempts (attempt_id,actor,request_json,provider,model,status,created_at,updated_at) VALUES ('historical','admin','{}','ollama','qwen3:8b','failed',1,1)")
    upgraded = ScriptGenerationService(scripts.storage.db_path)
    assert upgraded.get('historical')['model'] == 'qwen3:8b'
    assert upgraded.get('historical')['calculated_cost_usd'] is None
    ScriptGenerationService(scripts.storage.db_path)
    assert len(upgraded.list_attempts()) == 1


def test_unknown_response_status_requires_reconciliation(cloud):
    session = Mock()
    body = completed()
    body['status'] = 'in_progress'
    session.post.return_value = response(body)
    with pytest.raises(PaidScriptError) as error:
        OpenAIScriptService(session=session).generate_production_plan('Fixture')
    assert error.value.uncertain
    session.post.assert_called_once()


@pytest.mark.parametrize('content_type,label', [('news', 'unverified'), ('animated_comedy', 'satire')])
def test_content_labels_and_word_budget_are_enforced(content_type, label):
    data = production().model_dump(mode='json')
    data.update(content_type=content_type, factual_status=label)
    plan = ProductionPlan.model_validate(data)
    plan.validate_configuration(GenerationConfig(content_type=content_type))
    plan.content_plan.scenes[0].narration = 'word ' * 200
    with pytest.raises(ValueError, match='word budgets'):
        plan.validate_configuration(GenerationConfig(content_type=content_type))


@pytest.mark.parametrize('source_field', ['topic', 'supplied_script'])
def test_local_content_agent_supports_supplied_material_without_trends(source_field):
    from app.agents.content_agent import ContentAgent
    llm = Mock()
    llm.generate_plan.return_value = production().content_plan
    plan = ContentAgent(llm=llm).create_plan([], target_duration_seconds=45,
                                             **{source_field: 'My original material'})
    assert plan.source_video_ids == []
    assert 'My original material' in llm.generate_plan.call_args.args[0]
    assert plan.review_status == 'draft'
