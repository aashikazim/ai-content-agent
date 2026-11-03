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
    for path in ['videos','references']:
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
    assert [item['id'] for item in catalog['video_providers']]==['local','google']
