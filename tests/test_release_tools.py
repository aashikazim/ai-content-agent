"""Release utilities use local fixtures; no paid requests, uploads or model inference."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from tests.test_generation_api import generation_api

from app.services.work_queue import WorkQueue
from app.services.speech_analysis import word_error_rate,write_word_captions
from app.services.stock_service import StockService,trusted_url
from app.services.research_service import script_quality,research
from app.services.production_tools import ProductionTools


def test_durable_queue_preserves_idempotency_and_never_requeues_lost_work(tmp_path):
    queue=WorkQueue(tmp_path/'existing.db')
    first=queue.enqueue('script','draft-1');assert queue.enqueue('script','draft-1')['work_id']==first['work_id']
    handler=Mock();assert queue.run_next({'script':handler});handler.assert_called_once_with('draft-1')
    assert queue.run_next({'script':handler}) is False
    second=queue.enqueue('job','job-1')
    with queue.connect() as conn:conn.execute("UPDATE app_work_queue SET status='running',heartbeat=1 WHERE work_id=?",(second['work_id'],))
    queue.recover()
    with queue.connect() as conn:assert conn.execute('SELECT status FROM app_work_queue WHERE work_id=?',(second['work_id'],)).fetchone()[0]=='interrupted'
    assert queue.run_next({'job':handler}) is False
    with pytest.raises(ValueError):queue.enqueue('publish','never')


def test_word_error_and_safe_aligned_subtitles(tmp_path):
    assert word_error_rate('Maya walks home','Maya walks home')==0
    assert word_error_rate('Maya walks home','Maya walks slowly home')==pytest.approx(1/3)
    assert word_error_rate('','anything') is None
    output=tmp_path/'captions.ass'
    write_word_captions(output,{'duration_seconds':2,'words':[{'word':'hello{override}\\N','start':.1,'end':1.5}]},512,896)
    text=output.read_text();assert '0:00:00.10' in text and '{override}' not in text and '\\N' not in text


def test_stock_candidates_validate_cdn_license_and_attribution(monkeypatch):
    monkeypatch.setenv('PEXELS_API_KEY','fixture-secret')
    session=Mock();session.get.return_value=Mock(status_code=200)
    session.get.return_value.json.return_value={'videos':[{'id':12,'url':'https://www.pexels.com/video/12','width':720,'height':1280,'duration':8,'user':{'name':'A Creator'},'video_files':[{'width':720,'height':1280,'file_type':'video/mp4','link':'https://videos.pexels.com/video-files/12/clip.mp4'}]}]}
    values=StockService(session).search('cafe')
    assert len(values)==1 and 'A Creator' in values[0]['attribution'] and 'license' in values[0]['license_url']
    assert session.get.call_args.args[0]=='https://api.pexels.com/v1/videos/search'
    assert trusted_url('https://videos.pexels.com.evil.test/x.mp4') is None
    assert trusted_url('http://videos.pexels.com/x.mp4') is None
    assert trusted_url('https://user:secret@videos.pexels.com/x.mp4') is None
    with pytest.raises(ValueError,match='Confirm'):StockService(session).download(values[0],Path('/tmp'),confirmed=False)


def test_research_sources_never_become_verified_and_quality_flags_claims():
    session=Mock();session.get.return_value=Mock(status_code=200)
    session.get.return_value.json.return_value={'articles':[{'url':'https://example.org/report','title':'A source','seendate':'2026-11-05'}]}
    value=research('science',session);assert value['factual_status']=='unverified' and value['sources'][0]['checked'] is False
    report=script_quality('This always cures disease. This always cures disease.',60)
    assert report['warnings'] and report['factual_status']=='unverified' and report['repetition_ratio']>0


def test_tool_speech_outputs_private_versioned_asset_without_approval(tmp_path,monkeypatch):
    monkeypatch.setenv('PRODUCTION_TOOLS_DIR',str(tmp_path/'tools'))
    monkeypatch.setattr(ProductionTools,'launch',Mock())
    def synth(self,text,path):Path(path).write_bytes(b'fixture-wave')
    monkeypatch.setattr('app.services.tts_service.TTSService.synthesize',synth)
    tools=ProductionTools(tmp_path/'existing.db');identifier='request-1'
    job=tools.enqueue('speech',{'text':'Hello','profile_id':None},'admin',identifier)
    assert tools.enqueue('speech',{'text':'Hello','profile_id':None},'admin',identifier)['tool_job_id']==job['tool_job_id']
    tools.run(job['tool_job_id']);value=tools.get(job['tool_job_id'],'admin')
    assert value['status']=='completed' and str(tmp_path) not in json.dumps(value)
    assert tools.asset(job['tool_job_id'],'admin').read_bytes()==b'fixture-wave'
    with pytest.raises(ValueError):tools.asset(job['tool_job_id'],'another-user')
    assert len(tools.list('admin'))==1


def test_research_dossier_is_immutable_and_script_quote_binds_snapshot(tmp_path,monkeypatch):
    from app.services.research_dossiers import create,get
    from app.services.script_generation_service import ScriptGenerationService
    from app.models.generation_config import GenerationConfig
    monkeypatch.setenv('PRODUCTION_TOOLS_DIR',str(tmp_path/'tools'))
    notes=create('Cafe story',[{'claim':'The source reports a new cafe.','sources':['https://example.org/primary']}],True)
    config=GenerationConfig(source_mode='topic',topic='A cafe story',research_dossier_id=notes['dossier_id'])
    request=ScriptGenerationService.request(config)
    assert request['research_dossier']['claims'][0]['sources']==['https://example.org/primary']
    assert 'https://example.org/primary' in ScriptGenerationService.prompt(config,request,[])
    file=tmp_path/'tools/dossiers'/(notes['dossier_id']+'.json')
    file.write_text(file.read_text().replace('new cafe','old cafe'))
    with pytest.raises(ValueError,match='changed'):get(notes['dossier_id'])


def test_structured_logs_redact_keys_bearer_queries_and_tracebacks(monkeypatch):
    import logging
    from app.services.structured_logging import RedactedJSONFormatter
    monkeypatch.setenv('OPENAI_API_KEY','fixture-secret-value')
    record=logging.LogRecord('app.tools',logging.ERROR,'',1,'Failure fixture-secret-value Bearer abcdef1234 https://cdn.test/video?token=secret',(),None)
    value=RedactedJSONFormatter().format(record)
    assert 'fixture-secret-value' not in value and 'abcdef1234' not in value and 'token=secret' not in value
    assert json.loads(value)['level']=='ERROR'


def test_tools_api_requires_session_csrf_and_confirmation(generation_api,monkeypatch):
    from tests.test_generation_api import sign_in
    monkeypatch.setattr(ProductionTools,'launch',Mock())
    api=generation_api
    assert api.client.get('/api/v1/tools/jobs').status_code==401
    assert api.client.get('/api/v1/tools/readiness',headers=api.api_headers).status_code==401
    headers=sign_in(api)
    payload={'request_id':'f40e5d84-f717-4113-b7ba-55a72f4ed667','kind':'speech','text':'Hello','confirmed':True}
    assert api.client.post('/api/v1/tools/jobs',json=payload).status_code==403
    accepted=api.client.post('/api/v1/tools/jobs',headers=headers,json=payload)
    assert accepted.status_code==202,accepted.text
    assert api.client.post('/api/v1/tools/jobs',headers=headers,json=payload).json()['tool_job_id']==accepted.json()['tool_job_id']
    assert api.client.get('/api/v1/tools/jobs').json()['jobs'][0]['status']=='queued'
    api.publisher.assert_not_called()


def test_legacy_narration_regeneration_preserves_original_and_creates_pending_review(tmp_path,monkeypatch):
    import shutil
    from PIL import Image
    from app.models.content_plan import ContentPlan
    from app.services.content_storage import ContentStorage
    from app.services.job_service import JobService
    from app.services.cloud_video_renderer import media_sha256,run
    from tests.test_content_plan import valid_data
    from tests.test_cloud_media import fixture_video
    monkeypatch.setenv('PRODUCTION_TOOLS_DIR',str(tmp_path/'tools'))
    monkeypatch.setattr(ProductionTools,'launch',Mock())
    db=tmp_path/'existing.db';base=tmp_path/'original';base.mkdir()
    data=valid_data();data['scenes']=data['scenes'][:1];data['scenes'][0]['duration_seconds']=30
    plan=ContentPlan.model_validate(data);plan_id,_=ContentStorage(db,tmp_path/'plans').save_plan(plan)
    Image.new('RGB',(160,284),'navy').save(base/'scene_1.png')
    fixture_video(base/'scene_1.mp4',30)
    run(['ffmpeg','-v','error','-y','-i',str(base/'scene_1.mp4'),'-vn',str(base/'scene_1.wav')])
    shutil.copyfile(base/'scene_1.mp4',base/'final_video.mp4')
    jobs=JobService(db);source=jobs.create_job(image_mode='text');jobs.update_job(source,'approved',plan_id=plan_id,video_path=str(base/'final_video.mp4'))
    original={p.name:media_sha256(p) for p in base.iterdir()}
    tools=ProductionTools(db);tool=tools.enqueue('regenerate',{'job_id':source,'scene_number':1,'asset':'narration','text':'A shorter narration.','profile_id':None,'seed':42},'admin','regen-1')
    tools.run(tool['tool_job_id']);value=tools.get(tool['tool_job_id'],'admin')
    assert value['status']=='completed',value
    assert original=={p.name:media_sha256(p) for p in base.iterdir()}
    assert jobs.get_job(source)['status']=='approved'
    assert jobs.get_job(value['result']['review_job_id'])['status']=='pending_review'
    assert value['result']['original_job_id']==source


def test_dispatch_alias_preserves_prior_work_and_target_identifier(tmp_path):
    queue=WorkQueue(tmp_path/'existing.db');handler=Mock()
    queue.enqueue('script','draft');queue.run_next({'script':handler})
    queue.enqueue('script','draft',dispatch_key='human-continuation-1');queue.run_next({'script':handler})
    assert handler.call_count==2
    assert all(call.args==('draft',) for call in handler.call_args_list)
    with queue.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM app_work_queue').fetchone()[0]==2


def test_word_caption_export_keeps_source_and_requires_new_manual_review(tmp_path,monkeypatch):
    from app.models.content_plan import ContentPlan
    from app.services.content_storage import ContentStorage
    from app.services.job_service import JobService
    from app.services.cloud_video_renderer import media_sha256
    from tests.test_content_plan import valid_data
    from tests.test_cloud_media import fixture_video
    monkeypatch.setenv('PRODUCTION_TOOLS_DIR',str(tmp_path/'tools'));monkeypatch.setattr(ProductionTools,'launch',Mock())
    db=tmp_path/'existing.db';source=tmp_path/'source.mp4';fixture_video(source,30)
    data=valid_data();data['scenes']=data['scenes'][:1];data['scenes'][0]['duration_seconds']=30
    plan_id,_=ContentStorage(db,tmp_path/'plans').save_plan(ContentPlan.model_validate(data))
    jobs=JobService(db);job=jobs.create_job(image_mode='text');jobs.update_job(job,'approved',plan_id=plan_id,video_path=str(source))
    original=media_sha256(source)
    monkeypatch.setattr('app.services.speech_analysis.analyze_audio',lambda *a,**kw:{'duration_seconds':30,'words':[{'word':'Hello','start':1,'end':2}],'text':'Hello','word_error_rate':0,'script_matches':True,'human_review_required':True})
    tools=ProductionTools(db);queued=tools.enqueue('captions',{'job_id':job,'expected':'Hello','forced':False,'model':'base'},'admin','caption-1')
    tools.run(queued['tool_job_id']);value=tools.get(queued['tool_job_id'],'admin')
    assert value['status']=='completed',value
    assert media_sha256(source)==original and jobs.get_job(job)['status']=='approved'
    assert jobs.get_job(value['result']['review_job_id'])['status']=='pending_review'
    assert value['result']['speech_quality']['human_review_required'] is True


def test_failed_tool_worker_launch_is_visible_and_not_automatically_retried(tmp_path,monkeypatch):
    monkeypatch.setenv('PRODUCTION_TOOLS_DIR',str(tmp_path/'tools'))
    monkeypatch.setattr('app.services.production_tools.subprocess.Popen',Mock(side_effect=OSError('private-fixture-value')))
    tools=ProductionTools(tmp_path/'existing.db')
    with pytest.raises(ValueError,match='launch'):tools.enqueue('speech',{'text':'Hello'},'admin','failed-1')
    saved=tools.list('admin')[0]
    assert saved['status']=='failed' and 'private-fixture-value' not in saved['error']
    with tools.queue.connect() as conn:assert conn.execute('SELECT status FROM app_work_queue').fetchone()[0]=='interrupted'


def test_neural_voice_manifest_detects_modified_assets(tmp_path,monkeypatch):
    import hashlib
    from app.services.speech_profiles import verified
    root=tmp_path/'speech';monkeypatch.setenv('SPEECH_RUNTIME_ROOT',str(root))
    folder=root/'voices/test-voice';folder.mkdir(parents=True)
    (root/'profiles').mkdir();(folder/'voice.onnx').write_bytes(b'approved-model');(folder/'voice.json').write_text('{}')
    profile={'id':'test-voice','provider':'piper','model':'voice.onnx','config':'voice.json','voice_license':'Approved test license',
        'files':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ('voice.onnx','voice.json')}}
    (root/'profiles/test-voice.json').write_text(json.dumps(profile));assert verified('test-voice')['id']=='test-voice'
    (folder/'voice.onnx').write_bytes(b'changed-model')
    with pytest.raises(ValueError,match='changed'):verified('test-voice')


def test_stock_api_contract_requires_separate_rights_confirmation():
    from app.api.tools_routes import ToolRequest
    from uuid import uuid4
    from pydantic import ValidationError
    payload={'kind':'stock','request_id':str(uuid4()),'candidate_id':str(uuid4()),'confirmed':True}
    with pytest.raises(ValidationError,match='license'):ToolRequest.model_validate(payload)
    assert ToolRequest.model_validate({**payload,'rights_confirmed':True}).rights_confirmed is True


def test_long_film_transcript_can_be_supplied_for_asr_comparison():
    from app.api.tools_routes import ToolRequest
    from uuid import uuid4
    request=ToolRequest.model_validate({'request_id':str(uuid4()),'kind':'captions','job_id':str(uuid4()),'confirmed':True,'expected':'word '*4500})
    assert len(request.expected)>5000 and request.forced is False
