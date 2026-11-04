"""No weights, GPU dependencies, paid generation or uploads in these tests."""
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import time
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan
from app.services.cloud_video_service import CloudVideoService
from app.services.cloud_video_storage import CloudVideoStorage
from app.services.driving_reference_service import DrivingReferenceService, DrivingUpload
from app.services.gemini_video_service import VideoProviderError
from app.services.local_cinematic_service import LocalCinematicService, private_json
from app.services import local_cinematic_models as models
from app.services.provider_catalog import get_catalog, validate_for_script
from app.services.script_generation_service import ScriptGenerationService
from tests.test_gemini_video import cloud_production, FakeVideoProvider
from tests.test_long_drama import long_production


def config(**updates):
    return GenerationConfig.model_validate(dict(video_provider='local_cinematic', video_model='ltx-2.5',
        duration_seconds=45, source_mode='topic', topic='Ocean waves') | updates)


@pytest.fixture
def local_service(tmp_path, monkeypatch):
    for name, suffix in [('LOCAL_CINEMATIC_ROOT','runtime'),('CLOUD_VIDEOS_DIR','videos'),
        ('CONTENT_PLANS_DIR','plans'),('CHARACTER_REFERENCES_DIR','references'),('DRIVING_REFERENCES_DIR','motions')]:
        monkeypatch.setenv(name,str(tmp_path/suffix))
    db = str(tmp_path/'test.db')
    provider, renderer = FakeVideoProvider(), Mock()
    output = tmp_path/'assembled.mp4'; output.write_bytes(b'fixture')
    renderer.assemble.return_value = output, {'technical_passed':True}
    service = CloudVideoService(db,provider=provider,renderer_factory=lambda c:renderer)
    scripts = ScriptGenerationService(db)
    identifier = str(uuid4())
    with scripts.storage.connect() as conn:
        conn.execute('''INSERT INTO script_generation_attempts
            (attempt_id,actor,request_json,provider,model,status,result_json,maximum_cost_usd,created_at,updated_at)
            VALUES (?,'admin',?,'ollama','qwen3:8b','completed',?,'0',1,1)''',
            (identifier,json.dumps(scripts.request(config())),cloud_production().model_dump_json()))
    monkeypatch.setattr('app.services.cloud_video_service.validate_clip',lambda *args,**kwargs:{'technical_passed':True})
    service.script_id = identifier
    return service


def test_catalog_keeps_existing_providers_and_exact_models():
    providers = {item['id']:item for item in get_catalog()['video_providers']}
    assert {'local','google','runway','local_cinematic'} <= providers.keys()
    assert {item['id'] for item in providers['local_cinematic']['models']} == set(models.MODELS)
    assert models.WAN_REPO.endswith('Animate-2-14B-Distilled-Diffusers')


@pytest.mark.parametrize('seconds,frames',[(3,73),(5,121),(10,241)])
def test_ltx_causal_frame_grid(seconds,frames):
    assert models.frame_count(seconds)==frames and (frames-1)%8==0


def test_model_capabilities_and_hybrid_routing():
    with pytest.raises(ValueError,match='requires'):
        models.validate(config(video_model='wan2.2-animate-2'))
    with pytest.raises(ValueError,match='silent'):
        models.validate(config(video_model='wan2.2-animate-2',video_input_mode='image',narration='model_audio'))
    for cast,expected in [([],'ltx'),(['a'],'hybrid'),(['a','b'],'ltx')]:
        assert models.route('ltx-wan-hybrid',{'character_ids':cast})==expected
    with pytest.raises(ValueError,match='exactly one'):
        models.route('wan2.2-animate-2',{'character_ids':['a','b']})
    assert models.dimensions('9:16','wan2.2-animate-2')==(384,672)
    assert models.dimensions('9:16','ltx-wan-hybrid')==(512,896)


@pytest.mark.parametrize('duration',[192,240])
def test_long_local_screenplay(duration):
    c = config(content_type='long_drama',duration_seconds=duration,max_shots=60)
    validate_for_script(c)
    long_production(duration).validate_configuration(c)
    assert 'shared character bible' in ScriptGenerationService.prompt(c,{'category':'entertainment'},[])


def test_local_motion_requires_shot_plan():
    data=cloud_production().model_dump(mode='json');data['shots']=[]
    with pytest.raises(ValueError,match='shot plan'):
        ProductionPlan.model_validate(data).validate_configuration(config())


def test_local_quote_free_single_use_and_script_reuse(local_service):
    s=local_service;q=s.quote(s.script_id,'admin')
    assert q['estimate_usd']=='0' and q['paid_request'] is False and q['provider_label']=='Local cinematic'
    assert s.provider.posts==0
    with pytest.raises(ValueError):s.create(q['quote_id'],'admin',False)
    value=s.create(q['quote_id'],'admin',True)
    assert all(shot['provider']=='local_cinematic' and shot['estimate_usd']=='0' for shot in value['shots'])
    with pytest.raises(ValueError):s.create(q['quote_id'],'admin',True)
    s.process(value['production_id'])
    assert s.provider.posts==0


def test_local_model_prompt_quotes_dialogue_and_excludes_other_cast_metadata(local_service):
    data=cloud_production().model_dump(mode='json')
    data['characters']=[{'character_id':'theo','name':'Theo','description':'A dark-haired man in a blue jacket','voice_notes':'Warm baritone'}]
    data['shots'][0]['character_ids']=['theo']
    words=data['shots'][0]['spoken_text']
    data['dialogue']=[{'scene_number':1,'character_id':'theo','text':words}]
    plan=ProductionPlan.model_validate(data)
    request={'shot':data['shots'][0],'target_duration':data['shots'][0]['duration_seconds']}
    refs=[{'character_id':'someone_else','description':'UNSELECTED_ACTOR','wardrobe':'UNSELECTED_CLOTHING','voice_notes':'UNSELECTED_VOICE'}]
    prompt=local_service.prompt(plan,config(narration='model_audio'),request,refs)
    assert json.dumps(words,ensure_ascii=False) in prompt and 'warm baritone' in prompt
    assert 'Theo' in prompt and 'blue jacket' in prompt
    assert 'UNSELECTED_' not in prompt and 'Content label' not in prompt
    assert 'character_id' not in prompt and 'subtitles' not in prompt and '\n' not in prompt
    silent=local_service.prompt(plan,config(narration='local_tts'),request,refs)
    assert json.dumps(words,ensure_ascii=False) not in silent
    assert 'narration is added locally' not in silent


def test_local_native_dialogue_preserves_speaker_turns(local_service):
    data=cloud_production().model_dump(mode='json')
    data['characters']=[
        {'character_id':'theo','name':'Theo','description':'Man in a blue jacket','voice_notes':'Warm baritone'},
        {'character_id':'maya','name':'Maya','description':'Woman in a burgundy blouse','voice_notes':'Soft alto'}]
    data['content_plan']['scenes'][0]['narration']="Stay with me. I'm here."
    data['shots'][0].update(character_ids=['theo','maya'],spoken_text="Stay with me. I'm here.")
    data['dialogue']=[{'scene_number':1,'character_id':'theo','text':'Stay with me.'},
                      {'scene_number':1,'character_id':'maya','text':"I'm here."}]
    plan=ProductionPlan.model_validate(data)
    prompt=local_service.prompt(plan,config(narration='model_audio'),
        {'shot':data['shots'][0],'target_duration':data['shots'][0]['duration_seconds']},[])
    assert prompt.index('Theo says')<prompt.index('Maya says')
    assert '"Stay with me."' in prompt and '"I\'m here."' in prompt
    assert 'warm baritone' in prompt and 'soft alto' in prompt


def test_local_workflow_preserves_manual_review_and_regeneration(local_service):
    s=local_service;value=s.create(s.quote(s.script_id,'admin')['quote_id'],'admin',True)
    s.provider.submit=Mock(wraps=s.provider.submit)
    s.process(value['production_id'],submit_queued=True)
    inputs=s.provider.submit.call_args.kwargs['local_inputs']
    assert inputs['route']=='ltx' and inputs['width']==512 and inputs['height']==896
    value=s.process(value['production_id'])
    assert value['status']=='technical_ready'
    assert s.jobs.get_job(value['job_id'])['status']=='awaiting_quality_review'
    selected=[shot['attempt_id'] for shot in value['shots'] if shot['selected']]
    q=s.quote_change(value['production_id'],'admin',1)
    new=s.change(q['quote_id'],'admin',True)
    s.process(value['production_id'],submit_queued=True,attempt_ids=[new['attempt_id']])
    s.process(value['production_id'])
    assert [shot['attempt_id'] for shot in s.storage.get(value['production_id'])['shots'] if shot['selected']]==selected
    with pytest.raises(ValueError,match='regeneration'):
        s.quote_change(value['production_id'],'admin',1,operation='edit')


def test_unknown_local_submission_never_restarts(local_service):
    s=local_service;value=s.create(s.quote(s.script_id,'admin')['quote_id'],'admin',True)
    s.provider.fail=True
    value=s.process(value['production_id'],submit_queued=True)
    assert value['shots'][0]['status']=='needs_reconciliation' and s.provider.posts==1
    with pytest.raises(ValueError,match='Reconcile'):s.process(value['production_id'],submit_queued=True)
    s.process(value['production_id']);assert s.provider.posts==1


def test_metrics_sanitize_unknown_values(local_service):
    s=local_service;value=s.create(s.quote(s.script_id,'admin')['quote_id'],'admin',True)
    s.record(value['shots'][0]['attempt_id'],{'interaction_id':str(uuid4()),'status':'in_progress',
        'provider':'local_cinematic','usage':{'stage':'wan_animation','progress':.5,'elapsed_seconds':12,
        'peak_gpu_memory_mb':float('nan'),'token':'private','path':'/private'}})
    shot=s.storage.get(value['production_id'])['shots'][0]
    assert shot['usage']=={'stage':'wan_animation','progress':.5,'elapsed_seconds':12}
    assert shot['calculated_cost_usd']=='0'


def test_worker_isolation_secrets_and_dead_heartbeat(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCAL_CINEMATIC_ROOT',str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY','never-inherit')
    monkeypatch.setattr(LocalCinematicService,'check_model',lambda self:{})
    popen=Mock(return_value=Mock(pid=42));monkeypatch.setattr('app.services.local_cinematic_service.subprocess.Popen',popen)
    snapshot=LocalCinematicService(config()).submit('A moving actor',aspect_ratio='16:9',local_inputs={'shot_number':1})
    args,kw=popen.call_args
    assert args[0][0]==str(tmp_path/'venv/bin/python') and 'OPENAI_API_KEY' not in kw['env']
    assert Path(args[0][1]).is_file()
    assert kw['env']['HF_HUB_OFFLINE']=='1' and kw['start_new_session']
    assert kw['env']['HF_HOME']==str(tmp_path/'cache/huggingface')
    assert str(tmp_path/'sysroot/usr/include/python3.12') in kw['env']['CPATH']
    saved=json.loads((tmp_path/'tasks'/snapshot['interaction_id']/'request.json').read_text())
    assert saved['wan_profile']=={'width':672,'height':384,'segment_frame_length':49}
    task=tmp_path/'tasks'/snapshot['interaction_id']
    assert task.joinpath('request.json').stat().st_mode & 0o777==0o600
    private_json(task/'state.json',{'status':'in_progress','heartbeat':time.time()-130})
    with pytest.raises(VideoProviderError) as error:LocalCinematicService(config()).retrieve(snapshot['interaction_id'])
    assert error.value.uncertain and popen.call_count==1


def test_missing_runtime_does_not_fall_back(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCAL_CINEMATIC_ROOT',str(tmp_path))
    with pytest.raises(VideoProviderError,match='setup incomplete'):LocalCinematicService(config()).check_model()


def test_worker_cli_uses_exact_checkpoint_and_frame_grid():
    spec=importlib.util.spec_from_file_location('worker',Path(__file__).parents[1]/'app/workers/local_cinematic_worker.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args=module.ltx_arguments({'duration':5,'height':512,'width':896,'seed':42,'prompt':'a moving actor',
        'ltx_files':{'transformer-path':'/verified/model.safetensors'},'start_image':'/permitted/image.png'},Path('/output.mp4'))
    assert args[:3]==['ltx_pipelines.distilled','--transformer-path','/verified/model.safetensors']
    assert args[args.index('--num-frames')+1]=='121' and args[-4:]==['--image','/permitted/image.png','0','1.0']


def test_driving_registry_rights_motion_hash_privacy(tmp_path,monkeypatch):
    monkeypatch.setenv('DRIVING_REFERENCES_DIR',str(tmp_path/'private'))
    output=tmp_path/'motion.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x160:rate=24:duration=5',
        '-c:v','libx264','-pix_fmt','yuv420p',str(output)],check=True)
    s=DrivingReferenceService(str(tmp_path/'test.db'))
    body={'name':'Own motion','rights_note':'Original recording','video_base64':base64.b64encode(output.read_bytes()).decode()}
    with pytest.raises(ValueError,match='permission'):s.create(DrivingUpload(**body))
    saved=s.create(DrivingUpload(**body,rights_confirmed=True))
    assert saved['quality']['motion_samples_distinct']>=2 and 'video_path' not in saved
    path=Path(s.verified(saved['reference_id'])['video_path'])
    assert path.stat().st_mode & 0o777==0o600
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):s.verified(saved['reference_id'])


def test_existing_database_migration_preserves_rows(tmp_path):
    db=tmp_path/'legacy.db';s=CloudVideoStorage(str(db))
    with s.connect() as conn:
        conn.execute("INSERT INTO video_generation_quotes VALUES ('old','admin','{}','hash','1',10,NULL)")
        conn.execute('DROP TABLE driving_references')
    CloudVideoStorage(str(db));CloudVideoStorage(str(db))
    with s.connect() as conn:
        assert conn.execute('SELECT actor FROM video_generation_quotes').fetchone()[0]=='admin'
        assert conn.execute('SELECT COUNT(*) FROM driving_references').fetchone()[0]==0


def test_real_adapter_uuids_persist_and_refresh_never_relaunches(local_service,monkeypatch):
    s=local_service;s.provider=None
    monkeypatch.setattr(LocalCinematicService,'check_model',lambda self:{})
    launch=Mock(return_value=Mock(pid=42))
    monkeypatch.setattr('app.services.local_cinematic_service.subprocess.Popen',launch)
    value=s.create(s.quote(s.script_id,'admin')['quote_id'],'admin',True)
    value=s.process(value['production_id'],submit_queued=True)
    identifiers=[shot['generation_id'] for shot in value['shots']]
    from uuid import UUID
    assert all(UUID(identifier) for identifier in identifiers)
    count=launch.call_count
    for identifier in identifiers:
        directory=models.runtime_root()/'tasks'/identifier
        (directory/'output.mp4').write_bytes(b'fixture')
        private_json(directory/'state.json',{'status':'completed','stage':'completed','progress':1,
            'elapsed_seconds':12,'peak_gpu_memory_mb':1000,'heartbeat':time.time()})
    value=s.process(value['production_id'])
    assert value['status']=='technical_ready'
    assert [shot['generation_id'] for shot in value['shots']]==identifiers
    assert launch.call_count==count
    assert s.jobs.get_job(value['job_id'])['status']=='awaiting_quality_review'


def test_hybrid_local_assembly_preserves_ltx_audio_and_motion(tmp_path,monkeypatch):
    from app.services.cloud_video_renderer import validate_clip
    from tests.test_cloud_media import fixture_video
    spec=importlib.util.spec_from_file_location('worker_audio',Path(__file__).parents[1]/'app/workers/local_cinematic_worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    stages=[]
    def stage(name,directory,state,lock_fd):
        stages.append(name)
        fixture_video(directory/(name+'.mp4'),5,audio=name=='ltx')
    monkeypatch.setattr(worker,'run_model',stage)
    worker.generate({'route':'hybrid','reference_files':[],'duration':5,'width':160,'height':284},tmp_path,{},7)
    quality=validate_clip(tmp_path/'output.mp4',5,require_audio=True)
    assert quality['motion_samples_distinct']>=2
    assert stages==['ltx','wan']


def test_neural_child_inherits_gpu_lock_and_reports_metrics(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('worker_lock',Path(__file__).parents[1]/'app/workers/local_cinematic_worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    (tmp_path/'ltx-metrics.json').write_text(json.dumps({'status':'completed','peak_gpu_memory_mb':1000,'peak_rss_mb':2000,'elapsed_seconds':30}))
    child=Mock(returncode=0,pid=123);child.poll.return_value=0
    run=Mock(return_value=child);monkeypatch.setattr(worker.subprocess,'Popen',run)
    monkeypatch.setattr(worker,'available_memory_mb',lambda:100_000)
    state={};worker.run_model('ltx',tmp_path,state,13)
    assert run.call_args.kwargs['pass_fds']==(13,)
    assert run.call_args.kwargs['start_new_session'] is True
    assert json.loads((tmp_path/'ltx-process.json').read_text())['pid']==123
    assert run.call_args.args[0][-3:]==['--model-stage','ltx',str(tmp_path)]
    assert state=={'peak_gpu_memory_mb':1000,'peak_rss_mb':2000,'ltx_seconds':30}


def test_failed_neural_stage_keeps_peak_metrics_without_echoing_raw_error(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('worker_failure',Path(__file__).parents[1]/'app/workers/local_cinematic_worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    (tmp_path/'request.json').write_text('{}')
    cuda=SimpleNamespace(is_available=lambda:True,reset_peak_memory_stats=lambda:None,max_memory_allocated=lambda:1024*1024*512,set_per_process_memory_fraction=Mock())
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(cuda=cuda))
    failure=type('OutOfMemoryError',(RuntimeError,),{})
    monkeypatch.setattr(worker,'ltx',Mock(side_effect=failure('raw private prompt must not enter metrics')))
    assert worker.model_stage('ltx',tmp_path)==1
    metrics=json.loads((tmp_path/'ltx-metrics.json').read_text())
    assert metrics['error']=='out_of_memory' and metrics['peak_gpu_memory_mb']==512
    cuda.set_per_process_memory_fraction.assert_called_once_with(.5)
    assert 'private prompt' not in json.dumps(metrics)


def worker_module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'app/workers/local_cinematic_worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    return worker


def test_wan_requires_compilation_and_never_falls_back_eagerly():
    from types import SimpleNamespace
    worker=worker_module('compiled_wan')
    original=Mock();compiled=Mock()
    backend=SimpleNamespace(flex_attention=original,BlockMask=object)
    dispatch=SimpleNamespace(flex_attention=backend)
    torch=SimpleNamespace(compile=Mock(return_value=compiled))
    assert worker.compile_wan_attention(torch,dispatch) is compiled
    torch.compile.assert_called_once_with(original,fullgraph=True)
    assert backend.flex_attention is original
    assert dispatch.flex_attention.BlockMask is object
    assert worker.compile_wan_attention(torch,dispatch) is compiled
    assert torch.compile.call_count==1
    compiled.side_effect=RuntimeError('Compile failed')
    with pytest.raises(RuntimeError,match='Compile failed'):dispatch.flex_attention.flex_attention()
    original.assert_not_called()


def test_wan_offload_strategy_keeps_hot_models_and_releases_encoders():
    worker=worker_module('wan_offload')
    transformer,vae,text_encoder,image_encoder=object(),object(),object(),object()
    hooks=[Mock(model=model) for model in (transformer,vae,text_encoder,image_encoder)]
    select=worker.offload_except(transformer,vae)
    assert select(hooks,model_id='arbitrary-registry-uuid')==hooks[2:]
    assert select(hooks[:2])==[] and select([])==[]


def test_host_memory_guard_blocks_before_launch(tmp_path,monkeypatch):
    worker=worker_module('memory_before_launch')
    launch=Mock();monkeypatch.setattr(worker.subprocess,'Popen',launch)
    monkeypatch.setattr(worker,'available_memory_mb',lambda:1024)
    with pytest.raises(worker.LocalMemoryError):worker.run_model('wan',tmp_path,{},13)
    launch.assert_not_called()


def test_host_memory_guard_stops_only_its_neural_process_group(tmp_path,monkeypatch):
    worker=worker_module('memory_after_launch')
    child=Mock(pid=123);child.poll.return_value=None
    monkeypatch.setattr(worker.subprocess,'Popen',Mock(return_value=child))
    monkeypatch.setattr(worker,'available_memory_mb',Mock(side_effect=[100_000,1]))
    signal=Mock();monkeypatch.setattr(worker.os,'killpg',signal)
    with pytest.raises(worker.LocalMemoryError):worker.run_model('wan',tmp_path,{},13)
    signal.assert_called_once_with(123,worker.signal.SIGTERM)
    child.wait.assert_called_once_with(timeout=5)


def test_neural_timeout_stops_child_without_restart(tmp_path,monkeypatch):
    worker=worker_module('timeout_guard')
    child=Mock(pid=123);child.poll.return_value=None
    launch=Mock(return_value=child);monkeypatch.setattr(worker.subprocess,'Popen',launch)
    monkeypatch.setattr(worker,'available_memory_mb',lambda:100_000)
    monkeypatch.setattr(worker.time,'monotonic',Mock(side_effect=[0,2000]))
    signal=Mock();monkeypatch.setattr(worker.os,'killpg',signal)
    with pytest.raises(worker.LocalTimeoutError):worker.run_model('wan',tmp_path,{},13)
    assert launch.call_count==1
    signal.assert_called_once_with(123,worker.signal.SIGTERM)


@pytest.mark.parametrize('setting,value',[('LOCAL_CINEMATIC_GPU_MEMORY_FRACTION','nan'),('LOCAL_CINEMATIC_GPU_MEMORY_FRACTION','1'),('LOCAL_CINEMATIC_MIN_AVAILABLE_GB','0'),('LOCAL_CINEMATIC_STAGE_TIMEOUT_SECONDS','0')])
def test_invalid_memory_limits_rejected(setting,value,monkeypatch):
    monkeypatch.setenv(setting,value)
    with pytest.raises(ValueError):models.runtime_policy()


@pytest.mark.parametrize('fails',[False,True])
def test_private_stage_diagnostics_cancel_timer_and_preserve_metrics(tmp_path,monkeypatch,fails):
    import sys
    worker=worker_module('stage_diagnostics')
    torch=Mock();torch.cuda.is_available.return_value=True
    torch.cuda.max_memory_allocated.return_value=1024**2
    monkeypatch.setitem(sys.modules,'torch',torch)
    timer=Mock();monkeypatch.setattr(worker,'faulthandler',timer)
    (tmp_path/'request.json').write_text('{}')
    inference=Mock(side_effect=MemoryError('private model diagnostic') if fails else None)
    monkeypatch.setattr(worker,'ltx',inference)
    assert worker.model_stage('ltx',tmp_path)==(1 if fails else 0)
    timer.dump_traceback_later.assert_called_once_with(180,repeat=True)
    timer.cancel_dump_traceback_later.assert_called_once_with()
    metrics=json.loads((tmp_path/'ltx-metrics.json').read_text())
    assert metrics['status']==('failed' if fails else 'completed')
    assert metrics['peak_gpu_memory_mb']==1
    assert 'private model diagnostic' not in json.dumps(metrics)
