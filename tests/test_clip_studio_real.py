"""Real orchestration with hosted/GPU APIs mocked; media checks use CPU FFmpeg."""
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests
from fastapi import UploadFile
import io

from app.services.clip_studio.comfy import inject_workflow, LtxAdapter
from app.services.clip_studio.media import StudioError
from app.services.clip_studio.native import NativeGenerator, chunk_durations
from app.services.clip_studio.planner import HostedPlanner, MockPlanner, strict_schema
from app.services.clip_studio.review import send_to_review
from app.services.clip_studio.schemas import ProjectRequest
from app.services.clip_studio.service import ClipStudioService
from app.services.clip_studio.storage import encoded
from app.services.content_storage import ContentStorage
from app.services.job_service import JobService
from app.services.publishing_service import PublishingService
from app.services.review_dashboard_service import ReviewDashboardService
from app.services.web_dashboard_service import WebDashboardService
from tests.test_clip_studio import specification, png
from tests.test_cloud_media import fixture_video


@pytest.fixture
def real(tmp_path,monkeypatch):
    monkeypatch.setenv('CLIP_STUDIO_MOCK','false')
    monkeypatch.setenv('CLIP_STUDIO_DATA_DIR',str(tmp_path/'media'))
    monkeypatch.setenv('CHARACTER_REFERENCES_DIR',str(tmp_path/'refs'))
    monkeypatch.setenv('OPENAI_API_KEY','fixture-only')
    monkeypatch.setattr(ClipStudioService,'launch',Mock())
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.check_model',lambda self:{'status':'configured'})
    return ClipStudioService(tmp_path/'existing.db')


def real_request(**settings):
    body=specification('images','openai')
    body['settings'].update(video_model='ltx',duration_seconds=3,paid_planning_confirmed=True)
    body['settings'].update(settings)
    return ProjectRequest.model_validate(body)


def upload():return UploadFile(filename='reference.png',file=io.BytesIO(png()))


def fake_plan(request,sources,analysis,cast=None):
    return MockPlanner().plan(request,sources),[{'id':'response-fixture','usage':{}}]


def install_native(monkeypatch, *, fail_second=False):
    calls=[]
    def submit(self,prompt,**kwargs):
        calls.append(kwargs['local_inputs']);return {'interaction_id':str(uuid4()),'status':'in_progress'}
    def retrieve(self,identifier):return {'interaction_id':identifier,'status':'completed','usage':{'elapsed_seconds':1}}
    def download(self,snapshot,destination):
        fixture_video(destination,calls[-1]['duration'])
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.submit',submit)
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.retrieve',retrieve)
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.download',download)
    monkeypatch.setattr(HostedPlanner,'plan',lambda self,*args,**kwargs:fake_plan(*args,**kwargs))
    return calls


def test_real_native_flow_imports_review_approval_never_uploads(real,monkeypatch):
    calls=install_native(monkeypatch)
    project=real.create(real_request(),[upload()],'admin')
    assert real.run_next()
    saved=real.get(project['project_id'],'admin')
    assert saved['jobs'][0]['status']=='completed',saved['jobs']
    assert len(calls)==2
    assert str(real.root) not in json.dumps(saved)
    jobs=JobService(real.storage.db_path);content=ContentStorage(real.storage.db_path,real.root/'plans')
    review=ReviewDashboardService(jobs,content)
    assert len(review.list_pending())==2
    first=saved['clips'][0]['attempts'][0]
    linked=send_to_review(real,first['attempt_id'],'admin')
    assert linked['status']=='already_sent' and len(jobs.list_jobs())==2
    card=WebDashboardService(jobs,content).details(linked['job_id'])
    assert card['job']['title']=='Clip 1' and card['job']['duration_seconds']==3
    publisher=Mock();publishing=PublishingService(jobs,content,youtube=publisher,facebook=publisher)
    with pytest.raises(ValueError,match='approved'):publishing.publish(linked['job_id'],'youtube',dry_run=False)
    review.review(linked['job_id'],'approved');publisher.upload.assert_not_called()
    assert publishing.publish(linked['job_id'],'youtube',dry_run=True)['status']=='dry_run'
    publisher.upload.assert_not_called()


def test_paid_confirmation_and_wan_preflight_fail_before_any_work(real):
    with pytest.raises(StudioError,match='Confirm'):real.create(real_request(paid_planning_confirmed=False),[upload()],'admin')
    with pytest.raises(StudioError,match='cast'):real.create(real_request(video_model='wan'),[upload()],'admin')
    assert real.list_projects('admin')==[]


def test_chunks_are_balanced_and_never_exceed_model_limits():
    for duration in range(3,301):
        values=chunk_durations(duration)
        assert sum(values)==duration and all(3<=v<=10 for v in values)
    assert chunk_durations(2)==[3]  # minimum neural shot is trimmed to requested duration.


def test_lost_native_acknowledgment_is_durable_and_never_retried(real,monkeypatch):
    calls=install_native(monkeypatch)
    def lost(*args,**kwargs):raise requests.Timeout('private-fixture-secret')
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.submit',lost)
    project=real.create(real_request(),[upload()],'admin');real.run_next()
    saved=real.get(project['project_id'],'admin')
    assert saved['jobs'][0]['status']=='uncertain'
    assert all(a['status']=='uncertain' for c in saved['clips'] for a in c['attempts'])
    assert 'private-fixture-secret' not in json.dumps(saved)
    with pytest.raises(ValueError,match='uncertain'):real.enqueue(project['project_id'],'admin',uuid4(),clip_id=saved['clips'][0]['clip_id'])
    with pytest.raises(StudioError,match='missing'):real.reconcile(project['project_id'],'admin',uuid4(),resume=True)
    real.reconcile(project['project_id'],'admin',uuid4(),resume=False)
    assert calls==[]
    assert real.get(project['project_id'],'admin')['jobs'][0]['status']=='failed'


def workflow():
    names=['prompt','negative_prompt','reference','duration','width','height','seed','character_reference','continuation_reference','source_video']
    return {'configured':True,'output_node_id':'output','bindings':{n:[str(i),n] for i,n in enumerate(names)},
            'workflow':{**{str(i):{'class_type':'Fixture','inputs':{n:''}} for i,n in enumerate(names)},'output':{'class_type':'SaveVideo','inputs':{}}}}


def test_comfy_injection_maps_each_input_without_mutating_export():
    template=workflow();before=json.dumps(template)
    values={'prompt':'Maya walks','negative_prompt':'text','reference':'frame.png','duration':5,'width':512,'height':896,'seed':12,'character_reference':'actor.png','continuation_reference':'tail.png','source_video':'motion.mp4'}
    result=inject_workflow(template,values)
    for key,value in values.items():node,field=template['bindings'][key];assert result[node]['inputs'][field]==value
    assert json.dumps(template)==before
    with pytest.raises(StudioError,match='placeholder'):inject_workflow({'configured':False},values)
    template['bindings']['prompt']=['missing','text']
    with pytest.raises(StudioError,match='binding'):inject_workflow(template,values)


def planner_session(request,sources,*,bad=False):
    valid=MockPlanner().plan(request,sources).model_dump(mode='json')
    for c in valid['clips']:c['reference_mode']='image-to-video'
    def response(text):
        value=Mock(status_code=200);value.json.return_value={'id':'resp-fixture','status':'completed','usage':{'input_tokens':100,'output_tokens':50},'output':[{'type':'message','content':[{'type':'output_text','text':text}]}]};return value
    session=Mock()
    session.post.side_effect=[response('invalid' if bad else json.dumps(valid)),response(json.dumps(valid))]
    return session


def test_planner_repairs_only_known_invalid_output_and_records_usage(real):
    request=real_request();sources=[{'metadata_json':'{}'}]
    session=planner_session(request,sources,bad=True)
    plan,usage=HostedPlanner(real.storage,'project-fixture',session=session).plan(request,sources,{'scenes':[],'transcript':[]})
    assert len(plan.clips)==2 and len(usage)==2 and session.post.call_count==2
    schema=session.post.call_args.kwargs['json']['text']['format']['schema']
    assert schema['additionalProperties'] is False and set(schema['required'])==set(schema['properties'])
    with real.storage.connect() as conn:assert conn.execute("SELECT COUNT(*) FROM clip_studio_calls WHERE status='received'").fetchone()[0]==2


def test_planner_timeout_never_retries_or_exposes_provider_error(real):
    session=Mock();session.post.side_effect=requests.Timeout('private-key')
    with pytest.raises(StudioError,match='uncertain') as error:
        HostedPlanner(real.storage,'project-fixture',session=session).plan(real_request(),[{'metadata_json':'{}'}],{})
    assert error.value.uncertain and 'private-key' not in str(error.value)
    assert session.post.call_count==1
    with real.storage.connect() as conn:assert conn.execute('SELECT status FROM clip_studio_calls').fetchone()[0]=='submitting'


def test_mock_outputs_cannot_be_imported_for_publishing(real,monkeypatch):
    monkeypatch.setenv('CLIP_STUDIO_MOCK','true')
    request=real_request();project=real.create(request,[upload()],'admin');real.run_next()
    attempt=real.get(project['project_id'],'admin')['clips'][0]['attempts'][0]
    with pytest.raises(StudioError,match='mock'):send_to_review(real,attempt['attempt_id'],'admin')


def test_connected_native_hybrid_uses_wan_on_cuts_then_ltx_carry(real,monkeypatch,tmp_path):
    from types import SimpleNamespace
    from app.services.cloud_video_renderer import media_sha256
    reference=tmp_path/'actor.png';reference.write_bytes(png())
    clip=MockPlanner().plan(real_request(clip_count=1,duration_seconds=13),[{'metadata_json':'{}'}]).clips[0]
    clip.character_ids=['maya']
    settings=real_request(video_model='hybrid',clip_count=1,duration_seconds=13).settings
    cast={'sha256':'cast-fixture','characters':[{'character_id':'maya','name':'Maya','description':'Dark hair','wardrobe':'Blue jacket','voice_notes':'Calm voice','primary_reference_id':'actor-ref'}]}
    calls=install_native(monkeypatch)
    directory=tmp_path/'render';generator=NativeGenerator(real.storage,'attempt-fixture')
    output,thumbnail,quality=generator.generate(clip,settings,42,directory,references={
        'cast':cast,'identity_refs':[{'reference_id':'actor-ref','image_path':str(reference),'sha256':media_sha256(reference)}],
        'source':{'kind':'images','path':str(reference)},'position':0})
    assert [v['route'] for v in calls]==['hybrid','ltx']
    assert calls[0]['start_image']==str(reference)
    assert len(calls[1]['continuation_frames'])==17 and calls[1]['character_image']==str(reference)
    assert quality['technical_passed'] and quality['cast_sha256']=='cast-fixture'
    assert output.is_file() and thumbnail.is_file()
    # A second invocation reuses completed chunks; no new neural submission.
    generator.generate(clip,settings,42,directory,references={'cast':cast,'identity_refs':[{'reference_id':'actor-ref','image_path':str(reference),'sha256':media_sha256(reference)}],'source':{'kind':'images','path':str(reference)},'position':0})
    assert len(calls)==2


def test_claude_planner_uses_selected_model_and_structured_schema(real,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','fixture-key')
    request=real_request(planner_provider='anthropic',planner_model='claude-sonnet-5-5')
    valid=MockPlanner().plan(request,[{'metadata_json':'{}'}]).model_dump(mode='json')
    session=Mock();response=Mock(status_code=200)
    response.json.return_value={'id':'claude-fixture','stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(valid)}],'usage':{}}
    session.post.return_value=response
    plan,usage=HostedPlanner(real.storage,'claude-project',session=session).plan(request,[{'metadata_json':'{}'}],{})
    assert len(plan.clips)==2 and len(usage)==1
    args=session.post.call_args
    assert args.args[0]=='https://api.anthropic.com/v1/messages'
    assert args.kwargs['json']['model']=='claude-sonnet-5-5'
    assert args.kwargs['json']['output_config']['format']['type']=='json_schema'


def test_comfy_known_id_poll_and_download_without_resubmission(real,monkeypatch,tmp_path):
    request=real_request(clip_count=1)
    clip=MockPlanner().plan(request,[{'metadata_json':'{}'}]).clips[0]
    directory=tmp_path/'comfy-render';source=tmp_path/'source.png';source.write_bytes(png())
    video=tmp_path/'fixture.mp4';fixture_video(video,3)
    identifier=str(uuid4());session=Mock()
    submit=Mock(status_code=200);submit.json.return_value={'prompt_id':identifier}
    history=Mock(status_code=200);history.json.return_value={identifier:{'status':{'completed':True},'outputs':{'output':{'videos':[{'filename':'clip.mp4','subfolder':'','type':'output'}]}}}}
    download=Mock(status_code=200);download.__enter__=Mock(return_value=download);download.__exit__=Mock(return_value=False);download.iter_content.return_value=[video.read_bytes()]
    session.post.return_value=submit;session.get.side_effect=[history,download]
    adapter=LtxAdapter(real.storage,'comfy-attempt',session=session)
    monkeypatch.setattr(adapter,'template',lambda model:workflow());monkeypatch.setattr(adapter,'upload',lambda path:'uploaded.png')
    monkeypatch.setattr('app.services.clip_studio.comfy.models.runtime_root',lambda:tmp_path/'runtime')
    output,thumbnail,quality=adapter.generate(clip,request.settings,42,directory,references={'source':{'kind':'images','path':str(source)},'position':0})
    assert quality['technical_passed'] and output.is_file() and thumbnail.is_file()
    submits=[c for c in session.post.call_args_list if c.args[0].endswith('/prompt')]
    assert len(submits)==1
    with real.storage.connect() as conn:
        row=conn.execute('SELECT * FROM clip_studio_chunks').fetchone()
    assert row['external_id']==identifier and row['status']=='completed'
    adapter.generate(clip,request.settings,42,directory,references={'source':{'kind':'images','path':str(source)},'position':0})
    assert len([c for c in session.post.call_args_list if c.args[0].endswith('/prompt')])==1


def test_retrying_failed_planning_requires_fresh_cost_consent(real,monkeypatch):
    def invalid(*args,**kwargs):raise StudioError('Received an invalid plan')
    monkeypatch.setattr(HostedPlanner,'plan',invalid)
    project=real.create(real_request(),[upload()],'admin');real.run_next()
    saved=real.get(project['project_id'],'admin');job=saved['jobs'][0]
    assert job['status']=='failed' and saved['clips']==[]
    with pytest.raises(ValueError,match='two new paid'):
        real.enqueue(project['project_id'],'admin',uuid4(),retry_job_id=job['job_id'])
    accepted=real.enqueue(project['project_id'],'admin',uuid4(),retry_job_id=job['job_id'],paid_planning_confirmed=True)
    assert accepted['status']=='queued'


def test_resume_polls_known_native_ids_without_repeating_submissions(real,monkeypatch):
    from app.services.gemini_video_service import VideoProviderError
    calls=install_native(monkeypatch)
    def unavailable(*args,**kwargs):raise VideoProviderError('Fixture: temporarily unavailable',uncertain=True)
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.retrieve',unavailable)
    project=real.create(real_request(),[upload()],'admin');real.run_next()
    saved=real.get(project['project_id'],'admin')
    assert saved['jobs'][0]['status']=='uncertain' and len(calls)==2
    ids=[c['attempts'][0]['chunks'][0]['external_id'] for c in saved['clips']]
    assert all(ids)
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.retrieve',
        lambda self,identifier:{'interaction_id':identifier,'status':'completed','usage':{}})
    resume_id=uuid4()
    resumed=real.reconcile(project['project_id'],'admin',resume_id,resume=True)
    assert real.reconcile(project['project_id'],'admin',resume_id,resume=True)==resumed
    assert resumed['status']=='queued';real.run_next()
    saved=real.get(project['project_id'],'admin')
    assert saved['jobs'][0]['status']=='completed' and len(calls)==2
    assert [c['attempts'][0]['chunks'][0]['external_id'] for c in saved['clips']]==ids

    # A completed resume clears the old uncertainty gate without discarding history.
    accepted=real.enqueue(project['project_id'],'admin',uuid4(),clip_id=saved['clips'][0]['clip_id'])
    assert accepted['status']=='queued'


def test_five_fifteen_second_source_audio_clips_with_real_planner_contract_and_mocked_models(real,monkeypatch,tmp_path):
    """Same UI configuration as the reported failure, through Review import."""
    from app.services.clip_studio import pipeline
    from tests.test_clip_studio_planner_contract import request_for,candidate,response
    import sys
    request=request_for()
    monkeypatch.setenv('ANTHROPIC_API_KEY','fixture-only')
    monkeypatch.setattr('app.services.clip_studio.media.analysis_python',lambda:Path(sys.executable))
    source=tmp_path/'source.mp4';fixture_video(source,100)
    project=real.create(request,[UploadFile(filename='source.mp4',file=io.BytesIO(source.read_bytes()))],'admin')
    with real.storage.connect() as conn:
        conn.execute('INSERT INTO clip_studio_analyses VALUES (?,?,?)',(project['project_id'],encoded({'duration_seconds':100,'scenes':[{'source_start':0,'source_end':100}],'transcript':[{'start':0,'end':100,'text':'A moving source with original audio.'}]}),1))
    value=candidate(request)
    for clip in value['clips']:
        clip.update(recommended_model='LTX',reference_mode='Image-to-video',duration_seconds=15.0,transition='Cut')
    session=Mock();session.post.return_value=response('anthropic',value)
    original=HostedPlanner
    monkeypatch.setattr(pipeline,'HostedPlanner',lambda storage,identifier:original(storage,identifier,session))
    submissions=[]
    def submit(self,prompt,**kwargs):
        submissions.append(kwargs['local_inputs'])
        return {'interaction_id':str(uuid4()),'status':'in_progress'}
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.submit',submit)
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.retrieve',
        lambda self,identifier:{'interaction_id':identifier,'status':'completed','usage':{}})
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.download',
        lambda self,snapshot,destination:fixture_video(destination,submissions[-1]['duration']))
    assert real.run_next()
    saved=real.get(project['project_id'],'admin')
    assert saved['jobs'][0]['status']=='completed',saved['jobs']
    assert session.post.call_count==1  # No paid repair for equivalent enum/number spellings.
    assert len(saved['clips'])==5 and len(submissions)==10  # Balanced8s+7s per15s clip.
    for clip in saved['clips']:
        attempt=clip['attempts'][0]
        assert attempt['quality']['technical_passed'] and attempt['review_job_id']
        assert clip['plan']['character_ids']==[] and clip['plan']['duration_seconds']==15
    jobs=JobService(real.storage.db_path).list_jobs()
    assert len(jobs)==5 and all(job['status']=='pending_review' for job in jobs)
