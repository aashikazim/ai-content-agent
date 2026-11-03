"""Official SDK wire contracts and production lifecycle; all cloud I/O mocked."""
import json
from unittest.mock import Mock, MagicMock

import httpx
import pytest
from runwayml import RunwayML

from app.models.generation_config import GenerationConfig
from app.services.cloud_video_storage import CloudVideoStorage
from app.services.gemini_video_service import VideoProviderError
from app.services.provider_catalog import validate_for_draft, validate_for_execution
from app.services.runway_models import MODELS, estimate, ratio
from app.services.runway_video_service import RunwayVideoService
from tests.test_gemini_video import video_service

TASK = 'ee65b49b-b955-43fc-95f4-002d6c834755'


def config(**changes):
    return GenerationConfig(video_provider='runway', video_model='gen4.5', **changes)


def adapter(configuration, handler):
    client = RunwayML(api_key='private-fixture-secret', max_retries=0,
        base_url='https://api.dev.runwayml.com',
        http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False))
    return RunwayVideoService(configuration, client=client)


@pytest.mark.parametrize('name,mode', [(name, mode) for name, info in MODELS.items() for mode in info['input_modes']])
def test_sdk_model_payloads(name, mode):
    conf = config().model_copy(update={'video_model': name, 'video_input_mode': mode})
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['X-Runway-Version'] == '2024-11-06'
        assert request.headers['authorization'] == 'Bearer private-fixture-secret'
        return httpx.Response(200, json={'id': TASK})
    provider = adapter(conf, handler)
    snapshot = provider.submit('A person walks through a sunlit garden.', aspect_ratio='9:16', duration=5,
                               start_image=b'normalized-png' if mode == 'image' else None)
    assert snapshot['interaction_id'] == TASK
    assert len(requests) == 1
    assert requests[0].url.path == f'/v1/{mode}_to_video'
    body = json.loads(requests[0].content)
    assert body['model'] == name and body['duration'] == 5
    assert 'private-fixture' not in json.dumps(body)
    if name != 'kling2.5_turbo_pro' or mode != 'image':
        assert body['ratio'] == ratio(conf)
    else:
        assert 'ratio' not in body
    assert ('promptImage' in body) == (mode == 'image')
    if MODELS[name]['audio'] and not MODELS[name].get('always_audio'):
        assert body['audio'] is False
    else:
        assert 'audio' not in body


@pytest.mark.parametrize('failure', ['timeout', 500, 408, 409, 429, 403])
def test_sdk_paid_request_never_retries(failure):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('private-fixture-secret', request=request)
        return httpx.Response(failure, json={'error': 'private-fixture-secret'})
    with pytest.raises(VideoProviderError) as error:
        adapter(config(), handler).submit('Visible moving clouds', aspect_ratio='9:16', duration=5)
    assert len(calls) == 1
    assert error.value.uncertain == (failure in ['timeout', 500, 408, 409])
    assert 'private-fixture' not in str(error.value)


def test_production_sdk_configuration_ignores_proxy_and_base_url_overrides(monkeypatch):
    monkeypatch.setenv('RUNWAYML_API_SECRET', 'fixture')
    monkeypatch.setenv('RUNWAYML_BASE_URL', 'https://attacker.invalid')
    provider = RunwayVideoService(config())
    sdk = provider.sdk()
    assert sdk.max_retries == 0
    assert str(sdk.base_url) == 'https://api.dev.runwayml.com'
    assert not sdk._client.follow_redirects
    assert provider.session.trust_env is False
    assert 'Authorization' not in provider.session.headers
    sdk.close()


@pytest.mark.parametrize('state', ['PENDING', 'THROTTLED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'])
def test_poll_task_and_numeric_credits(state):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == '/v1/organization':
            return httpx.Response(200, json={'creditBalance': 1234})
        return httpx.Response(200, json={'id': TASK, 'createdAt': '2026-11-03T12:00:00Z', 'status': state,
            'cost': {'credits': 60}, 'estimatedCost': {'credits': 60},
            'output': ['https://example.cloudfront.net/output.mp4?private-signature=fixture'],
            'failure': 'private-fixture-secret', 'failureCode': 'SAFETY.INPUT.TEXT'})
    provider = adapter(config(), handler)
    assert provider.check_model()['available_credits'] == 1234
    snapshot = provider.retrieve(TASK)
    assert all(request.method == 'GET' for request in calls)
    assert snapshot['status'] == ('completed' if state == 'SUCCEEDED' else 'failed' if state == 'FAILED' else 'cancelled' if state == 'CANCELLED' else 'in_progress')
    assert 'private-fixture' not in str(snapshot.get('provider_error'))
    assert snapshot['usage']['credits'] == 60


def test_download_is_bounded_atomic_and_without_api_credentials(tmp_path, monkeypatch):
    session = MagicMock()
    response = session.get.return_value.__enter__.return_value
    response.status_code = 200
    response.iter_content.return_value = [b'video-fixture']
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('18.1.2.3', 443))])
    provider = RunwayVideoService(config(), session=session)
    path = tmp_path / 'clip.mp4'
    provider.download({'output': ['https://example.cloudfront.net/video.mp4?signature=private']}, path)
    assert path.read_bytes() == b'video-fixture'
    assert session.get.call_args.kwargs['headers'] == {}
    assert session.get.call_args.kwargs['allow_redirects'] is False
    response.status_code = 403
    with pytest.raises(VideoProviderError, match='fresh link'):
        provider.download({'output': ['https://example.cloudfront.net/video.mp4']}, tmp_path / 'new.mp4')
    assert not (tmp_path / 'new.mp4').exists() and not (tmp_path / 'new.part').exists()


@pytest.mark.parametrize('url', ['http://example.cloudfront.net/a', 'https://127.0.0.1/a',
    'https://user:pass@example.cloudfront.net/a', 'https://attacker.invalid/a', 'https://example.cloudfront.net:8000/a'])
def test_download_rejects_untrusted_urls_without_network(url, tmp_path):
    session = Mock()
    with pytest.raises(VideoProviderError, match='unsupported'):
        RunwayVideoService(config(), session=session).download({'output': [url]}, tmp_path / 'v.mp4')
    session.get.assert_not_called()


def test_model_capabilities_costs_and_independent_script_selection(monkeypatch):
    monkeypatch.setenv('RUNWAYML_API_SECRET', 'fixture')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture')
    for script, name in [('ollama', 'qwen3:8b'), ('openai', 'gpt-6.1-sol')]:
        validate_for_execution(config(script_provider=script, script_model=name))
    assert str(estimate(config(), 5)) == '0.60'


@pytest.mark.parametrize('changes,message', [
    ({'video_model': 'gen4_turbo'}, 'requires image'),
    ({'narration': 'model_audio'}, 'no generated audio'),
    ({'aspect_ratio': '1:1'}, 'square needs image'),
    ({'video_model': 'unverified'}, 'Unknown Runway'),
])
def test_unsupported_settings_fail_before_paid_requests(changes, message):
    with pytest.raises(ValueError, match=message):
        validate_for_draft(config().model_copy(update=changes))


@pytest.fixture
def runway_service(video_service, monkeypatch):
    instance, fake, _ = video_service
    monkeypatch.setenv('RUNWAYML_API_SECRET', 'fixture')
    with instance.storage.connect() as conn:
        row = conn.execute('SELECT request_json FROM script_generation_attempts').fetchone()
        request = json.loads(row[0])
        request['configuration'].update(video_provider='runway', video_model='gen4.5')
        conn.execute('UPDATE script_generation_attempts SET request_json=?', (json.dumps(request),))
    fake.check_model = Mock(return_value={'available_credits': 10000})
    return instance, fake


def test_runway_quote_confirmation_ids_polling_and_regeneration(runway_service):
    instance, fake = runway_service
    quote = instance.quote('script-fixture', 'admin')
    assert quote['model'] == 'gen4.5' and quote['provider_label'] == 'Runway'
    assert quote['estimate_usd'] == '5.40' and quote['estimated_credits'] == '540.00'
    assert fake.posts == 0
    with pytest.raises(ValueError, match='confirmation'):
        instance.create(quote['quote_id'], 'admin', False)
    value = instance.create(quote['quote_id'], 'admin', True)
    value = instance.process(value['production_id'], submit_queued=True)
    assert all(shot['provider'] == 'runway' and shot['model'] == 'gen4.5' for shot in value['shots'])
    assert all(shot['generation_id'] for shot in value['shots'])
    posts = fake.posts
    value = instance.process(value['production_id'])
    gets = fake.gets
    instance.process(value['production_id'])  # No second GET within five seconds.
    assert fake.posts == posts and fake.gets == gets
    assert value['status'] == 'technical_ready'
    assert instance.jobs.get_job(value['job_id'])['status'] == 'awaiting_quality_review'
    for operation in ['edit', 'extend']:
        with pytest.raises(ValueError, match='regeneration only'):
            instance.quote_change(value['production_id'], 'admin', 1, operation=operation)
    change_quote = instance.quote_change(value['production_id'], 'admin', 1, instructions='Sunlit waves')
    candidate = instance.change(change_quote['quote_id'], 'admin', True)
    assert instance.storage.attempt(candidate['attempt_id'])['estimate_usd'] == '0.96'
    original = [shot['attempt_id'] for shot in value['shots'] if shot['selected']]
    value = instance.process(value['production_id'], submit_queued=True, attempt_ids=[candidate['attempt_id']])
    assert [shot['attempt_id'] for shot in value['shots'] if shot['selected']] == original
    assert fake.posts == posts + 1


def test_insufficient_credits_and_image_requirements_block_quotes(runway_service):
    instance, fake = runway_service
    fake.check_model.return_value = {'available_credits': 1}
    with pytest.raises(ValueError, match='Insufficient Runway credits'):
        instance.quote('script-fixture', 'admin')
    with instance.storage.connect() as conn:
        request = json.loads(conn.execute('SELECT request_json FROM script_generation_attempts').fetchone()[0])
        request['configuration'].update(video_input_mode='image')
        conn.execute('UPDATE script_generation_attempts SET request_json=?', (json.dumps(request),))
    with pytest.raises(ValueError, match='start frame for every shot'):
        instance.quote('script-fixture', 'admin')
    assert fake.posts == 0


def test_uncertain_runway_submit_remains_single_use(runway_service):
    instance, fake = runway_service
    quote = instance.quote('script-fixture', 'admin')
    value = instance.create(quote['quote_id'], 'admin', True)
    fake.fail = True
    value = instance.process(value['production_id'], submit_queued=True)
    assert value['status'] == 'needs_reconciliation' and fake.posts == 1
    instance.process(value['production_id'])
    with pytest.raises(ValueError, match='Reconcile'):
        instance.process(value['production_id'], submit_queued=True)
    with pytest.raises(ValueError, match='already used'):
        instance.create(quote['quote_id'], 'admin', True)
    assert fake.posts == 1


def test_provider_migration_preserves_legacy_shots_and_is_idempotent(video_service):
    instance, _, _ = video_service
    value = instance.create(instance.quote('script-fixture', 'admin')['quote_id'], 'admin', True)
    with instance.storage.connect() as conn:
        for column in ['provider', 'model', 'last_polled_at']:
            conn.execute(f'ALTER TABLE video_shots DROP COLUMN {column}')
    for _ in range(2):
        storage = CloudVideoStorage(instance.storage.db_path)
        migrated = storage.get(value['production_id'])
        assert len(migrated['shots']) == len(value['shots'])
        assert all(shot['provider'] == 'google' for shot in migrated['shots'])


def test_numeric_runway_costs_are_saved_without_signed_urls(runway_service):
    instance, _ = runway_service
    value = instance.create(instance.quote('script-fixture', 'admin')['quote_id'], 'admin', True)
    attempt = value['shots'][0]['attempt_id']
    instance.record(attempt, {'interaction_id': TASK, 'status': 'completed', 'provider': 'runway',
        'usage': {'credits': 96}, 'output': ['https://example.cloudfront.net/video?private=signature']})
    saved = instance.storage.attempt(attempt)
    assert saved['calculated_cost_usd'] == '0.96'
    assert saved['actual_cost_usd'] is None
    assert 'signature' not in json.dumps(instance.storage.get(value['production_id']))


def test_langgraph_reuses_runway_script_without_discovery_or_local_render(runway_service, monkeypatch):
    from app.workflows.content_workflow import ContentWorkflow
    instance, fake = runway_service
    value = instance.create(instance.quote('script-fixture','admin')['quote_id'], 'admin', True)
    monkeypatch.setattr('app.services.cloud_video_service.CloudVideoService', lambda path: instance)
    trends, content, local = Mock(), Mock(), Mock()
    workflow = ContentWorkflow(job_service=instance.jobs, trend_agent=trends, content_agent=content,
                               content_storage=instance.content_storage, video_agent_factory=local)
    workflow.run(value['job_id'])
    assert fake.posts == len(value['shots'])
    assert not trends.mock_calls and not content.mock_calls and not local.mock_calls
    assert instance.jobs.get_job(value['job_id'])['status'] != 'approved'


def test_runway_account_balance_can_be_unavailable_but_never_fabricated():
    provider = adapter(config(), lambda request: httpx.Response(403, json={'error':'private-fixture'}))
    result = provider.check_model()
    assert result['available_credits'] is None and 'unverified' in result['credit_notice']
    assert 'private-fixture' not in json.dumps(result)


def test_runway_download_rejects_private_dns_results(tmp_path, monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('127.0.0.1', 443))])
    session = Mock()
    with pytest.raises(VideoProviderError, match='non-public'):
        RunwayVideoService(config(), session=session).download({'output':['https://example.cloudfront.net/clip.mp4']}, tmp_path/'clip.mp4')
    session.get.assert_not_called()


def test_runway_model_duration_budgets_and_audio_rates():
    from decimal import Decimal
    from app.services.runway_models import validate
    for name in MODELS:
        conf = config().model_copy(update={'video_model':name, 'video_input_mode':'image'})
        for duration in [1,31]:
            with pytest.raises(ValueError, match='duration'):
                validate(conf, duration)
    with pytest.raises(ValueError, match='duration'):
        estimate(config().model_copy(update={'video_model':'kling2.5_turbo_pro'}), 8)
    silent = config().model_copy(update={'video_model':'kling3.0_standard'})
    assert estimate(silent, 5) == Decimal('.45')
    assert estimate(silent.model_copy(update={'narration':'model_audio'}), 5) == Decimal('.65')
    kling4 = config().model_copy(update={'video_model':'kling_4', 'resolution':'720p'})
    assert estimate(kling4, 5) == Decimal('.79')  # Round list price up per task; no promotional assumption.
    assert estimate(kling4.model_copy(update={'resolution':'1080p'}), 5) == Decimal('1.18')


def test_simultaneous_cloud_reads_migrate_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'concurrent.db'
    CloudVideoStorage(path)
    with CloudVideoStorage(path).connect() as conn:
        for column in ['provider', 'model', 'last_polled_at']:
            conn.execute(f'ALTER TABLE video_shots DROP COLUMN {column}')
    with ThreadPoolExecutor(max_workers=3) as pool:
        stores = list(pool.map(lambda _: CloudVideoStorage(path), range(3)))
    with stores[0].connect() as conn:
        columns = [row['name'] for row in conn.execute('PRAGMA table_info(video_shots)')]
    assert all(columns.count(column) == 1 for column in ['provider', 'model', 'last_polled_at'])
