"""Clip Studio uses the existing database; mock E2E uses CPU FFmpeg, never models."""
import io
import json
import time
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from PIL import Image
import pytest
from pydantic import ValidationError

from app.services.clip_studio.generators import MockGenerator
from app.services.clip_studio.planner import MockPlanner, validate_plan
from app.services.clip_studio.schemas import ClipPlan, ProjectRequest, youtube_url
from app.services.clip_studio.service import ClipStudioService
from app.services.cloud_video_renderer import media_sha256
from app.services.job_service import JobService
from tests.test_cloud_media import fixture_video
from tests.test_generation_api import generation_api, sign_in


@pytest.fixture
def studio(generation_api, monkeypatch):
    monkeypatch.setenv('CLIP_STUDIO_MOCK', 'true')
    monkeypatch.setenv('CLIP_STUDIO_DATA_DIR', str(generation_api.tmp_path/'clip-media'))
    monkeypatch.setattr(ClipStudioService, 'launch', Mock())
    return generation_api


def specification(kind='youtube', provider='anthropic'):
    return {'request_id': str(uuid4()), 'instruction': 'Create hopeful cinematic clips', 'source_kind': kind,
            'source_url': 'https://youtu.be/abcdefghijk' if kind == 'youtube' else None,
            'rights_confirmed': True, 'settings': {'planner_provider': provider,
            'planner_model': 'claude-sonnet-5-5' if provider == 'anthropic' else 'gpt-6-sol',
            'video_model': 'wan', 'clip_count': 2, 'duration_seconds': 2, 'aspect_ratio': '9:16', 'seed': 42}}


def png():
    data = io.BytesIO(); Image.new('RGB', (128, 192), 'navy').save(data, format='PNG')
    return data.getvalue()


def create(api, payload=None, files=None):
    headers = sign_in(api)
    response = api.client.post('/api/v1/clip-studio/projects', headers=headers,
                               data={'payload': json.dumps(payload or specification())}, files=files)
    assert response.status_code == 202, response.text
    return response.json(), headers


@pytest.mark.parametrize('url', ['http://youtu.be/abcdefghijk', 'https://localhost/watch?v=abcdefghijk',
    'https://youtube.com.evil.test/watch?v=abcdefghijk', 'https://user:password@youtube.com/watch?v=abcdefghijk',
    'https://www.youtube.com/playlist?list=abcdef', 'https://www.youtube.com/redirect?q=http://127.0.0.1'])
def test_source_url_is_not_a_generic_fetch_endpoint(url):
    with pytest.raises(ValueError): youtube_url(url)


def test_strict_plan_checks_ranges_types_duration_and_image_semantics():
    request = ProjectRequest.model_validate(specification())
    sources = [{'metadata_json': json.dumps({'duration_seconds': 4})}]
    valid = MockPlanner().plan(request, sources).model_dump(mode='json')
    assert len(validate_plan(valid, request, 4).clips) == 2
    for bad in ({'source_end': 5}, {'source_start': -1}, {'source_start': '0'},
                {'source_start': float('nan')}, {'reference_mode': 'unknown'}, {'duration_seconds': 3},
                {'unexpected': 'field'}):
        value = json.loads(json.dumps(valid)); value['clips'][0].update(bad)
        with pytest.raises((ValueError, ValidationError)): validate_plan(value, request, 4)
    with pytest.raises(ValueError): validate_plan({'clips': valid['clips'][:1]}, request, 4)
    image_request = ProjectRequest.model_validate(specification('images'))
    with pytest.raises(ValueError): validate_plan(valid, image_request)
    assert MockPlanner().plan(image_request, [{'metadata_json': '{}'}]).clips[0].source_start is None


def test_auth_rights_and_mock_opt_in_before_work(studio, monkeypatch):
    api = studio
    assert api.client.get('/api/v1/clip-studio/catalog').status_code == 401
    assert api.client.post('/api/v1/clip-studio/projects', headers=api.api_headers).status_code == 401
    headers = sign_in(api)
    assert api.client.post('/api/v1/clip-studio/projects', data={'payload': '{}'}).status_code == 403
    body = specification(); body['rights_confirmed'] = False
    assert api.client.post('/api/v1/clip-studio/projects', headers=headers, data={'payload': json.dumps(body)}).status_code == 422
    monkeypatch.setenv('CLIP_STUDIO_MOCK', 'false')
    response = api.client.post('/api/v1/clip-studio/projects', headers=headers, data={'payload': json.dumps(specification())})
    assert response.status_code == 422 and 'planning' in response.text
    assert ClipStudioService().list_projects('generation-admin') == []
    api.publisher.assert_not_called()


@pytest.mark.parametrize('kind', ['youtube', 'images', 'video'])
def test_mock_api_workflow_persists_reopens_and_downloads_in_existing_db(studio, kind):
    api = studio; payload = specification(kind, 'openai'); files = None
    if kind == 'images': files = [('files', ('../../escape.png', png(), 'image/png')), ('files', ('second.png', png(), 'image/png'))]
    if kind == 'video':
        path = api.tmp_path/'source.mp4'; fixture_video(path, 4)
        files = [('files', ('source.mp4', path.read_bytes(), 'video/mp4'))]
    project, headers = create(api, payload, files)
    service = ClipStudioService()
    assert service.storage.db_path == str(api.db_path)
    assert project['jobs'][0]['status'] == 'queued' and project['clips'] == []
    assert service.run_next() is True
    reopened = api.client.get('/api/v1/clip-studio/projects/'+project['project_id']).json()
    assert reopened['jobs'][0]['status'] == 'completed' and len(reopened['clips']) == 2
    assert reopened['request']['settings']['planner_model'] == 'gpt-6-sol'
    for item in reopened['clips']:
        attempt = item['attempts'][0]
        assert attempt['quality']['generator'] == 'mock' and attempt['quality']['technical_passed']
        assert (attempt['quality']['width'], attempt['quality']['height']) == (288, 512)
        assert api.client.get(attempt['video_url']+'?download=true').status_code == 200
        assert api.client.get(attempt['thumbnail_url']).headers['content-type'] == 'image/jpeg'
    assert str(api.tmp_path) not in json.dumps(reopened)
    assert api.client.get('/api/v1/clip-studio/jobs/'+project['jobs'][0]['job_id']).json()['status'] == 'completed'
    duplicate = api.client.post('/api/v1/clip-studio/projects', headers=headers, data={'payload': json.dumps(payload)}, files=files)
    assert duplicate.status_code == 202 and duplicate.json()['project_id'] == project['project_id']
    assert len(duplicate.json()['jobs']) == 1
    assert len(api.client.get('/api/v1/clip-studio/projects').json()['projects']) == 1
    assert JobService(api.db_path).list_jobs() == []  # No existing job/review/publish behavior changed.
    assert not (api.tmp_path/'escape.png').exists()
    api.publisher.assert_not_called()


def test_partial_failure_retries_only_failed_clip_and_keeps_success(studio):
    api = studio; project, headers = create(api)
    service = ClipStudioService(); real = MockGenerator()
    class FailSecond:
        def generate(self, clip, *args, **kwargs):
            if clip.title == 'Clip 2': raise RuntimeError('private-provider-token-must-not-appear')
            return real.generate(clip, *args, **kwargs)
    service.run_next(generator=FailSecond())
    value = service.get(project['project_id'], 'generation-admin')
    assert value['jobs'][0]['status'] == 'partial'
    assert 'private-provider-token' not in json.dumps(value)
    first = value['clips'][0]['selected_attempt_id']
    clip = value['clips'][1]; previous = clip['attempts'][0]['attempt_id']
    key = str(uuid4()); endpoint = f"/api/v1/clip-studio/projects/{project['project_id']}/jobs/{value['jobs'][0]['job_id']}/retry"
    body = {'request_id': key, 'confirmed': True}
    assert api.client.post(endpoint, headers=headers, json=body).status_code == 202
    calls = []
    class Observe:
        def generate(self, clip, *args, **kwargs):
            calls.append(clip.title); return real.generate(clip, *args, **kwargs)
    service.run_next(generator=Observe())
    value = service.get(project['project_id'], 'generation-admin')
    assert calls == ['Clip 2'] and value['jobs'][0]['status'] == 'completed'
    assert value['clips'][0]['selected_attempt_id'] == first
    assert value['clips'][1]['attempts'][0]['attempt_id'] == previous
    api.client.post(endpoint, headers=headers, json=body)
    assert len(service.get(project['project_id'], 'generation-admin')['clips'][1]['attempts']) == 2


def test_regenerate_is_idempotent_and_replacement_needs_manual_selection(studio):
    api = studio; project, headers = create(api)
    service = ClipStudioService(); service.run_next()
    before = service.get(project['project_id'], 'generation-admin'); clip = before['clips'][0]
    original = clip['selected_attempt_id']; digest = clip['attempts'][0]['quality']['sha256']
    endpoint = f"/api/v1/clip-studio/projects/{project['project_id']}/clips/{clip['clip_id']}"
    body = {'request_id': str(uuid4()), 'confirmed': True}
    assert api.client.post(endpoint+'/regenerate', headers=headers, json=body).status_code == 202
    assert api.client.post(endpoint+'/regenerate', headers=headers, json=body).status_code == 202
    service.run_next()
    value = service.get(project['project_id'], 'generation-admin'); updated = value['clips'][0]
    assert len(updated['attempts']) == 2 and updated['selected_attempt_id'] == original
    assert media_sha256(service.asset(original, 'generation-admin', 'video')) == digest
    new = updated['attempts'][-1]['attempt_id']
    assert api.client.post(endpoint+'/select', headers=headers, json={'attempt_id': new, 'confirmed': False}).status_code == 422
    selected = api.client.post(endpoint+'/select', headers=headers, json={'attempt_id': new, 'confirmed': True}).json()
    assert selected['clips'][0]['selected_attempt_id'] == new
    assert selected['clips'][1]['selected_attempt_id'] == before['clips'][1]['selected_attempt_id']
    with pytest.raises(ValueError): service.get(project['project_id'], 'another-user')
    with pytest.raises(ValueError): service.asset(new, 'another-user', 'video')
    api.publisher.assert_not_called()


def test_interrupted_mock_job_requires_explicit_retry(studio):
    api = studio; project, headers = create(api)
    service = ClipStudioService()
    with service.storage.connect() as conn:
        conn.execute("UPDATE clip_studio_jobs SET status='running',heartbeat=1 WHERE job_id=?", (project['jobs'][0]['job_id'],))
    value = service.get(project['project_id'], 'generation-admin')
    assert value['jobs'][0]['status'] == 'interrupted' and value['clips'] == []
    endpoint = f"/api/v1/clip-studio/projects/{project['project_id']}/jobs/{value['jobs'][0]['job_id']}/retry"
    assert api.client.post(endpoint, headers=headers, json={'request_id': str(uuid4()), 'confirmed': False}).status_code == 422
    assert api.client.post(endpoint, headers=headers, json={'request_id': str(uuid4()), 'confirmed': True}).status_code == 202
    service.run_next()
    assert service.get(project['project_id'], 'generation-admin')['jobs'][0]['status'] == 'completed'


def test_source_limits_and_output_integrity(studio, monkeypatch):
    api = studio; headers = sign_in(api)
    payload = specification('images')
    response = api.client.post('/api/v1/clip-studio/projects', headers=headers, data={'payload': json.dumps(payload)}, files={'files': ('bad.png', b'not an image', 'image/png')})
    assert response.status_code == 422
    assert list((api.tmp_path/'clip-media').iterdir()) == []
    monkeypatch.setenv('CLIP_STUDIO_UPLOAD_MB', '1')
    assert api.client.post('/api/v1/clip-studio/projects', headers={**headers, 'Content-Length': str(3*1024**2)}).status_code == 413


def test_worker_startup_failure_is_visible_and_does_not_inherit_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('CLIP_STUDIO_MOCK', 'true')
    monkeypatch.setenv('CLIP_STUDIO_DATA_DIR', str(tmp_path/'media'))
    monkeypatch.setenv('OPENAI_API_KEY', 'private-fixture-key')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'another-private-fixture')
    child = Mock(); child.wait.return_value = 1
    launch = Mock(return_value=child)
    monkeypatch.setattr('app.services.clip_studio.service.subprocess.Popen', launch)
    service = ClipStudioService(tmp_path/'existing.db')
    project = service.create(ProjectRequest.model_validate(specification()), [], 'admin')
    deadline = time.monotonic()+3
    while time.monotonic()<deadline:
        value = service.get(project['project_id'], 'admin')
        if value['jobs'][0]['status']=='failed': break
        time.sleep(.01)
    assert value['jobs'][0]['status']=='failed' and 'worker exited' in value['jobs'][0]['error']
    assert 'OPENAI_API_KEY' not in launch.call_args.kwargs['env']
    assert 'ANTHROPIC_API_KEY' not in launch.call_args.kwargs['env']
    assert str(tmp_path/'existing.db') in launch.call_args.args[0]


def test_waking_queued_worker_never_restarts_running_or_uncertain_work(studio):
    project,headers=create(studio)
    service=ClipStudioService();endpoint=f"/api/v1/clip-studio/projects/{project['project_id']}/start-queued"
    body={'request_id':str(uuid4()),'confirmed':True}
    assert studio.client.post(endpoint,headers=headers,json=body).status_code==202
    assert studio.client.post(endpoint,headers=headers,json=body).status_code==202
    with service.storage.connect() as conn:
        conn.execute("UPDATE clip_studio_jobs SET status='uncertain' WHERE project_id=?",(project['project_id'],))
    assert studio.client.post(endpoint,headers=headers,json=body).status_code==422


def test_saved_failure_is_reported_in_backend_log(studio,caplog):
    project,_=create(studio)
    feature=ClipStudioService()
    with feature.storage.connect() as conn:
        conn.execute("UPDATE clip_studio_jobs SET status='failed',stage='analyze',error='YouTube source download failed' WHERE project_id=?",(project['project_id'],))
    response=studio.client.get('/api/v1/clip-studio/projects/'+project['project_id'])
    assert response.status_code==200
    assert project['jobs'][0]['job_id'] in caplog.text
    assert 'YouTube source download failed' in caplog.text
