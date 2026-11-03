"""No paid calls: mock HTTP contracts, temporary SQLite and synthetic clips."""
import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan
from app.services.gemini_video_service import GeminiVideoService, VideoProviderError
from app.services.cloud_video_service import CloudVideoService
from app.services.script_generation_service import ScriptGenerationService
from tests.test_openai_scripts import production


def cloud_production():
    data = production().model_dump(mode='json')
    data['shots'] = []
    index = 1
    for scene in data['content_plan']['scenes']:
        import math
        count = math.ceil(scene['duration_seconds'] / 10)
        base, remainder = divmod(scene['duration_seconds'], count)
        durations = [base + (1 if item < remainder else 0) for item in range(count)]
        for part, duration in enumerate(durations):
            data['shots'].append({'shot_number': index, 'scene_number': scene['scene_number'], 'duration_seconds': duration,
                'action': 'A moving ocean wave', 'camera': 'Slow tracking shot', 'spoken_text': scene['narration'] if part == 0 else '', 'character_ids': []})
            index += 1
    return ProductionPlan.model_validate(data)


def response(data, status=200):
    item = Mock(status_code=status)
    item.__enter__ = Mock(return_value=item)
    item.__exit__ = Mock(return_value=False)
    item.iter_content.return_value = [json.dumps(data).encode()]
    return item


def rest(status='in_progress', data=None):
    return {'id': 'v1_fixture', 'status': status, 'steps': [{'type': 'model_output', 'content':
        [{'type': 'video', 'mime_type': 'video/mp4', 'data': data}] if data else []}],
        'usage': {'total_input_tokens': 100, 'total_output_tokens': 57920,
                  'output_tokens_by_modality': [{'modality': 'video', 'tokens': 57920}]}}


def test_google_background_payload_and_safe_download(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'private-fixture-never-reflect')
    session = Mock()
    session.request.side_effect = [response(rest()), response(rest('completed', base64.b64encode(b'fixture-video').decode()))]
    provider = GeminiVideoService(session)
    submitted = provider.submit('Generate a moving shot for exactly 10 seconds', aspect_ratio='9:16')
    payload = session.request.call_args.kwargs['json']
    assert payload['model'] == 'gemini-omni-1.1-flash'
    assert payload['background'] is True and payload['store'] is True
    assert payload['response_format']['resolution'] == '720p'
    assert 'duration' not in payload['response_format']  # undocumented string syntax
    assert 'private-fixture' not in json.dumps(payload)
    assert provider.retrieve(submitted['interaction_id'])['status'] == 'completed'
    session.request.assert_called_with('GET', 'https://generativelanguage.googleapis.com/v1beta/interactions/v1_fixture',
        headers=provider.headers(), json=None, timeout=(5,60), allow_redirects=False, stream=True)
    provider.download({'interaction_id': 'v1_fixture', 'video': {'data': base64.b64encode(b'fixture-video').decode()}}, tmp_path / 'video.mp4')
    assert (tmp_path / 'video.mp4').read_bytes() == b'fixture-video'


@pytest.mark.parametrize('status', [401,403,404,429,500,503,408])
def test_paid_http_failures_never_retry_or_reflect_key(monkeypatch, status):
    monkeypatch.setenv('GEMINI_API_KEY', 'private-fixture-never-reflect')
    session = Mock()
    session.request.return_value = response({'error': 'private-fixture-never-reflect'}, status)
    with pytest.raises(VideoProviderError) as error:
        GeminiVideoService(session).submit('A moving shot', aspect_ratio='9:16')
    assert 'private-fixture' not in str(error.value)
    assert error.value.uncertain == (status >= 500 or status == 408)
    assert session.request.call_count == 1


def test_timeout_and_missing_key_never_retry(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fixture')
    session = Mock()
    session.request.side_effect = requests.Timeout('private credentials in raw exception')
    with pytest.raises(VideoProviderError) as error:
        GeminiVideoService(session).submit('A moving shot', aspect_ratio='16:9')
    assert error.value.uncertain and 'credentials' not in str(error.value)
    session.request.assert_called_once()
    monkeypatch.delenv('GEMINI_API_KEY')
    session.reset_mock()
    with pytest.raises(VideoProviderError, match='not configured'):
        GeminiVideoService(session).submit('A moving shot', aspect_ratio='16:9')
    session.request.assert_not_called()


def test_media_download_never_sends_key_to_foreign_host(tmp_path):
    session = Mock()
    provider = GeminiVideoService(session)
    provider.retrieve = Mock(return_value={'video': {'uri': 'https://attacker.invalid/video?token=secret'}})
    with pytest.raises(VideoProviderError, match='unsupported'):
        provider.download({'interaction_id':'v1_fixture'}, tmp_path / 'video.mp4')
    session.get.assert_not_called()
    assert not (tmp_path / 'video.mp4').exists()


class FakeVideoProvider:
    def __init__(self): self.posts = 0; self.gets = 0; self.fail = False
    def check_model(self): pass
    def submit(self, *args, **kwargs):
        self.posts += 1
        if self.fail: raise VideoProviderError('Unknown submission fixture', uncertain=True)
        return {'interaction_id': f'v1_{self.posts}', 'status': 'in_progress', 'usage': {}}
    def retrieve(self, identifier):
        self.gets += 1
        return {'interaction_id': identifier, 'status': 'completed', 'usage': {}, 'video': {'data': 'fixture'}}
    def download(self, snapshot, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b'synthetic fixture')


@pytest.fixture
def video_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GEMINI_API_KEY', 'fixture')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture')
    monkeypatch.setenv('CONTENT_PLANS_DIR', str(tmp_path / 'plans'))
    monkeypatch.setenv('CLOUD_VIDEOS_DIR', str(tmp_path / 'clips'))
    monkeypatch.setenv('CHARACTER_REFERENCES_DIR', str(tmp_path / 'references'))
    config = GenerationConfig(script_provider='openai', script_model='gpt-6.1-sol', video_provider='google',
                              video_model='gemini-omni-1.1-flash', fps=24, source_mode='topic', topic='Ocean waves')
    scripts = ScriptGenerationService(tmp_path / 'test.db')
    # Insert a completed, previously-authorized script: no LLM call.
    with scripts.storage.connect() as conn:
        conn.execute('''INSERT INTO script_generation_attempts (attempt_id,actor,request_json,provider,model,status,result_json,created_at,updated_at)
            VALUES ('script-fixture','admin',?,'openai','gpt-6.1-sol','completed',?,1,1)''',
            (json.dumps(scripts.request(config)), cloud_production().model_dump_json()))
    fake = FakeVideoProvider()
    renderer = Mock()
    output = tmp_path / 'assembled.mp4'
    output.write_bytes(b'fixture')
    renderer.assemble.return_value = (output, {'technical_passed': True})
    instance = CloudVideoService(tmp_path / 'test.db', provider=fake, renderer_factory=lambda config: renderer)
    monkeypatch.setattr('app.services.cloud_video_service.validate_clip', lambda *args, **kwargs: {'technical_passed':True})
    return instance, fake, renderer


def test_production_reuses_script_and_only_submits_each_shot_once(video_service):
    instance, fake, renderer = video_service
    quote = instance.quote('script-fixture', 'admin')
    with pytest.raises(ValueError, match='confirmation'):
        instance.create(quote['quote_id'], 'admin', False)
    created = instance.create(quote['quote_id'], 'admin', True)
    assert len(created['shots']) == 5
    with pytest.raises(ValueError, match='already used'):
        instance.create(quote['quote_id'], 'admin', True)
    value = instance.process(created['production_id'], submit_queued=True)
    assert fake.posts == 5 and value['status'] == 'processing'
    value = instance.process(created['production_id'])
    assert fake.posts == 5 and fake.gets == 5
    assert value['status'] == 'technical_ready'
    assert instance.jobs.get_job(created['job_id'])['status'] == 'awaiting_quality_review'
    assert instance.jobs.get_job(created['job_id'])['status'] != 'approved'
    instance.process(created['production_id'], submit_queued=True)
    assert fake.posts == 5
    renderer.assemble.assert_called_once()


def test_uncertain_submission_stops_batch_without_retry(video_service):
    instance, fake, renderer = video_service
    fake.fail = True
    created = instance.create(instance.quote('script-fixture', 'admin')['quote_id'], 'admin', True)
    value = instance.process(created['production_id'], submit_queued=True)
    assert value['status'] == 'needs_reconciliation'
    assert fake.posts == 1
    instance.process(created['production_id'])
    assert fake.posts == 1
    renderer.assemble.assert_not_called()


def test_quote_actor_expiry_and_budget_are_enforced(video_service):
    instance, fake, renderer = video_service
    quote = instance.quote('script-fixture', 'admin')
    with pytest.raises(ValueError, match='administrator'):
        instance.create(quote['quote_id'], 'other', True)
    with instance.storage.connect() as conn:
        conn.execute('UPDATE video_generation_quotes SET expires_at=0')
    with pytest.raises(ValueError, match='expired'):
        instance.create(quote['quote_id'], 'admin', True)
    assert fake.posts == 0
    with instance.storage.connect() as conn:
        row = conn.execute('SELECT request_json FROM script_generation_attempts').fetchone()
        request = json.loads(row[0]); request['configuration']['max_cost_usd'] = '0.01'
        conn.execute('UPDATE script_generation_attempts SET request_json=?', (json.dumps(request),))
    with pytest.raises(ValueError, match='exceeds'):
        instance.quote('script-fixture','admin')
    assert instance.jobs.list_jobs() == []


def completed_production(instance):
    value = instance.create(instance.quote('script-fixture', 'admin')['quote_id'], 'admin', True)
    instance.process(value['production_id'], submit_queued=True)
    return instance.process(value['production_id'])


def test_regenerate_preserves_original_until_explicit_selection(video_service):
    instance, fake, renderer = video_service
    value = completed_production(instance)
    original = value['shots'][0]
    quote = instance.quote_change(value['production_id'], 'admin', 1, instructions='A wider camera angle')
    changed = instance.change(quote['quote_id'], 'admin', True)
    instance.process(value['production_id'], submit_queued=True, attempt_ids=[changed['attempt_id']])
    value = instance.process(value['production_id'])
    assert value['status'] == 'candidate_ready'
    assert next(item for item in value['shots'] if item['attempt_id'] == original['attempt_id'])['selected']
    candidate = next(item for item in value['shots'] if item['attempt_id'] == changed['attempt_id'])
    assert not candidate['selected']
    assert candidate['version'] == 2
    value = instance.select(value['production_id'], changed['attempt_id'])
    assert value['status'] == 'technical_ready'
    assert not next(item for item in value['shots'] if item['attempt_id'] == original['attempt_id'])['selected']
    assert instance.storage.attempt(original['attempt_id'])['clip_path']
    assert fake.posts == 6


def test_quality_review_is_manual_and_never_approval(video_service):
    instance, fake, renderer = video_service
    value = completed_production(instance)
    with pytest.raises(ValueError, match='Inspect'):
        instance.send_to_review(value['production_id'], continuity_confirmed=False, captions_confirmed=True, rights_confirmed=True)
    value = instance.send_to_review(value['production_id'], continuity_confirmed=True, captions_confirmed=True, rights_confirmed=True)
    assert value['status'] == 'pending_review'
    assert instance.jobs.get_job(value['job_id'])['status'] == 'pending_review'
    instance.jobs.review_job(value['job_id'], 'approved')
    with pytest.raises(ValueError, match='locked'):
        instance.quote_change(value['production_id'], 'admin', 1)


def test_extension_retains_parent_and_requires_a_valid_trim_window(video_service):
    instance, fake, renderer = video_service
    value = completed_production(instance)
    quote = instance.quote_change(value['production_id'], 'admin', 1, operation='extend', extension_seconds=3)
    changed = instance.change(quote['quote_id'], 'admin', True)
    attempt = instance.storage.attempt(changed['attempt_id'])
    request = json.loads(attempt['request_json'])
    assert request['target_duration'] == request['shot']['duration_seconds'] + 3
    instance.process(value['production_id'], submit_queued=True, attempt_ids=[changed['attempt_id']])
    instance.process(value['production_id'])
    with pytest.raises(ValueError, match='Trim window'):
        instance.select(value['production_id'], changed['attempt_id'], 4)
    value = instance.select(value['production_id'], changed['attempt_id'], 3)
    assert next(item for item in value['shots'] if item['attempt_id'] == changed['attempt_id'])['trim_start_seconds'] == 3


def test_uncertain_attempt_needs_human_reconciliation_and_new_quote(video_service):
    instance, fake, renderer = video_service
    fake.fail = True
    value = instance.create(instance.quote('script-fixture', 'admin')['quote_id'], 'admin', True)
    value = instance.process(value['production_id'], submit_queued=True)
    uncertain = value['shots'][0]
    with pytest.raises(ValueError, match='Resolve'):
        instance.quote_change(value['production_id'], 'admin', 1)
    with pytest.raises(ValueError, match='Inspect'):
        instance.reconcile(value['production_id'], uncertain['attempt_id'], 'admin', 'Inspected usage', False)
    instance.reconcile(value['production_id'], uncertain['attempt_id'], 'admin', 'Inspected provider usage; no interaction ID found', True)
    fake.fail = False
    quote = instance.quote_change(value['production_id'], 'admin', 1)
    changed = instance.change(quote['quote_id'], 'admin', True)
    instance.process(value['production_id'], submit_queued=True, attempt_ids=[changed['attempt_id']])
    assert fake.posts == 2  # Other originally queued shots were not submitted by regeneration.
    with pytest.raises(ValueError, match='Resolve'):
        instance.continue_queued(value['production_id'], True)


def test_cloud_tables_migrate_existing_database_without_losing_jobs(video_service):
    instance, fake, renderer = video_service
    value = completed_production(instance)
    with instance.storage.connect() as conn:
        conn.execute('ALTER TABLE video_shots DROP COLUMN media_quality_json')
        conn.execute('ALTER TABLE video_shots DROP COLUMN trim_start_seconds')
    from app.services.cloud_video_storage import CloudVideoStorage
    upgraded = CloudVideoStorage(instance.storage.db_path)
    CloudVideoStorage(instance.storage.db_path)
    assert len(upgraded.get(value['production_id'])['shots']) == 5
    assert instance.jobs.get_job(value['job_id'])['status'] == 'awaiting_quality_review'


def test_video_usage_includes_separate_thought_tokens_and_keeps_invoice_unknown(video_service):
    instance,fake,renderer=video_service
    value=instance.create(instance.quote('script-fixture','admin')['quote_id'],'admin',True)
    attempt=value['shots'][0]['attempt_id']
    usage={'total_input_tokens':100,'total_output_tokens':5802,'total_thought_tokens':49,
           'output_tokens_by_modality':[{'modality':'video','tokens':5792},{'modality':'text','tokens':10}]}
    instance.record(attempt,{'interaction_id':'v1_usage','status':'in_progress','usage':usage})
    row=instance.storage.attempt(attempt)
    assert row['calculated_cost_usd']=='0.102041'
    assert row['actual_cost_usd'] is None
    usage['total_output_tokens']=9000
    instance.record(attempt,{'interaction_id':'v1_usage','status':'in_progress','usage':usage})
    assert instance.storage.attempt(attempt)['calculated_cost_usd'] is None


def test_drama_requires_and_reuses_matching_character_references(video_service):
    import io
    from PIL import Image
    from tests.test_openai_scripts import production as screenplay
    instance,fake,renderer=video_service
    data=screenplay('short_drama').model_dump(mode='json')
    data['shots']=cloud_production().model_dump(mode='json')['shots']
    for shot in data['shots']:
        shot['character_ids']=['actor_a' if shot['scene_number']==1 else 'actor_b']
    with instance.storage.connect() as conn:
        row=conn.execute('SELECT request_json FROM script_generation_attempts').fetchone()
        request=json.loads(row[0]);request['configuration'].update(content_type='short_drama',narration='model_audio')
        conn.execute('UPDATE script_generation_attempts SET request_json=?,result_json=?',(json.dumps(request),ProductionPlan.model_validate(data).model_dump_json()))
    with pytest.raises(ValueError,match='every fictional character'):
        instance.quote('script-fixture','admin')
    image=Image.new('RGB',(256,256),'green');buffer=io.BytesIO();image.save(buffer,format='PNG')
    identifiers=[]
    for character in ('actor_a','actor_b'):
        reference=instance.references.create({'character_id':character,'name':character,'description':'Fictional explorer','rights_note':'Original synthetic test image',
            'rights_confirmed':True,'image_base64':base64.b64encode(buffer.getvalue()).decode()})
        identifiers.append(reference['reference_id'])
    quote=instance.quote('script-fixture','admin',identifiers)
    value=instance.create(quote['quote_id'],'admin',True)
    original=fake.submit;fake.submit=Mock(wraps=original)
    instance.process(value['production_id'],submit_queued=True)
    assert all(len(call.kwargs['references'])==2 for call in fake.submit.call_args_list)
    assert fake.posts==5


def test_process_lease_prevents_duplicate_paid_submissions(video_service):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    instance,fake,renderer=video_service
    value=instance.create(instance.quote('script-fixture','admin')['quote_id'],'admin',True)
    entered,released=threading.Event(),threading.Event()
    original=fake.submit
    def submit(*args,**kwargs):
        entered.set(); assert released.wait(5); return original(*args,**kwargs)
    fake.submit=submit
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(instance.process,value['production_id'],submit_queued=True)
        assert entered.wait(5)
        second=pool.submit(instance.process,value['production_id'],submit_queued=True)
        assert second.result(timeout=5)['production_id']==value['production_id']
        released.set();first.result(timeout=5)
    assert fake.posts==5


def test_confirmations_are_strict_and_reference_change_cannot_bill(video_service):
    instance,fake,renderer=video_service
    quote=instance.quote('script-fixture','admin')
    with pytest.raises(ValueError,match='confirmation'):
        instance.create(quote['quote_id'],'admin','true')
    with instance.storage.connect() as conn:
        conn.execute("UPDATE video_generation_quotes SET request_hash='changed'")
    with pytest.raises(ValueError,match='changed'):
        instance.create(quote['quote_id'],'admin',True)
    assert fake.posts==0


def test_human_can_close_known_uncertain_attempt_without_retry_or_losing_id(video_service):
    instance,fake,renderer=video_service
    value=instance.create(instance.quote('script-fixture','admin')['quote_id'],'admin',True)
    attempt=value['shots'][0]['attempt_id']
    instance.record(attempt,{'interaction_id':'v1_existing','status':'needs_reconciliation','usage':{}})
    value=instance.reconcile(value['production_id'],attempt,'admin','Inspected usage; closing locally without cancelling provider work',True,close_attempt=True)
    shot=next(item for item in value['shots'] if item['attempt_id']==attempt)
    assert shot['status']=='failed' and shot['interaction_id']=='v1_existing'
    instance.process(value['production_id'])
    assert fake.posts==0 and fake.gets==0


def test_discarding_unsubmitted_shots_never_spends_or_retries_unknown_request(video_service):
    instance,fake,renderer=video_service
    fake.fail=True
    value=instance.create(instance.quote('script-fixture','admin')['quote_id'],'admin',True)
    value=instance.process(value['production_id'],submit_queued=True)
    value=instance.discard_queued(value['production_id'],True)
    assert sum(shot['status']=='cancelled' for shot in value['shots'])==4
    assert value['shots'][0]['status']=='needs_reconciliation'
    assert fake.posts==1
    instance.process(value['production_id'])
    assert fake.posts==1
