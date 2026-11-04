"""Claude and cinematic integration tests: HTTP, models and rendering are mocked."""
import json
from unittest.mock import Mock

import pytest
import requests

from app.models.generation_config import GenerationConfig
from app.services.anthropic_script_service import AnthropicScriptService, ANTHROPIC_MODEL, production_schema
from app.services.openai_script_service import PaidScriptError
from app.services.provider_catalog import get_catalog, validate_for_script
from app.services.script_generation_service import ScriptGenerationService
from tests.test_gemini_video import cloud_production
from tests.test_long_drama import long_production
from tests.test_openai_scripts import response
from tests.test_generation_api import generation_api, sign_in
from tests.test_local_cinematic import local_service


@pytest.fixture
def configuration(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'private-claude-test-key')
    return GenerationConfig(script_provider='anthropic', script_model=ANTHROPIC_MODEL,
        video_provider='local_cinematic', video_model='ltx-2.5', source_mode='topic',
        topic='An original moving story', fps=24, resolution='720p', narration='model_audio')


def completed(plan=None):
    return {'id': 'msg_fixture', 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1000, 'output_tokens': 2000},
            'content': [{'type': 'text', 'text': (plan or cloud_production()).model_dump_json()}]}


def test_messages_payload_schema_and_usage(configuration):
    session = Mock()
    session.post.return_value = response(completed())
    adapter = AnthropicScriptService(session=session)
    result = adapter.generate_production_plan('Write moving shots').validate_configuration(configuration)
    assert result.content_plan.review_status == 'draft'
    session.post.assert_called_once()
    payload = session.post.call_args.kwargs['json']
    assert payload['model'] == ANTHROPIC_MODEL
    assert payload['max_tokens'] == 8000
    assert payload['output_config']['format']['type'] == 'json_schema'
    assert payload['output_config']['format']['schema']['additionalProperties'] is False
    assert 'private-claude' not in json.dumps(payload)
    assert session.post.call_args.kwargs['allow_redirects'] is False
    assert adapter.last_response_id == 'msg_fixture'
    # The API grammar omits unsupported bounds, while our local model retains them.
    schema = production_schema()
    assert 'maxLength' not in schema['$defs']['CharacterProfile']['properties']['name']
    assert 'maxLength=80' in schema['$defs']['CharacterProfile']['properties']['name']['description']


@pytest.mark.parametrize('status', [401, 403, 404, 429, 408, 409, 500, 529])
def test_errors_sanitized_no_retry(configuration, status):
    session = Mock()
    session.post.return_value = response({'error': 'private-claude-test-key'}, status)
    with pytest.raises(PaidScriptError) as exc:
        AnthropicScriptService(session=session).generate_production_plan('Story')
    assert exc.value.uncertain is (status >= 500 or status in {408, 409})
    assert 'private-claude' not in str(exc.value)
    session.post.assert_called_once()


@pytest.mark.parametrize('body', [
    {'stop_reason': 'max_tokens'}, {'stop_reason': 'refusal'},
    {'stop_reason': 'end_turn', 'stop_details': {'type': 'refusal'}},
    {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '{}'}]},
])
def test_incomplete_refused_and_invalid_are_not_repaired_automatically(configuration, body):
    session = Mock()
    session.post.return_value = response(completed() | body)
    with pytest.raises(PaidScriptError):
        AnthropicScriptService(session=session).generate_production_plan('Story')
    session.post.assert_called_once()


def test_model_check_does_not_generate_and_missing_key_is_clear(configuration, monkeypatch):
    session = Mock()
    session.get.return_value = response({'id': ANTHROPIC_MODEL})
    AnthropicScriptService(session=session).check_model()
    session.post.assert_not_called()
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    with pytest.raises(ValueError, match='ANTHROPIC_API_KEY'):
        validate_for_script(configuration)
    with pytest.raises(PaidScriptError, match='ANTHROPIC_API_KEY'):
        AnthropicScriptService(session=session).generate_production_plan('Story')
    session.post.assert_not_called()


@pytest.mark.parametrize('duration', [45, 120, 180, 240])
def test_authorized_scripts_persist_and_obey_short_long_contracts(configuration, tmp_path, duration):
    config = configuration.model_copy(update={'duration_seconds': duration,
        'content_type': 'long_drama' if duration >= 120 else 'realistic_short',
        'max_shots': 60 if duration >= 120 else 12})
    plan = long_production(((duration + 7) // 8) * 8) if duration >= 120 else cloud_production()
    if duration >= 120 and duration % 8:
        # The existing fixture uses 8-second shots; trim its last shot to the exact target.
        plan.content_plan.scenes[-1].duration_seconds -= 8 - duration % 8
        plan.shots[-1].duration_seconds -= 8 - duration % 8
    session = Mock()
    session.get.return_value = response({'id': ANTHROPIC_MODEL})
    session.post.return_value = response(completed(plan))
    service = ScriptGenerationService(tmp_path / 'test.db',
        anthropic_factory=lambda model: AnthropicScriptService(model, session=session))
    request = service.request(config)
    quote = service.quote(request, 'admin')
    assert quote['paid'] and quote['provider'] == 'anthropic'
    session.post.assert_not_called()
    with pytest.raises(ValueError, match='confirmation'):
        service.submit(request, 'admin')
    identifier = service.submit(request, 'admin', quote_id=quote['quote_id'], confirmed=True)
    service.run(identifier)
    service.run(identifier)  # A second task delivery must never resend the paid POST.
    saved = service.get(identifier)
    assert saved['status'] == 'completed', saved['error']
    assert saved['response_id'] == 'msg_fixture'
    assert saved['calculated_cost_usd'] == '0.022'
    assert saved['actual_cost_usd'] is None
    assert saved['production_plan']['content_plan']['review_status'] == 'draft'
    assert session.post.call_args.kwargs['json']['max_tokens'] == (64000 if duration >= 120 else 8000)
    session.post.assert_called_once()
    with pytest.raises(ValueError):
        service.submit(request, 'admin', quote_id=quote['quote_id'], confirmed=True)


def test_timeout_requires_reconciliation_before_new_identical_request(configuration, tmp_path):
    session = Mock()
    session.get.return_value = response({'id': ANTHROPIC_MODEL})
    session.post.side_effect = requests.Timeout('private-claude-test-key')
    service = ScriptGenerationService(tmp_path / 'test.db',
        anthropic_factory=lambda model: AnthropicScriptService(model, session=session))
    request = service.request(configuration)
    quote = service.quote(request, 'admin')
    identifier = service.submit(request, 'admin', quote_id=quote['quote_id'], confirmed=True)
    service.run(identifier)
    assert service.get(identifier)['status'] == 'needs_reconciliation'
    assert 'private-claude' not in json.dumps(service.get(identifier))
    service.run(identifier)
    quote = service.quote(request, 'admin')
    with pytest.raises(ValueError, match='reconciliation'):
        service.submit(request, 'admin', quote_id=quote['quote_id'], confirmed=True)
    session.post.assert_called_once()


def test_oversized_input_and_low_budget_never_generate(configuration, tmp_path):
    session = Mock()
    with pytest.raises(PaidScriptError, match='input limit'):
        AnthropicScriptService(session=session).generate_production_plan('x' * 60000)
    session.post.assert_not_called()
    service = ScriptGenerationService(tmp_path / 'test.db',
        anthropic_factory=lambda model: AnthropicScriptService(model, session=session))
    request = service.request(configuration.model_copy(update={'max_cost_usd': 0}))
    with pytest.raises(ValueError, match='budget'):
        service.quote(request, 'admin')
    session.get.assert_not_called()


def test_claude_draft_feeds_mocked_local_video_and_manual_review(configuration, local_service):
    videos = local_service
    session = Mock()
    session.get.return_value = response({'id': ANTHROPIC_MODEL})
    session.post.return_value = response(completed())
    scripts = ScriptGenerationService(videos.storage.db_path,
        anthropic_factory=lambda model: AnthropicScriptService(model, session=session))
    request = scripts.request(configuration)
    identifier = scripts.submit(request, 'admin', quote_id=scripts.quote(request, 'admin')['quote_id'], confirmed=True)
    scripts.run(identifier)
    quote = videos.quote(identifier, 'admin')
    assert quote['paid_request'] is False
    production = videos.create(quote['quote_id'], 'admin', True)
    videos.process(production['production_id'], submit_queued=True)
    finished = videos.process(production['production_id'])
    assert finished['status'] == 'technical_ready'
    assert videos.jobs.get_job(finished['job_id'])['status'] == 'awaiting_quality_review'
    assert finished['configuration']['script_provider'] == 'anthropic'
    originals = [shot['attempt_id'] for shot in finished['shots'] if shot['selected']]
    regenerate = videos.quote_change(finished['production_id'], 'admin', 1)
    candidate = videos.change(regenerate['quote_id'], 'admin', True)
    videos.process(finished['production_id'], submit_queued=True, attempt_ids=[candidate['attempt_id']])
    videos.process(finished['production_id'])
    assert [shot['attempt_id'] for shot in videos.storage.get(finished['production_id'])['shots'] if shot['selected']] == originals
    session.post.assert_called_once()  # Regenerating video never regenerates the paid screenplay.


def test_catalog_does_not_expose_claude_key(configuration):
    catalog = get_catalog()
    provider = next(item for item in catalog['script_providers'] if item['id'] == 'anthropic')
    assert provider['configured'] and provider['implemented']
    assert provider['models'][0]['id'] == ANTHROPIC_MODEL
    assert 'private-claude' not in json.dumps(catalog)


def test_claude_api_requires_admin_csrf_and_single_use_authorization(generation_api, monkeypatch):
    api = generation_api
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'private-api-test-key')
    monkeypatch.setattr(AnthropicScriptService, 'check_model', lambda self: None)
    calls = Mock()
    def generate(self, prompt):
        calls(prompt)
        self.last_response_id = 'msg_api_fixture'
        self.last_usage = completed()['usage']
        return cloud_production()
    monkeypatch.setattr(AnthropicScriptService, 'generate_production_plan', generate)
    config = GenerationConfig(script_provider='anthropic', script_model=ANTHROPIC_MODEL,
        source_mode='topic', topic='My own moving story', video_provider='local_cinematic',
        video_model='ltx-2.5', fps=24, narration='model_audio')
    body = {'configuration': config.model_dump(mode='json')}
    assert api.client.post('/api/v1/generation/script-quotes', headers=api.api_headers, json=body).status_code == 401
    headers = sign_in(api)
    assert api.client.post('/api/v1/generation/script-quotes', json=body).status_code == 403
    quote = api.client.post('/api/v1/generation/script-quotes', headers=headers, json=body)
    assert quote.status_code == 200, quote.text
    calls.assert_not_called()
    body['quote_id'] = quote.json()['quote_id']
    assert api.client.post('/api/v1/generation/scripts', headers=headers, json=body).status_code == 422
    body['confirmed'] = True
    created = api.client.post('/api/v1/generation/scripts', headers=headers, json=body)
    assert created.status_code == 202, created.text
    saved = api.client.get('/api/v1/generation/scripts/' + created.json()['attempt_id']).json()
    assert saved['status'] == 'completed'
    assert saved['response_id'] == 'msg_api_fixture'
    assert saved['configuration']['video_provider'] == 'local_cinematic'
    assert saved['production_plan']['content_plan']['review_status'] == 'draft'
    assert api.client.post('/api/v1/generation/scripts', headers=headers, json=body).status_code == 422
    calls.assert_called_once()
    api.execute.assert_not_called()
    api.publisher.assert_not_called()
    api.client.cookies.clear()
    assert api.client.post('/api/v1/jobs', headers=api.api_headers,
        json={'generation_config': config.model_dump(mode='json')}).status_code == 403
