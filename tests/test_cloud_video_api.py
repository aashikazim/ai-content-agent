import base64
import io
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from PIL import Image

from app.api import cloud_routes
from app.models.generation_config import GenerationConfig
from app.services.cloud_video_service import CloudVideoService
from app.services.script_generation_service import ScriptGenerationService
from tests.test_generation_api import generation_api, sign_in
from tests.test_gemini_video import cloud_production, FakeVideoProvider


@pytest.fixture
def cloud_api(generation_api, monkeypatch):
    api = generation_api
    monkeypatch.setenv('GEMINI_API_KEY','test-only-google-key')
    monkeypatch.setenv('OPENAI_API_KEY','test-only-openai-key')
    monkeypatch.setenv('CLOUD_VIDEOS_DIR', str(api.tmp_path/'cloud'))
    monkeypatch.setenv('CHARACTER_REFERENCES_DIR', str(api.tmp_path/'references'))
    config = GenerationConfig(script_provider='openai',script_model='gpt-6.1-sol',video_provider='google',video_model='gemini-omni-1.1-flash',fps=24,source_mode='topic',topic='Ocean waves')
    scripts = ScriptGenerationService(api.db_path)
    identifier = str(uuid4())
    with scripts.storage.connect() as conn:
        conn.execute('''INSERT INTO script_generation_attempts (attempt_id,actor,request_json,provider,model,status,result_json,created_at,updated_at)
            VALUES (?,'generation-admin',?,'openai','gpt-6.1-sol','completed',?,1,1)''',
            (identifier,json.dumps(scripts.request(config)),cloud_production().model_dump_json()))
    provider = FakeVideoProvider()
    renderer = Mock()
    output = api.tmp_path/'assembled.mp4'; output.write_bytes(b'fixture')
    renderer.assemble.return_value=(output,{'technical_passed':True})
    instance = CloudVideoService(api.db_path,provider=provider,renderer_factory=lambda config:renderer)
    monkeypatch.setattr(cloud_routes,'service',lambda:instance)
    monkeypatch.setattr('app.services.cloud_video_service.validate_clip',lambda *args,**kwargs:{'technical_passed':True})
    api.cloud_service=instance;api.provider=provider;api.script_id=identifier
    api.headers=sign_in(api)
    return api


def authorize(api):
    result=api.client.post('/api/v1/generation/video-quotes',json={'script_attempt_id':api.script_id},headers=api.headers)
    assert result.status_code==200,result.text
    return result.json()['quote_id']


def create(api):
    result=api.client.post('/api/v1/generation/videos',json={'quote_id':authorize(api),'confirmed':True},headers=api.headers)
    assert result.status_code==202,result.text
    return result.json()


def test_cloud_endpoints_require_session_and_csrf(generation_api):
    api=generation_api
    for path in ['videos','references','runway/credits']:
        assert api.client.get('/api/v1/generation/'+path,headers=api.api_headers).status_code==401
    sign_in(api)
    assert api.client.post('/api/v1/generation/video-quotes',json={'script_attempt_id':str(uuid4())}).status_code==403
    assert api.client.post('/api/v1/generation/references',json={}).status_code==403


def test_cloud_quote_and_creation_are_separate_and_do_not_repeat_script(cloud_api):
    api=cloud_api
    quote=authorize(api)
    assert api.provider.posts==0
    assert api.client.post('/api/v1/generation/videos',json={'quote_id':quote},headers=api.headers).status_code==422
    result=api.client.post('/api/v1/generation/videos',json={'quote_id':quote,'confirmed':True},headers=api.headers)
    assert result.status_code==202,result.text
    api.execute.assert_called_once_with(result.json()['job_id'])
    api.publisher.assert_not_called()
    assert api.provider.posts==0  # Worker is mocked in this fixture.
    assert api.client.post('/api/v1/generation/videos',json={'quote_id':quote,'confirmed':True},headers=api.headers).status_code==422
    history=api.client.get('/api/v1/generation/videos').json()['productions']
    assert len(history)==1
    assert 'test-only-google-key' not in json.dumps(history)


def test_status_retrieval_never_submits_a_queued_shot(cloud_api):
    api=cloud_api
    value=create(api)
    identifier=value['production_id']
    response=api.client.post(f'/api/v1/generation/videos/{identifier}/refresh',json={},headers=api.headers)
    assert response.status_code==200
    assert api.provider.posts==0
    api.cloud_service.process(identifier,submit_queued=True)
    response=api.client.post(f'/api/v1/generation/videos/{identifier}/refresh',json={},headers=api.headers)
    assert response.status_code==200
    assert api.provider.posts==5 and api.provider.gets==5
    value=api.client.get(f'/api/v1/generation/videos/{identifier}').json()
    assert value['status']=='technical_ready'
    response=api.client.post(f'/api/v1/generation/videos/{identifier}/review',json={'continuity_confirmed':True,'captions_confirmed':True,'rights_confirmed':True},headers=api.headers)
    assert response.status_code==200
    assert api.cloud_service.jobs.get_job(value['job_id'])['status']=='pending_review'
    api.publisher.assert_not_called()


def test_reference_upload_normalizes_image_and_requires_rights(cloud_api):
    api=cloud_api
    image=Image.new('RGB',(256,256),'blue'); buffer=io.BytesIO();image.save(buffer,format='PNG')
    body={'character_id':'actor_a','name':'Alex','description':'Fictional explorer','rights_note':'Original test fixture','image_base64':base64.b64encode(buffer.getvalue()).decode()}
    assert api.client.post('/api/v1/generation/references',json=body,headers=api.headers).status_code==422
    response=api.client.post('/api/v1/generation/references',json={**body,'rights_confirmed':True},headers=api.headers)
    assert response.status_code==201,response.text
    value=response.json()
    assert 'image_path' not in value and 'image_base64' not in value
    preview=api.client.get(value['image_url'])
    assert preview.status_code==200 and preview.headers['content-type']=='image/png'
    api.client.cookies.clear()
    assert api.client.get(value['image_url']).status_code==401


def test_missing_keys_retired_kling_and_bad_shot_plans_cannot_bill(cloud_api,monkeypatch):
    api=cloud_api
    monkeypatch.delenv('GEMINI_API_KEY')
    assert api.client.post('/api/v1/generation/video-quotes',json={'script_attempt_id':api.script_id},headers=api.headers).status_code==422
    assert api.provider.posts==0
    catalog=api.client.get('/api/v1/generation/catalog').json()
    assert [item['id'] for item in catalog['video_providers']]==['local','google','runway']


def test_runway_image_quote_submission_and_manual_review_are_separate(cloud_api, monkeypatch):
    api = cloud_api
    monkeypatch.setenv('RUNWAYML_API_SECRET', 'test-only-runway-key')
    monkeypatch.delenv('GEMINI_API_KEY')
    with api.cloud_service.storage.connect() as conn:
        request = json.loads(conn.execute('SELECT request_json FROM script_generation_attempts').fetchone()[0])
        request['configuration'].update(video_provider='runway', video_model='gen4_turbo', video_input_mode='image')
        conn.execute('UPDATE script_generation_attempts SET request_json=?', (json.dumps(request),))
    api.provider.check_model = Mock(return_value={'available_credits': 10000})
    # Neither a scheduler key nor missing CSRF can authorize paid video.
    api.client.cookies.clear()
    assert api.client.post('/api/v1/generation/video-quotes', json={'script_attempt_id': api.script_id}, headers=api.api_headers).status_code == 401
    response = api.client.post('/api/v1/auth/login', json={'username':'generation-admin','password':'a-long-temporary-admin-password'})
    api.headers = {'X-CSRF-Token': response.json()['csrf_token']}
    assert api.client.post('/api/v1/generation/video-quotes', json={'script_attempt_id': api.script_id}, headers=api.headers).status_code == 422
    image = Image.new('RGB', (256, 512), 'blue'); buffer = io.BytesIO(); image.save(buffer, format='PNG')
    response = api.client.post('/api/v1/generation/references', headers=api.headers, json={
        'character_id':'scene_1', 'name':'Original scene frame', 'description':'Ocean scene',
        'rights_note':'Synthetic test fixture', 'rights_confirmed':True, 'image_base64':base64.b64encode(buffer.getvalue()).decode()})
    assert response.status_code == 201
    reference = response.json()['reference_id']
    response = api.client.post('/api/v1/generation/video-quotes', headers=api.headers,
        json={'script_attempt_id':api.script_id, 'start_image_reference_id':reference})
    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote['provider'] == 'runway' and quote['available_credits'] == 10000
    assert api.provider.posts == 0
    assert api.client.post('/api/v1/generation/videos', headers=api.headers,
        json={'quote_id':quote['quote_id'], 'confirmed':False}).status_code == 422
    created = api.client.post('/api/v1/generation/videos', headers=api.headers,
        json={'quote_id':quote['quote_id'], 'confirmed':True})
    assert created.status_code == 202, created.text
    production = api.cloud_service.process(created.json()['production_id'], submit_queued=True)
    production = api.cloud_service.process(production['production_id'])
    assert production['status'] == 'technical_ready'
    assert all(shot['provider'] == 'runway' and shot['generation_id'] for shot in production['shots'])
    response = api.client.post(f"/api/v1/generation/videos/{production['production_id']}/review", headers=api.headers,
        json={'continuity_confirmed':True,'captions_confirmed':True,'rights_confirmed':True})
    assert response.status_code == 200
    assert api.cloud_service.jobs.get_job(production['job_id'])['status'] == 'pending_review'
    api.publisher.assert_not_called()


def test_runway_credit_lookup_is_read_only_and_private(generation_api, monkeypatch):
    api = generation_api
    sign_in(api)
    lookup = Mock(return_value={'available_credits': 1234})
    monkeypatch.setattr('app.services.runway_video_service.RunwayVideoService.check_model', lookup)
    response = api.client.get('/api/v1/generation/runway/credits')
    assert response.status_code == 200 and response.json() == {'available_credits':1234}
    lookup.assert_called_once()
    api.execute.assert_not_called()
    api.publisher.assert_not_called()


def test_long_drama_quotes_generates_and_stops_at_separate_manual_review(cloud_api,monkeypatch):
    from tests.test_long_drama import long_config,long_production
    api=cloud_api
    monkeypatch.setenv('RUNWAYML_API_SECRET','test-runway-key')
    config=long_config()
    scripts=ScriptGenerationService(api.db_path)
    with scripts.storage.connect() as conn:
        conn.execute('UPDATE script_generation_attempts SET request_json=?,result_json=? WHERE attempt_id=?',
            (json.dumps(scripts.request(config)),long_production().model_dump_json(),api.script_id))
    api.provider.check_model=Mock(return_value={'available_credits':100000})
    response=api.client.post('/api/v1/generation/video-quotes',headers=api.headers,json={'script_attempt_id':api.script_id})
    assert response.status_code==200,response.text
    quote=response.json()
    assert quote['shot_count']==15 and quote['model']=='kling3.0_standard'
    response=api.client.post('/api/v1/generation/videos',headers=api.headers,json={'quote_id':quote['quote_id'],'confirmed':True})
    assert response.status_code==202,response.text
    identifier=response.json()['production_id']
    api.cloud_service.process(identifier,submit_queued=True)
    value=api.cloud_service.process(identifier)
    assert value['status']=='technical_ready' and len(value['shots'])==15
    assert api.cloud_service.jobs.get_job(value['job_id'])['status']=='awaiting_quality_review'
    details=api.client.get(f"/api/v1/jobs/{value['job_id']}/details").json()
    assert len(details['plan']['scenes'])==15
    response=api.client.post(f'/api/v1/generation/videos/{identifier}/review',headers=api.headers,
        json={'continuity_confirmed':True,'captions_confirmed':True,'rights_confirmed':True})
    assert response.status_code==200,response.text
    assert api.cloud_service.jobs.get_job(value['job_id'])['status']=='pending_review'
    api.publisher.assert_not_called()


def test_runway_edit_quote_respects_explicit_model_without_paid_submission(cloud_api,monkeypatch):
    import hashlib
    from pathlib import Path
    api=cloud_api
    monkeypatch.setenv('RUNWAYML_API_SECRET','fixture')
    with api.cloud_service.storage.connect() as conn:
        request=json.loads(conn.execute('SELECT request_json FROM script_generation_attempts').fetchone()[0])
        request['configuration'].update(video_provider='runway',video_model='gen4.5')
        conn.execute('UPDATE script_generation_attempts SET request_json=?',(json.dumps(request),))
    api.provider.check_model=Mock(return_value={'available_credits':10000})
    value=create(api)
    api.cloud_service.process(value['production_id'],submit_queued=True)
    value=api.cloud_service.process(value['production_id'])
    parent=value['shots'][0]
    row=api.cloud_service.storage.attempt(parent['attempt_id'])
    with api.cloud_service.storage.connect() as conn:
        conn.execute('UPDATE video_shots SET media_quality_json=? WHERE attempt_id=?',
            (json.dumps({'sha256':hashlib.sha256(Path(row['clip_path']).read_bytes()).hexdigest()}),parent['attempt_id']))
    posts=api.provider.posts
    response=api.client.post(f"/api/v1/generation/videos/{value['production_id']}/shot-quotes",headers=api.headers,
        json={'shot_number':1,'operation':'edit','instructions':'Change the lighting','edit_model':'klingO3_pro'})
    assert response.status_code==200,response.text
    assert response.json()['model']=='klingO3_pro'
    assert response.json()['estimate_usd']=='1.36'
    assert api.provider.posts==posts
    api.publisher.assert_not_called()
