"""Cast, continuation and long-film recovery. Neural and paid calls stay mocked."""
import base64
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest
import requests

from app.models.generation_config import GenerationConfig
from app.services.cinematic_cast_service import CinematicCastService
from app.services.cinematic_continuity import prepare_tail, source_dependency, stale_dependencies
from app.services.cinematic_screenplay import chapter_configs
from app.services.cloud_video_renderer import media_sha256
from app.services.local_production_runner import LocalProductionRunner
from app.services.script_generation_service import ScriptGenerationService
from app.services.anthropic_script_service import ANTHROPIC_MODEL
from app.services.openai_script_service import PaidScriptError
from tests.test_local_cinematic import local_service
from tests.test_cloud_media import fixture_video
from tests.test_long_drama import long_production
from tests.test_generation_api import generation_api, sign_in


def image_reference(service, actor='actor_a', color='navy'):
    buffer = io.BytesIO(); Image.new('RGB',(192,192),color).save(buffer,format='PNG')
    return service.references.create({'character_id':actor,'name':'Alex' if actor=='actor_a' else 'Sam',
        'description':'Fictional explorer','wardrobe':'Blue jacket','voice_notes':'Calm',
        'rights_note':'Synthetic test fixture','rights_confirmed':True,'image_base64':base64.b64encode(buffer.getvalue()).decode()})


def package(service, actors=('actor_a',)):
    refs = [image_reference(service, actor) for actor in actors]
    return service.cast.create({'name':'Shared cast','confirmed':True,'location_notes':'Bright kitchen',
        'characters':[{'character_id':ref['character_id'],'name':ref['name'],'description':ref['description'],
            'wardrobe':ref['wardrobe'],'voice_notes':ref['voice_notes'], 'reference_ids':[ref['reference_id']],
            'primary_reference_id':ref['reference_id']} for ref in refs]})


def connect_fixture(service):
    cast = package(service)
    with service.storage.connect() as conn:
        row = conn.execute('SELECT * FROM script_generation_attempts WHERE attempt_id=?',(service.script_id,)).fetchone()
        request, plan = json.loads(row['request_json']), json.loads(row['result_json'])
        request['configuration'].update(continuity_mode='connected', cast_package_id=cast['package_id'], video_input_mode='image')
        plan['characters'] = [{key:cast['characters'][0][key] for key in ('character_id','name','description','voice_notes')}]
        for shot in plan['shots']:
            shot['character_ids'] = ['actor_a']
            shot['transition'] = 'continue' if shot['shot_number'] in {2,4,5} else 'cut'
        conn.execute('UPDATE script_generation_attempts SET request_json=?,result_json=? WHERE attempt_id=?',
            (json.dumps(request),json.dumps(plan),service.script_id))
    return cast


def test_cast_multiview_package_is_immutable_and_hash_bound(local_service):
    service=local_service
    one, two = image_reference(service), image_reference(service,color='red')
    body={'name':'Alex','confirmed':True,'characters':[{'character_id':'actor_a','name':'Alex','description':'Explorer',
        'wardrobe':'Blue jacket','reference_ids':[one['reference_id'],two['reference_id']], 'primary_reference_id':one['reference_id']}]}
    saved=service.cast.create(body)
    assert len(saved['characters'][0]['reference_ids'])==2
    assert 'image_path' not in json.dumps(saved)
    with pytest.raises(ValueError,match='confirm'):
        service.cast.create(body | {'confirmed':False})
    Path(two['image_path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):
        service.cast.get(saved['package_id'])


@pytest.mark.parametrize('duration,shots,count',[(900,180,5),(1800,360,10),(1800,600,10),(241,49,2)])
def test_local_long_contracts_and_chapter_layout(duration,shots,count):
    config=GenerationConfig(video_provider='local_cinematic',video_model='ltx-2.5',content_type='long_drama',
        duration_seconds=duration,max_shots=shots)
    parts=chapter_configs(config)
    assert len(parts)==count
    assert sum(part.duration_seconds for part in parts)==duration
    assert sum(part.max_shots for part in parts)==shots
    assert all(120<=part.duration_seconds<=180 for part in parts)
    with pytest.raises(ValueError):
        GenerationConfig(video_provider='runway',duration_seconds=1800)


def test_worker_carries_context_and_trims_it_off_the_story_timeline():
    path=Path(__file__).parents[1]/'app/workers/local_cinematic_worker.py'
    spec=importlib.util.spec_from_file_location('carry_worker',path);worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    request={'duration':5,'width':512,'height':896,'seed':1,'prompt':'A moving actor','ltx_files':{},
        'continuation_frames':[{'path':f'/frame_{i}.png','frame':i} for i in range(17)]}
    args=worker.ltx_arguments(request,'output.mp4')
    assert args[args.index('--num-frames')+1]=='137'
    assert args.count('--image')==3
    assert '/frame_16.png' in args
    assert 'no speech' in args[args.index('--prompt')+1]
    native=worker.ltx_arguments({**request,'continuation_frames':[], 'native_chunking':True,
        'scene_keyframes':[{'path':'/next-scene.png','frame':360}]},'output.mp4')
    assert '--chunked' in native and native[native.index('--chunk-carry-frames')+1]=='25'
    assert '/next-scene.png' in native and '360' in native


def test_actor_switch_requires_a_cut_instead_of_carrying_the_wrong_face():
    from app.models.production_plan import ProductionPlan
    from tests.test_gemini_video import cloud_production
    data=cloud_production().model_dump(mode='json')
    data['characters']=[{'character_id':actor,'name':actor,'description':'A fictional actor'} for actor in ('actor_a','actor_b')]
    data['shots'][0]['character_ids']=['actor_a']
    data['shots'][1].update(character_ids=['actor_b'],transition='continue')
    with pytest.raises(ValueError,match='same on-screen cast'):
        ProductionPlan.model_validate(data)
    data['shots'][1]['transition']='cut'
    ProductionPlan.model_validate(data)


@pytest.mark.parametrize('legacy_authorization',[False,True])
def test_connected_hybrid_freezes_cut_and_continuation_stages_without_changing_old_authorizations(local_service,monkeypatch,legacy_authorization):
    service=local_service;cast=connect_fixture(service)
    with service.storage.connect() as conn:
        request=json.loads(conn.execute('SELECT request_json FROM script_generation_attempts WHERE attempt_id=?',(service.script_id,)).fetchone()[0])
        request['configuration']['video_model']='ltx-wan-hybrid'
        conn.execute('UPDATE script_generation_attempts SET request_json=? WHERE attempt_id=?',(json.dumps(request),service.script_id))
    monkeypatch.setattr('app.services.cloud_video_service.validate_clip',lambda path,*args,**kwargs:{'technical_passed':True,'sha256':media_sha256(path)})
    monkeypatch.setattr('app.services.cinematic_continuity.prepare_tail',Mock(return_value=[]))
    submit=Mock(wraps=service.provider.submit);monkeypatch.setattr(service.provider,'submit',submit)
    quote=service.quote(service.script_id,'admin',start_image_reference_id=cast['characters'][0]['primary_reference_id'])
    assert 'continuous action uses LTX' in quote['notice']
    value=service.create(quote['quote_id'],'admin',True)
    assert value['shots'][0]['generation_route']=='hybrid'
    assert value['shots'][1]['generation_route']=='ltx'
    manifest=Path(service.renderer_factory(None).assemble.return_value[0]).parent/'manifest.json'
    manifest.write_text('{}')
    if legacy_authorization:
        with service.storage.connect() as conn:
            for row in conn.execute('SELECT attempt_id,request_json FROM video_shots WHERE production_id=?',(value['production_id'],)).fetchall():
                old=json.loads(row['request_json']);old.pop('local_route')
                conn.execute('UPDATE video_shots SET request_json=? WHERE attempt_id=?',(json.dumps(old),row['attempt_id']))
    clock=[1000];monkeypatch.setattr('app.services.cloud_video_service.time.time',lambda:clock[0])
    with monkeypatch.context() as patch:
        if not legacy_authorization:
            patch.setattr('app.services.local_cinematic_models.route',Mock(side_effect=AssertionError('Frozen authorizations must not be rerouted')))
        for _ in range(20):
            clock[0]+=10;value=service.process(value['production_id'],submit_queued=True)
            if value['status']=='technical_ready':break
    assert value['status']=='technical_ready'
    routes=[call.kwargs['local_inputs']['route'] for call in submit.call_args_list]
    assert routes[:2]==['hybrid','hybrid' if legacy_authorization else 'ltx']
    assert json.loads(manifest.read_text())['local_models']['shot_routes']['2']==('hybrid' if legacy_authorization else 'ltx')
    quote=service.quote_change(value['production_id'],'admin',2)
    candidate=service.change(quote['quote_id'],'admin',True)
    assert json.loads(service.storage.attempt(candidate['attempt_id'])['request_json'])['local_route']=='ltx'
    assert service.storage.attempt(value['shots'][1]['attempt_id'])['selected']


def test_real_cpu_tail_extraction_is_private_and_exact(tmp_path):
    video=tmp_path/'source.mp4';fixture_video(video,3)
    frames=prepare_tail({'source_trim':0,'source_duration':3,'clip_path':str(video)},tmp_path/'tail')
    assert len(frames)==17 and frames[-1]['frame']==16
    assert all(Path(item['path']).stat().st_mode & 0o777 == 0o600 for item in frames)
    assert all(item['sha256']==media_sha256(item['path']) for item in frames)


def test_connected_shots_wait_for_source_and_replacement_invalidates_dependents(local_service,monkeypatch):
    service=local_service;cast=connect_fixture(service)
    monkeypatch.setattr('app.services.cloud_video_service.validate_clip',lambda path,*args,**kw:{'technical_passed':True,'sha256':media_sha256(path)})
    tail=Mock(return_value=[]);monkeypatch.setattr('app.services.cinematic_continuity.prepare_tail',tail)
    image=cast['characters'][0]['primary_reference_id']
    quote=service.quote(service.script_id,'admin',start_image_reference_id=image)
    production=service.create(quote['quote_id'],'admin',True)
    clock=[1000];monkeypatch.setattr('app.services.cloud_video_service.time.time',lambda:clock[0])
    for _ in range(20):
        clock[0]+=10
        production=service.process(production['production_id'],submit_queued=True)
        if production['status']=='technical_ready':break
    assert production['status']=='technical_ready'
    assert tail.call_count==3
    second=next(item for item in production['shots'] if item['shot_number']==2)
    assert second['conditioning_source']['source_attempt_id']==production['shots'][0]['attempt_id']
    assert not stale_dependencies(service.storage,production['production_id'])
    quote=service.quote_change(production['production_id'],'admin',1)
    candidate=service.change(quote['quote_id'],'admin',True)
    for _ in range(3):
        clock[0]+=10;service.process(production['production_id'],submit_queued=True,attempt_ids=[candidate['attempt_id']])
    changed=service.select(production['production_id'],candidate['attempt_id'])
    assert changed['status']=='continuity_stale'
    assert second['attempt_id'] in stale_dependencies(service.storage,production['production_id'])
    with pytest.raises(ValueError):
        service.send_to_review(production['production_id'],continuity_confirmed=True,captions_confirmed=True,rights_confirmed=True)


def test_runner_bounds_authorized_ids_pause_and_secret_isolation(local_service,monkeypatch):
    service=local_service
    production=service.create(service.quote(service.script_id,'admin')['quote_id'],'admin',True)
    monkeypatch.setenv('ANTHROPIC_API_KEY','private-never-inherit')
    monkeypatch.setenv('TTS_VOICE','en-us+f2')
    popen=Mock(return_value=Mock());monkeypatch.setattr('app.services.local_production_runner.subprocess.Popen',popen)
    runner=LocalProductionRunner(service)
    allowed=[production['shots'][0]['attempt_id']]
    run=runner.launch(production['production_id'],allowed)
    assert 'ANTHROPIC_API_KEY' not in popen.call_args.kwargs['env']
    assert popen.call_args.kwargs['env']['TTS_VOICE']=='en-us+f2'
    service.process=Mock(return_value={'status':'processing'})
    assert runner.tick(run)
    assert service.process.call_args.kwargs['attempt_ids']==allowed
    runner.pause(production['production_id'])
    assert not runner.tick(run)
    service.process.assert_called_once()


def test_bounded_runner_stops_without_claiming_other_queued_shots(local_service,monkeypatch):
    service=local_service
    production=service.create(service.quote(service.script_id,'admin')['quote_id'],'admin',True)
    monkeypatch.setattr('app.services.local_production_runner.subprocess.Popen',Mock(return_value=Mock()))
    runner=LocalProductionRunner(service);first=production['shots'][0]['attempt_id']
    identifier=runner.launch(production['production_id'],[first])
    result={**production,'status':'processing','shots':[{**shot,'status':'completed' if shot['attempt_id']==first else 'queued'} for shot in production['shots']]}
    service.process=Mock(return_value=result)
    assert runner.tick(identifier) is False
    assert service.process.call_args.kwargs['attempt_ids']==[first]
    with service.storage.connect() as conn:
        assert conn.execute('SELECT status FROM local_production_runs WHERE run_id=?',(identifier,)).fetchone()[0]=='stopped'


def test_continuity_checks_do_not_approve_or_publish_and_all_shots_need_human_checks(local_service,monkeypatch):
    service=local_service;cast=connect_fixture(service)
    monkeypatch.setattr('app.services.cloud_video_service.validate_clip',lambda path,*args,**kwargs:{'technical_passed':True,'sha256':media_sha256(path)})
    monkeypatch.setattr('app.services.cinematic_continuity.prepare_tail',Mock(return_value=[]))
    value=service.create(service.quote(service.script_id,'admin',start_image_reference_id=cast['characters'][0]['primary_reference_id'])['quote_id'],'admin',True)
    clock=[1000];monkeypatch.setattr('app.services.cloud_video_service.time.time',lambda:clock[0])
    for _ in range(20):
        clock[0]+=10;value=service.process(value['production_id'],submit_queued=True)
        if value['status']=='technical_ready':break
    checks=dict(continuity_confirmed=True,captions_confirmed=True,rights_confirmed=True)
    with pytest.raises(ValueError,match='each selected shot'):
        service.send_to_review(value['production_id'],**checks)
    for shot in value['shots']:
        with pytest.raises(ValueError,match='Confirm'):
            service.accept_continuity(value['production_id'],shot['attempt_id'],False)
        service.accept_continuity(value['production_id'],shot['attempt_id'],True)
        assert service.jobs.get_job(value['job_id'])['status']=='awaiting_quality_review'
    assert service.send_to_review(value['production_id'],**checks)['status']=='pending_review'
    assert service.jobs.get_job(value['job_id'])['status']=='pending_review'


def test_stale_runner_requires_inspection_and_resume_keeps_existing_generation_ids(local_service,monkeypatch):
    service=local_service
    production=service.create(service.quote(service.script_id,'admin')['quote_id'],'admin',True)
    popen=Mock(return_value=Mock());monkeypatch.setattr('app.services.local_production_runner.subprocess.Popen',popen)
    runner=LocalProductionRunner(service)
    run=runner.launch(production['production_id'])
    first=production['shots'][0]['attempt_id']
    with service.storage.connect() as conn:
        conn.execute('UPDATE local_production_runs SET heartbeat=1 WHERE run_id=?',(run,))
        conn.execute("UPDATE video_shots SET status='processing',interaction_id='existing-local-id' WHERE attempt_id=?",(first,))
    with pytest.raises(ValueError,match='stale'):
        runner.launch(production['production_id'])
    assert popen.call_count==1
    runner.pause(production['production_id'])
    resumed=runner.launch(production['production_id'])
    with service.storage.connect() as conn:
        allowed=json.loads(conn.execute('SELECT attempt_ids_json FROM local_production_runs WHERE run_id=?',(resumed,)).fetchone()[0])
    assert first not in allowed and len(allowed)==len(production['shots'])-1
    service.process=Mock(return_value={'status':'processing'})
    assert runner.tick(resumed)
    assert service.storage.attempt(first)['interaction_id']=='existing-local-id'


@pytest.mark.parametrize('duration',[900,1800])
def test_chapter_cost_bound_and_merged_timeline(local_service,monkeypatch,duration):
    monkeypatch.setenv('ANTHROPIC_API_KEY','dummy-private-key')
    cast=package(local_service,('actor_a','actor_b'))
    config=GenerationConfig(video_provider='local_cinematic',video_model='ltx-2.5',content_type='long_drama',
        script_provider='anthropic',script_model=ANTHROPIC_MODEL,source_mode='topic',topic='A kitchen mystery',
        duration_seconds=duration,max_shots=duration//5,max_cost_usd=25,cast_package_id=cast['package_id'])
    calls=Mock()
    def generate(prompt,configuration):
        calls(prompt)
        plan=long_production(((configuration.duration_seconds+7)//8)*8)
        excess=plan.content_plan.total_duration-configuration.duration_seconds
        plan.content_plan.scenes[-1].duration_seconds-=excess;plan.shots[-1].duration_seconds-=excess
        return plan
    factory=lambda model:SimpleNamespace(check_model=lambda:None,generate_production_plan=generate,
        last_usage={'input_tokens':1000,'output_tokens':2000},last_response_id='msg_chapter')
    scripts=ScriptGenerationService(local_service.storage.db_path,anthropic_factory=factory)
    request=scripts.request(config)
    quote=scripts.quote(request,'admin')
    count=len(chapter_configs(config))
    assert quote['max_requests']==count
    assert float(quote['maximum_cost_usd'])==pytest.approx(count*1.06)
    identifier=scripts.submit(request,'admin',quote_id=quote['quote_id'],confirmed=True)
    scripts.run(identifier);scripts.run(identifier)
    result=scripts.get(identifier)
    assert result['status']=='completed',result['error']
    assert sum(scene['duration_seconds'] for scene in result['production_plan']['content_plan']['scenes'])==duration
    assert len(result['parts'])==count and calls.call_count==count
    assert float(result['calculated_cost_usd'])==pytest.approx(count*.022)
    # A crash after saving all chapter results can finish without another paid request.
    with scripts.storage.connect() as conn:
        conn.execute("UPDATE script_generation_attempts SET status='needs_reconciliation',result_json=NULL WHERE attempt_id=?",(identifier,))
    scripts.resume_chapters(identifier,'admin',True)
    scripts.run(identifier)
    assert scripts.get(identifier)['status']=='completed' and calls.call_count==count
    with scripts.storage.connect() as conn:
        conn.execute("UPDATE script_generation_attempts SET status='needs_reconciliation' WHERE attempt_id=?",(identifier,))
        conn.execute("UPDATE script_generation_parts SET status='running' WHERE attempt_id=? AND part_number=2",(identifier,))
    with pytest.raises(ValueError,match='uncertain'):
        scripts.resume_chapters(identifier,'admin',True)


def test_cast_and_continuity_endpoints_need_session_and_csrf(generation_api,monkeypatch):
    api=generation_api
    assert api.client.get('/api/v1/generation/cast-packages',headers=api.api_headers).status_code==401
    headers=sign_in(api)
    assert api.client.get('/api/v1/generation/cast-packages').json()=={'packages':[]}
    assert api.client.post('/api/v1/generation/cast-packages',json={}).status_code==403
    route='/api/v1/generation/scripts/11111111-1111-4111-8111-111111111111/continue'
    assert api.client.post(route,json={'confirmed':True}).status_code==403
    assert api.client.post(route,headers=headers,json={'confirmed':False}).status_code==422
    api.publisher.assert_not_called()


def test_fixed_character_voices_are_reused_per_speaker(local_service,monkeypatch,tmp_path):
    from app.services.character_dialogue_audio import render_dialogue
    from app.services.video_renderer import VideoRenderer
    from app.services.tts_service import TTSService
    plan=long_production(120)
    cast=package(local_service,('actor_a','actor_b'))
    cast['characters'][0]['tts_voice']='en-us+m1'
    cast['characters'][1]['tts_voice']='en-us+f2'
    real=TTSService.synthesize
    calls=[]
    def synthesize(self,text,path):
        calls.append(self.voice)
        return real(self,text,path)
    monkeypatch.setattr(TTSService,'synthesize',synthesize)
    renderer=VideoRenderer(160,284,24)
    for shot in plan.shots[:3]:
        output=render_dialogue(plan,shot,cast,tmp_path/str(shot.shot_number),renderer)
        assert abs(renderer.audio_duration(output)-shot.duration_seconds)<.1
    assert calls==['en-us+m1','en-us+f2','en-us+m1']


def test_optional_adapter_registration_binds_settings_and_weights(local_service,tmp_path):
    from app.services.cinematic_adapter_service import CinematicAdapterService
    import struct
    header=json.dumps({'layer.lora_A.weight':{'dtype':'F32','shape':[1,1],'data_offsets':[0,4]}}).encode()
    header+=b' '*((-len(header))%8)
    source=tmp_path/'adapter.safetensors';source.write_bytes(struct.pack('<Q',len(header))+header+b'\x00'*4)
    adapters=CinematicAdapterService()
    with pytest.raises(ValueError,match='confirm'):
        adapters.register(source,name='Alex',license_note='Synthetic fixture',trigger='Alex actor')
    registered=adapters.register(source,name='Alex',license_note='Synthetic fixture; not real trained weights',trigger='Alex actor',confirmed=True)
    ref=image_reference(local_service)
    saved=local_service.cast.create({'name':'Adapted actor','confirmed':True,'characters':[{
        'character_id':'actor_a','name':'Alex','description':'Explorer','wardrobe':'Blue jacket',
        'reference_ids':[ref['reference_id']],'primary_reference_id':ref['reference_id'],
        'ltx_adapter_id':registered['adapter_id']}]})
    assert saved['adapters'][0]['settings_sha256']
    assert 'path' not in adapters.list_adapters()[0]
    manifest=adapters.root/(registered['adapter_id']+'.json')
    data=json.loads(manifest.read_text());data['scale']=.2;manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='changed'):
        local_service.cast.get(saved['package_id'])


def test_scene_identity_views_must_belong_to_the_approved_actor(local_service,monkeypatch):
    service=local_service;cast=connect_fixture(service)
    extra=image_reference(service,color='orange')
    characters=cast['characters'];characters[0]['reference_ids'].append(extra['reference_id'])
    updated=service.cast.create({'name':'Two views','confirmed':True,'characters':characters})
    with service.storage.connect() as conn:
        request=json.loads(conn.execute('SELECT request_json FROM script_generation_attempts WHERE attempt_id=?',(service.script_id,)).fetchone()[0])
        request['configuration'].update(cast_package_id=updated['package_id'],video_model='ltx-wan-hybrid')
        conn.execute('UPDATE script_generation_attempts SET request_json=? WHERE attempt_id=?',(json.dumps(request),service.script_id))
    quote=service.quote(service.script_id,'admin',start_image_reference_id=extra['reference_id'],
        scene_character_reference_ids={1:{'actor_a':extra['reference_id']}})
    assert quote['paid_request'] is False
    wrong=image_reference(service,actor='actor_b')
    with pytest.raises(ValueError,match='different character'):
        service.quote(service.script_id,'admin',start_image_reference_id=extra['reference_id'],
            scene_character_reference_ids={1:{'actor_a':wrong['reference_id']}})
    submit=Mock(wraps=service.provider.submit);monkeypatch.setattr(service.provider,'submit',submit)
    value=service.create(quote['quote_id'],'admin',True)
    service.process(value['production_id'],submit_queued=True)
    inputs=submit.call_args.kwargs['local_inputs']
    assert inputs['character_image']==extra['image_path']
    assert {'path':extra['image_path'],'sha256':extra['sha256']} in inputs['reference_files']


def test_approved_cast_text_overrides_old_registry_descriptions(local_service):
    from app.models.production_plan import ProductionPlan
    cast=connect_fixture(local_service)
    with local_service.storage.connect() as conn:
        row=conn.execute('SELECT request_json,result_json FROM script_generation_attempts WHERE attempt_id=?',(local_service.script_id,)).fetchone()
    config=GenerationConfig.model_validate(json.loads(row['request_json'])['configuration'])
    plan=ProductionPlan.model_validate_json(row['result_json'])
    cast['characters'][0].update(description='Canonical approved actor',wardrobe='Canonical approved coat',voice_notes='Canonical approved voice')
    plan=local_service.cast.bind(plan,cast)
    refs=[{'character_id':'actor_a','description':'OUTDATED FACE','wardrobe':'OUTDATED CLOTHES','voice_notes':'OUTDATED VOICE'}]
    config.narration='model_audio'
    prompt=local_service.prompt(plan,config,{'shot':plan.shots[0].model_dump(),'target_duration':plan.shots[0].duration_seconds},refs)
    assert 'Canonical approved actor' in prompt and 'Canonical approved coat' in prompt
    assert 'canonical approved voice' in prompt
    assert 'OUTDATED' not in prompt
    cast['characters'][0].update(description='A'*1000,wardrobe='Blue coat with brass buttons and distinctive red boots')
    constrained=local_service.cast.bind(plan,cast)
    assert len(constrained.characters[0].description)<=1000
    assert 'Blue coat with brass buttons and distinctive red boots' in constrained.characters[0].description


@pytest.mark.parametrize('adoption_failure',[False,True])
@pytest.mark.parametrize('script_provider',['anthropic','ollama'])
def test_failed_chapter_is_revised_only_with_new_cost_consent_then_resumes(local_service,monkeypatch,adoption_failure,script_provider):
    monkeypatch.setenv('ANTHROPIC_API_KEY','dummy-private-key')
    cast=package(local_service,('actor_a','actor_b'))
    config=GenerationConfig(video_provider='local_cinematic',video_model='ltx-2.5',content_type='long_drama',
        script_provider=script_provider,script_model=ANTHROPIC_MODEL if script_provider=='anthropic' else 'qwen3:8b',source_mode='topic',topic='An unfolding studio mystery',
        duration_seconds=900,max_shots=180,max_cost_usd=25 if script_provider=='anthropic' else 0,cast_package_id=cast['package_id'])
    calls=[]
    def generate(prompt,configuration):
        calls.append(prompt)
        if len(calls)==2:raise PaidScriptError('Known rejected script request',uncertain=False)
        plan=long_production(184)
        plan.content_plan.scenes[-1].duration_seconds=4;plan.shots[-1].duration_seconds=4
        return plan
    provider=lambda model:SimpleNamespace(check_model=lambda:None,generate_production_plan=generate,
        last_usage={'input_tokens':1000,'output_tokens':2000},last_response_id='msg_revision_fixture')
    scripts=ScriptGenerationService(local_service.storage.db_path,anthropic_factory=provider,local_factory=provider)
    request=scripts.request(config)
    parent=scripts.submit(request,'admin',quote_id=scripts.quote(request,'admin')['quote_id'],confirmed=True)
    scripts.run(parent)
    assert scripts.get(parent)['status']=='failed' and len(calls)==2
    first=scripts.get(parent)['parts'][0]['response_id']
    quote=scripts.quote_chapter_revision(parent,2,'admin','Keep exact duration and cast.')
    assert quote['max_requests']==1 and len(calls)==2
    with pytest.raises(ValueError,match='confirmation'):
        scripts.submit_chapter_revision(parent,quote['quote_id'],'admin',False)
    with pytest.raises(ValueError,match='administrator'):
        scripts.submit_chapter_revision(parent,quote['quote_id'],'someone-else',True)
    revision=scripts.submit_chapter_revision(parent,quote['quote_id'],'admin',True)
    with pytest.raises(ValueError,match='already used'):
        scripts.submit_chapter_revision(parent,quote['quote_id'],'admin',True)
    with monkeypatch.context() as patch:
        if adoption_failure:
            patch.setattr(scripts,'adopt_chapter_revision',Mock(side_effect=RuntimeError('Temporary bookkeeping failure')))
        scripts.run(revision)
    assert scripts.get(revision)['status']=='completed' and len(calls)==3
    if adoption_failure:
        assert scripts.get(revision)['production_plan']
        assert scripts.get(parent)['parts'][1]['status']=='failed'
        with pytest.raises(ValueError,match='already saved'):
            scripts.quote_chapter_revision(parent,2,'admin','Another request must be blocked')
        with pytest.raises(ValueError,match='confirm'):
            scripts.apply_saved_chapter_revision(parent,revision,'admin',False)
        scripts.apply_saved_chapter_revision(parent,revision,'admin',True)
        assert len(calls)==3
    assert scripts.get(parent)['parts'][1]['status']=='completed'
    assert scripts.get(parent)['parts'][0]['response_id']==first
    scripts.resume_chapters(parent,'admin',True);scripts.run(parent)
    assert scripts.get(parent)['status']=='completed' and len(calls)==6
    with scripts.storage.connect() as conn:
        saved=conn.execute('SELECT prior_state_json FROM script_part_revisions WHERE revision_attempt_id=?',(revision,)).fetchone()
    assert json.loads(saved[0])['status']=='failed'


def test_comparison_sheet_uses_validated_media_and_does_not_approve(local_service,tmp_path):
    from app.services.cinematic_inspection import comparison_sheet
    service=local_service
    value=service.create(service.quote(service.script_id,'admin')['quote_id'],'admin',True)
    shot=value['shots'][0]
    path=service.root/value['production_id']/'fixture.mp4';path.parent.mkdir(parents=True,exist_ok=True)
    fixture_video(path,8)
    with service.storage.connect() as conn:
        conn.execute("UPDATE video_shots SET status='completed',clip_path=?,selected=1,media_quality_json=? WHERE attempt_id=?",
            (str(path),json.dumps({'sha256':media_sha256(path)}),shot['attempt_id']))
    image=comparison_sheet(service,value['production_id'],shot['attempt_id'])
    assert image.exists() and image.stat().st_mode & 0o777==0o600
    assert service.jobs.get_job(value['job_id'])['status']=='queued'


def test_thirty_minute_synthetic_assembly_and_scene_cache(tmp_path,monkeypatch):
    """Actual 1800s media at a small CPU test raster; not a neural quality claim."""
    from app.models.production_plan import ProductionPlan
    from app.services.cloud_video_renderer import CloudVideoRenderer
    from app.services.video_renderer import VideoRenderer
    import app.services.cloud_video_renderer as module
    data=long_production(120).model_dump(mode='json')
    data['content_plan']['scenes']=[];data['shots']=[];data['dialogue']=[]
    source=tmp_path/'source.mp4';fixture_video(source,10)
    selected={}
    for scene in range(1,31):
        actor='actor_a' if scene%2 else 'actor_b'
        narration='We can finish this together.'
        data['content_plan']['scenes'].append({'scene_number':scene,'duration_seconds':60,'narration':narration,
            'visual_prompt':'A moving cinematic test scene','on_screen_text':''})
        data['dialogue'].append({'scene_number':scene,'character_id':actor,'text':narration})
        for part in range(6):
            number=len(data['shots'])+1
            data['shots'].append({'shot_number':number,'scene_number':scene,'duration_seconds':10,
                'action':'A moving subject','camera':'Fixed camera','spoken_text':narration if part==0 else '',
                'character_ids':[actor]})
            selected[number]={'clip_path':str(source),'trim_start_seconds':0,'media_quality_json':json.dumps({'sha256':media_sha256(source)})}
    config=GenerationConfig(video_provider='local_cinematic',video_model='ltx-2.5',content_type='long_drama',
        duration_seconds=1800,max_shots=180,fps=24,narration='model_audio',resolution='720p')
    plan=ProductionPlan.model_validate(data).validate_configuration(config)
    renderer=CloudVideoRenderer(config)
    renderer.width,renderer.height=256,144  # Deliberately small synthetic CPU verification profile.
    renderer.local_renderer=VideoRenderer(256,144,24)
    _,quality=renderer.assemble(plan,selected,tmp_path/'assembly_one')
    assert abs(quality['duration_seconds']-1800)<.6 and quality['technical_passed']
    assert len(list((tmp_path/'scene_cache').glob('*.json')))==30
    calls=[];real=module.run
    def observed(command,**kwargs):
        calls.append(command)
        return real(command,**kwargs)
    monkeypatch.setattr(module,'run',observed)
    _,again=renderer.assemble(plan,selected,tmp_path/'assembly_two')
    assert again['technical_passed']
    assert not any(any('raw_' in str(argument) for argument in command) for command in calls)
