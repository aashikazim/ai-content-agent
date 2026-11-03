"""Runway SDK edit/continuation contracts and immutable candidate lifecycle."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from app.services import runway_media
from app.services.gemini_video_service import VideoProviderError
from app.services.runway_models import EDIT_MODELS, operation_estimate
from app.services.runway_video_service import RunwayVideoService
from tests.test_gemini_video import video_service
from tests.test_runway_video import TASK, adapter, config, runway_service


@pytest.mark.parametrize('model', EDIT_MODELS)
def test_official_sdk_video_edit_payload(model,tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'id':TASK})
    base=adapter(config(),handler)
    provider=RunwayVideoService(config(),client=base.client,model=model)
    source=tmp_path/'source.mp4';source.write_bytes(b'video fixture')
    result=provider.submit('Change the jacket to red.',aspect_ratio='9:16',operation='edit',
        duration=8,source_video=source,references=[b'image fixture'])
    assert result['interaction_id']==TASK and len(calls)==1
    assert calls[0].url.path=='/v1/video_to_video'
    body=json.loads(calls[0].content)
    assert body['model']==model and 'duration' not in body and 'previous_id' not in body
    field='promptVideo' if model.startswith('klingO3') else 'videoUri'
    assert body[field].startswith('data:video/mp4;base64,')
    if model.startswith('klingO3'):
        assert body['providerSettings']=={'keepOriginalSound':True}
    if model=='kling_4': assert body['ratio']=='auto_1080p'
    else: assert 'ratio' not in body
    assert 'private-fixture' not in json.dumps(body)


def test_continuation_uses_image_endpoint_and_added_duration_not_parent_length():
    calls=[]
    base=adapter(config(),lambda request:(calls.append(request),httpx.Response(200,json={'id':TASK}))[1])
    base.submit('Continue the camera motion.',aspect_ratio='9:16',operation='extend',duration=3,start_image=b'final-frame')
    assert len(calls)==1 and calls[0].url.path=='/v1/image_to_video'
    body=json.loads(calls[0].content)
    assert body['duration']==3 and body['promptImage'].startswith('data:image/png;')
    assert body['model']=='gen4.5'


def test_large_source_uses_signed_upload_without_bearer_or_redirects(tmp_path,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path=='/v1/uploads':
            return httpx.Response(200,json={'uploadUrl':'https://example.s3.amazonaws.com/source',
                'runwayUri':'runway://temporary-source-token','fields':{'key':'temporary-object'}})
        return httpx.Response(200,json={'id':TASK})
    base=adapter(config(),handler)
    session=Mock();session.post.return_value.status_code=204
    provider=RunwayVideoService(config(),client=base.client,session=session,model='aleph2')
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**kw:[(2,1,6,'',('18.1.2.3',443))])
    source=tmp_path/'source.mp4';source.write_bytes(b'v'*(3*1024*1024+1))
    provider.submit('Change the lighting.',aspect_ratio='9:16',operation='edit',duration=8,source_video=source)
    assert [call.url.path for call in calls]==['/v1/uploads','/v1/video_to_video']
    assert json.loads(calls[1].content)['videoUri']=='runway://temporary-source-token'
    assert session.post.call_args.kwargs['headers']=={}
    assert session.post.call_args.kwargs['allow_redirects'] is False
    assert session.post.call_count==1


@pytest.mark.parametrize('status',[408,500,429])
def test_edit_request_failures_are_not_retried(tmp_path,status):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,json={'error':'private-fixture-secret'})
    provider=adapter(config(),handler);provider.model='aleph2'
    source=tmp_path/'source.mp4';source.write_bytes(b'fixture')
    with pytest.raises(VideoProviderError) as error:
        provider.submit('Change the jacket.',aspect_ratio='9:16',operation='edit',duration=8,source_video=source)
    assert len(calls)==1 and error.value.uncertain==(status in {408,500})
    assert 'private-fixture' not in str(error.value)


@pytest.fixture
def completed_runway(runway_service,monkeypatch):
    instance,fake=runway_service
    value=instance.create(instance.quote('script-fixture','admin')['quote_id'],'admin',True)
    instance.process(value['production_id'],submit_queued=True)
    value=instance.process(value['production_id'])
    with instance.storage.connect() as conn:
        for shot in value['shots']:
            row=instance.storage.attempt(shot['attempt_id'])
            digest=hashlib.sha256(Path(row['clip_path']).read_bytes()).hexdigest()
            conn.execute('UPDATE video_shots SET media_quality_json=? WHERE attempt_id=?',
                (json.dumps({'sha256':digest}),shot['attempt_id']))
    def normalize(source,output,*args,**kwargs):
        output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(Path(source).read_bytes());return output
    monkeypatch.setattr(runway_media,'normalize',normalize)
    monkeypatch.setattr(runway_media,'last_frame',lambda *args:b'last frame')
    monkeypatch.setattr(runway_media,'finish_candidate',lambda downloaded,output,*args:normalize(downloaded,output))
    return instance,fake,instance.storage.get(value['production_id'])


@pytest.mark.parametrize('operation',['edit','extend'])
def test_paid_candidate_keeps_parent_and_requires_new_quote_and_selection(completed_runway,operation):
    instance,fake,value=completed_runway
    parent=value['shots'][0]
    original=instance.storage.attempt(parent['attempt_id'])
    original_bytes=Path(original['clip_path']).read_bytes()
    quote=instance.quote_change(value['production_id'],'admin',1,operation=operation,
        instructions='Change the lighting.' if operation=='edit' else 'Continue walking.',edit_model='aleph2' if operation=='edit' else None)
    assert quote['model']==('aleph2' if operation=='edit' else 'gen4.5')
    assert quote['estimate_usd']==('2.24' if operation=='edit' else '0.36')
    assert quote['request_summary']['source']['sha256']
    posts=fake.posts
    with pytest.raises(ValueError,match='confirmation'):instance.change(quote['quote_id'],'admin',False)
    created=instance.change(quote['quote_id'],'admin',True)
    assert fake.posts==posts
    instance.process(value['production_id'],submit_queued=True,attempt_ids=[created['attempt_id']])
    candidate=instance.storage.attempt(created['attempt_id'])
    assert candidate['model']==quote['model'] and candidate['estimate_usd']==quote['estimate_usd']
    assert json.loads(candidate['request_json'])['generation_duration']==(8 if operation=='edit' else 3)
    with pytest.raises(ValueError):instance.change(quote['quote_id'],'admin',True)
    refreshed=instance.process(value['production_id'])
    assert fake.posts==posts+1
    assert next(shot for shot in refreshed['shots'] if shot['attempt_id']==parent['attempt_id'])['selected']
    assert not instance.storage.attempt(created['attempt_id'])['selected']
    assert Path(original['clip_path']).read_bytes()==original_bytes


def test_source_tampering_or_invalid_model_blocks_before_billing(completed_runway):
    instance,fake,value=completed_runway
    posts=fake.posts
    with pytest.raises(ValueError,match='supported edit model'):
        instance.quote_change(value['production_id'],'admin',1,operation='edit',instructions='Change lighting',edit_model='unverified')
    quote=instance.quote_change(value['production_id'],'admin',1,operation='edit',instructions='Change lighting',edit_model='aleph2')
    source=instance.storage.attempt(value['shots'][0]['attempt_id'])
    Path(source['clip_path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed after quotation'):instance.change(quote['quote_id'],'admin',True)
    assert fake.posts==posts
    with instance.storage.connect() as conn:
        assert conn.execute('SELECT consumed_at FROM video_generation_quotes WHERE quote_id=?',(quote['quote_id'],)).fetchone()[0] is None


def test_edit_and_extension_pricing_use_source_or_added_seconds():
    assert str(operation_estimate(config(),'edit',8,'aleph2'))=='2.24'
    assert str(operation_estimate(config(),'edit',8,'klingO3_standard'))=='1.04'
    assert str(operation_estimate(config(),'edit',8,'kling_4'))=='2.26'
    assert str(operation_estimate(config(),'extend',3))=='0.36'
    with pytest.raises(ValueError):operation_estimate(config(),'edit',15,'klingO3_standard')


def test_quality_handoff_hashes_media_without_loading_whole_videos(completed_runway,monkeypatch):
    instance,_,value=completed_runway
    path=Path(instance.jobs.get_job(value['job_id'])['video_path'])
    with path.open('rb') as handle:
        digest=hashlib.file_digest(handle,'sha256').hexdigest()
    with instance.storage.connect() as conn:
        conn.execute('UPDATE video_productions SET quality_json=? WHERE production_id=?',
            (json.dumps({'technical_passed':True,'sha256':digest}),value['production_id']))
    def reject_whole_file_read(*args):raise AssertionError('Review must stream video hashes')
    monkeypatch.setattr(Path,'read_bytes',reject_whole_file_read)
    reviewed=instance.send_to_review(value['production_id'],continuity_confirmed=True,captions_confirmed=True,rights_confirmed=True)
    assert reviewed['status']=='pending_review'
    assert instance.jobs.get_job(value['job_id'])['status']=='pending_review'
