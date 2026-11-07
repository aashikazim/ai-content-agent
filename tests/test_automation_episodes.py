"""Fake-clock episode lifecycle; external, neural and upload requests forbidden."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4
import pytest
from app.automation.config import load_settings
from app.automation.series.episodes import EpisodeService
from app.automation.series.episode_planner import EpisodePlanner,ChapterDraft,hook_check,packaging
from app.automation.series.service import SeriesService,read_series
from app.services.cinematic_cast_service import CinematicCastService
from app.automation.shorts_service import ShortsService
from tests.test_generation_api import generation_api
from tests.test_automation_series import bible_data,episodes_data,fake_planner,no_real_calls

ROOT=Path(__file__).parents[1]
class Clock:
    def __init__(self):self.instant=datetime(2026,11,8,5,30,tzinfo=timezone.utc)
    def now(self):return self.instant


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('AUTOMATION_DATA_DIR',str(tmp_path/'automation'))
    clock=Clock();settings=load_settings(ROOT/'config/lanes.yaml',environ={'AUTOMATION_ENABLED':'true'})
    cast=Mock();package={'package_id':str(uuid4()),'sha256':'fixture','characters':bible_data()['characters']}
    for actor in package['characters']:actor.update(primary_reference_id=str(uuid4()),reference_ids=[],tts_voice='en-us')
    cast.get.return_value=package
    story=SeriesService(tmp_path/'existing.db',settings,clock=clock,planner=fake_planner(),cast_service=cast)
    initial=story.enqueue(str(uuid4()),confirmed=True);story.process(initial['series_id'])
    row=read_series(story.storage.db_path,initial['series_id'])
    story.approve_cast(row['series_id'],package['package_id'],row['updated_at'],confirmed=True)
    service=EpisodeService(story.storage.db_path,settings,clock=clock,cast=cast)
    return service,row['series_id'],clock


def test_episode_compiler_chapters_memory_and_final_hook(tmp_path,monkeypatch):
    service,identifier,_=setup(tmp_path,monkeypatch)
    row=service.enqueue(identifier,1,confirmed=True)
    config=__import__('app.models.generation_config',fromlist=['GenerationConfig']).GenerationConfig.model_validate(row['request']['configuration'])
    received=[]
    def request(stage,prompt,model,validate,**kwargs):
        count=kwargs['schema']['properties']['beats']['minItems']
        value=ChapterDraft(beats=[{'actor_id':'ada' if i%2==0 else 'ben','speech':'The old coastal map points toward a hidden tunnel under the tower.',
            'action':'A lighthouse keeper walks to the window and raises a brass lantern.','camera':'Slow tracking closeup.'} for i in range(count)],ending_state='Ada holds the map in the tower; Ben knows the tidal tunnel exists.')
        assert not validate(value);received.append((stage,prompt,count));return value
    # A240s private pilot must still use <=180s chapter calls.
    config=config.model_copy(update={'duration_seconds':240,'max_shots':24})
    adapter=EpisodePlanner(config,row['memory'],request)
    plan=adapter.generate_production_plan('ignored legacy resolved-film prompt',configuration=config)
    assert plan.content_plan.total_duration==240 and len(received)==2
    assert [n for _,_,n in received]==[12,12]
    ending=hook_check(plan,row['memory']['outline']['hook'],row['memory']['packaging']['end_card'])
    assert ending['start_seconds']==210 and ending['hook_present']
    assert 'next_outline' in received[0][1] and 'story_state' in received[0][1]
    plan.shots[-1].spoken_text='No ending here'
    with pytest.raises(ValueError,match='Final 30'):hook_check(plan,row['memory']['outline']['hook'],row['memory']['packaging']['end_card'])


def test_idempotency_cast_and_private_validation_boundaries(tmp_path,monkeypatch):
    service,identifier,_=setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='private'):service.enqueue(identifier,1,confirmed=True,validation_duration=240)
    row=service.enqueue(identifier,1,confirmed=True)
    assert row['run_id']==service.enqueue(identifier,1,confirmed=True)['run_id']
    with service.storage.connect() as c:
        assert c.execute('SELECT count(*) FROM automation_episode_runs').fetchone()[0]==1
        c.execute("UPDATE series SET status='awaiting_cast' WHERE series_id=?",(identifier,))
    with pytest.raises(ValueError,match='approved'):service.enqueue(identifier,2,confirmed=True)


def test_35_day_lifecycle_buffer_failure_skip_order_and_rollover(tmp_path,monkeypatch):
    service,identifier,clock=setup(tmp_path,monkeypatch);calls=[];published=[]
    # Faster measured fixture rate is an explicit fake, never a claimed GPU result.
    service.gpu.record('fake-measured','ltx-2.5','image',450,45)
    def fake_produce(run_id,row):
        n=row['ep_no'];calls.append(n)
        if n==10:return {'status':'failed','error':'Recorded fake renderer failure'}
        state={'knowledge':{'ada':['Actual fake scripted event '+str(n)]},'open_threads':[]}
        with service.storage.connect() as c:c.execute('UPDATE series_episodes SET summary_text=?,state_after_json=?,script_plan_id=? WHERE series_id=? AND ep_no=?',
            (f'Episode {n} actual fixture screenplay summary.',json.dumps(state),'script-'+run_id,identifier,n))
        return {'status':'awaiting_quality_review','job_id':'fake-'+run_id}
    service.producer=fake_produce
    for day in range(1,36):
        clock.instant=datetime(2026,11,8,5,30,tzinfo=timezone.utc)+timedelta(days=day-1)
        service.tick();service.tick()  # Every date deliberately repeated.
        while True:
            with service.storage.connect() as c:queued=c.execute("SELECT run_id FROM automation_episode_runs WHERE status='queued' ORDER BY ep_no LIMIT 1").fetchone()
            if not queued:break
            service.process(queued[0]);service.process(queued[0])
        if day==10:service.skip(identifier,10,'test-owner','Explicit fixture skip after a failed production night',True)
        if day<=30 and day!=10:
            with service.storage.connect() as c:ep=c.execute('SELECT * FROM series_episodes WHERE series_id=? AND ep_no=?',(identifier,day)).fetchone()
            if ep['job_id']:
                assert service.publishable(identifier,day)
                published.append((day,ep['job_id']))
                with service.storage.connect() as c:c.execute("UPDATE series_episodes SET status='published',actual_publish_at=? WHERE series_id=? AND ep_no=?",(service.storage.timestamp(),identifier,day))
        assert not service.publishable(identifier,min(30,day+2)) if day<28 else True
    final=read_series(service.storage.db_path,identifier)
    assert final['status']=='completed'
    assert len(calls)==30 and len(set(calls))==30
    assert len(published)==29 and len({job for _,job in published})==29
    with service.storage.connect() as c:
        assert c.execute('SELECT count(*) FROM automation_series_rollovers').fetchone()[0]==1
        successor=c.execute('SELECT next_series_id FROM automation_series_rollovers').fetchone()[0]
    with service.storage.connect() as c:
        assert c.execute('SELECT eligible_date FROM automation_series_rollovers').fetchone()[0]=='2026-12-08'
    assert successor and read_series(service.storage.db_path,successor)['status']=='planning'
    assert read_series(service.storage.db_path,successor)['cast_package_ids']==[]


def test_gpu_planner_never_shortens_an_episode(tmp_path,monkeypatch):
    service,_,_=setup(tmp_path,monkeypatch)
    service.settings.config.gpu.nightly_window.end='00:31'
    result=service.gpu.forecast(__import__('datetime').date(2026,11,8),[{'slot':1,'kind':'episode','category':'episode','duration_s':450,'model':'ltx-2.5','input_mode':'image'}])
    assert result['items'][0]['duration_s']==450
    assert result['items'][0].get('skipped') or not result['fits']


def test_connected_shots_share_scene_until_actor_cut(tmp_path,monkeypatch):
    from app.models.generation_config import GenerationConfig
    s,identifier,_=setup(tmp_path,monkeypatch);row=s.enqueue(identifier,1,confirmed=True)
    config=GenerationConfig.model_validate(row['request']['configuration']).model_copy(update={'duration_seconds':240,'max_shots':24})
    def generate(stage,prompt,model,validate,**kwargs):
        count=kwargs['schema']['properties']['beats']['minItems']
        value=ChapterDraft(beats=[{'actor_id':'ada','speech':'I can see the old tidal passage behind the broken tower wall.',
            'action':'Ada walks along the flooded passage with a moving lantern.','camera':'Continuous tracking shot.'} for _ in range(count)],ending_state='Ada holds the lantern and knows the old tidal passage is open.')
        assert not validate(value);return value
    plan=EpisodePlanner(config,row['memory'],generate).generate_production_plan('',configuration=config)
    assert [s.duration_seconds for s in plan.content_plan.scenes]==[60,60,60,60]
    assert sum(s.transition=='continue' for s in plan.shots)==20
    assert plan.shots[0].transition=='cut' and plan.shots[12].transition=='cut'


def test_memory_derived_from_actual_script_and_hook_tampering(tmp_path,monkeypatch):
    from app.automation.series.episode_planner import state_from_script,EpisodeState
    from tests.test_long_drama import long_production
    s,identifier,_=setup(tmp_path,monkeypatch);context=s.context(identifier,1)
    # An updater must see actual screenplay text, not a fabricated video summary.
    script=long_production()
    seen=[]
    def request(stage,prompt,model,validate,**kwargs):
        seen.append(prompt)
        value=EpisodeState(summary='The actual fixture screenplay shows the keepers locating a passage.',knowledge={'ada':['A passage exists'],'ben':['A passage exists']},locations={'ada':'tower','ben':'tower'},props={},threads_advanced=['bell'],threads_resolved=[])
        assert not validate(value);return value
    state=state_from_script(context,script,request)
    assert script.content_plan.scenes[0].narration in seen[0]
    assert state['script_sha256'] and state['open_threads']==['bell'] and state['wardrobe']['ada']


def test_unknown_outcome_never_replays_or_advances_episode(tmp_path,monkeypatch):
    s,identifier,_=setup(tmp_path,monkeypatch)
    s.producer=Mock(return_value={'status':'needs_reconciliation','error':'Fixture uncertain GPU result'})
    row=s.enqueue(identifier,1,confirmed=True);s.process(row['run_id']);s.process(row['run_id'])
    assert s.producer.call_count==1 and s.get(row['run_id'])['status']=='needs_reconciliation'
    assert not s.tick()['queued'] and not s.publishable(identifier,2)


def test_episode_budget_cannot_overreserve_or_shorten(tmp_path,monkeypatch):
    s,identifier,_=setup(tmp_path,monkeypatch)
    s.settings.config.gpu.nightly_window.end='00:31'
    with pytest.raises(ValueError,match='capacity'):s.enqueue(identifier,1,confirmed=True)
    with s.storage.connect() as c:assert c.execute('SELECT count(*) FROM automation_episode_runs').fetchone()[0]==0


def test_episode_routes_require_session_confirmation_and_do_not_publish(generation_api,monkeypatch):
    from tests.test_generation_api import sign_in
    api=generation_api
    monkeypatch.setenv('AUTOMATION_LANES_FILE',str(ROOT/'config/lanes.yaml'))
    monkeypatch.setenv('AUTOMATION_DATA_DIR',str(api.tmp_path/'automation'))
    cast=Mock();package={'package_id':str(uuid4()),'sha256':'fixture','characters':bible_data()['characters']}
    for a in package['characters']:a.update(primary_reference_id=str(uuid4()),reference_ids=[],tts_voice='en-us')
    cast.get.return_value=package
    s=SeriesService(api.db_path,load_settings(ROOT/'config/lanes.yaml',environ={}),planner=fake_planner(),cast_service=cast)
    row=s.enqueue(str(uuid4()),confirmed=True);s.process(row['series_id']);row=read_series(api.db_path,row['series_id'])
    s.approve_cast(row['series_id'],package['package_id'],row['updated_at'],confirmed=True)
    monkeypatch.setattr(CinematicCastService,'get',lambda self,*_:package)
    monkeypatch.setattr(ShortsService,'launch_drain',Mock())
    root='/api/v1/automation/series/'+row['series_id']
    assert api.client.get(root+'/episodes').status_code==401
    headers=sign_in(api);body={'confirmed':True}
    assert api.client.post(root+'/episodes/1/run',json=body).status_code==403
    assert api.client.post(root+'/episodes/1/run',json={},headers=headers).status_code==422
    response=api.client.post(root+'/episodes/1/run',json=body,headers=headers);assert response.status_code==202
    run=response.json()['run_id']
    assert api.client.get('/api/v1/automation/episode-runs/'+run).json()['status']=='queued'
    assert api.client.get(root+'/episodes').json()['episodes'][0]['production_status']=='queued'
    assert api.client.post(root+'/episodes/1/run',json=body,headers=headers).json()['run_id']==run
    assert api.client.post(root+'/episodes/1/run',json={'confirmed':True,'validation_duration':240},headers=headers).json()['run_id']==run
    api.publisher.assert_not_called();api.execute.assert_not_called()


def test_episode_actor_lifecycle_cannot_be_bypassed_by_a_script(tmp_path,monkeypatch):
    from app.models.generation_config import GenerationConfig
    s,identifier,_=setup(tmp_path,monkeypatch);row=s.enqueue(identifier,1,confirmed=True)
    config=GenerationConfig.model_validate(row['request']['configuration']).model_copy(update={'duration_seconds':120,'max_shots':12})
    context=row['memory'];context['outline']['ep_no']=4
    context['bible']['characters'][1]['removed_after']=3
    def generate(stage,prompt,model,validate,**kwargs):
        assert kwargs['schema']['$defs']['EpisodeBeat']['properties']['actor_id']['enum']==['ada']
        draft=ChapterDraft(beats=[{'actor_id':'ben','speech':'I will not return after my declared removal from this story.',
            'action':'Ben walks back into the lighthouse despite being absent.','camera':'A slowly tracking camera.'}]*12,ending_state='An invalid resurrection must be rejected before video submission.')
        assert any('resurrected' in error for error in validate(draft))
        raise ValueError('Known invalid fixture')
    with pytest.raises(ValueError,match='fixture'):EpisodePlanner(config,context,generate).generate_production_plan('',configuration=config)


def test_episode_existing_workflow_with_mocked_models_and_actual_ffmpeg(tmp_path,monkeypatch):
    import base64,io,shutil
    from PIL import Image
    from app.models.generation_config import GenerationConfig
    from app.services.cloud_video_service import CloudVideoService
    from app.services.cloud_video_renderer import validate_clip
    from app.services.job_service import JobService
    from tests.test_cloud_media import fixture_video
    for key,value in {'AUTOMATION_DATA_DIR':tmp_path,'CONTENT_PLANS_DIR':tmp_path/'plans','CLOUD_VIDEOS_DIR':tmp_path/'videos',
                      'CHARACTER_REFERENCES_DIR':tmp_path/'references','DRIVING_REFERENCES_DIR':tmp_path/'driving','LOCAL_CINEMATIC_ROOT':tmp_path/'runtime'}.items():monkeypatch.setenv(key,str(value))
    monkeypatch.setattr(GenerationConfig,'output_dimensions',property(lambda self:(320,180)))
    db=tmp_path/'validation'/'episode.db';db.parent.mkdir()
    videos=CloudVideoService(db);actors=[]
    for actor in bible_data()['characters']:
        buf=io.BytesIO();Image.new('RGB',(192,192),'navy').save(buf,format='PNG')
        ref=videos.references.create({**{k:actor[k] for k in ('character_id','name','description','wardrobe','voice_notes')},
            'rights_note':'Synthetic unit-test fixture, not a real actor','rights_confirmed':True,'image_base64':base64.b64encode(buf.getvalue()).decode()})
        actors.append({**{k:actor[k] for k in ('character_id','name','description','wardrobe','voice_notes')},'primary_reference_id':ref['reference_id'],'reference_ids':[ref['reference_id']]})
    package=videos.cast.create({'name':'Explicit fixture-approved cast','characters':actors,'confirmed':True})
    settings=load_settings(ROOT/'config/lanes.yaml',environ={}) # Disable automatic buffer follow-on.
    story=SeriesService(db,settings,planner=fake_planner());series=story.enqueue(str(uuid4()),confirmed=True);story.process(series['series_id'])
    row=read_series(db,series['series_id']);story.approve_cast(row['series_id'],package['package_id'],row['updated_at'],confirmed=True)
    source=tmp_path/'moving.mp4';fixture_video(source,10)
    class Provider:
        def check_model(self):return {'status':'fixture'}
        def submit(self,*args,**kwargs):return {'provider':'local_cinematic','interaction_id':str(uuid4()),'status':'completed','usage':{}}
        def retrieve(self,identifier):return {'provider':'local_cinematic','interaction_id':identifier,'status':'completed','usage':{}}
        def download(self,snapshot,destination):destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    provider=Provider();monkeypatch.setattr(CloudVideoService,'provider_for',lambda *args,**kwargs:provider)
    def fake_dispatch(self,identifier,attempt_ids=None):
        for _ in range(30):
            value=self.process(identifier,submit_queued=True)
            if value['status']=='technical_ready':return
            if value['status'] in {'failed','assembly_failed','needs_reconciliation'}:raise AssertionError(value.get('error') or value['status'])
        raise AssertionError('Bounded fixture coordinator did not finish')
    monkeypatch.setattr(CloudVideoService,'dispatch_local',fake_dispatch)
    monkeypatch.setattr('app.services.speech_analysis.analyze_audio',lambda *args,**kwargs:{'method':'fixture_asr','duration_seconds':10,'words':[{'word':'fixture','start':0,'end':1}],'human_review_required':True})
    human=Mock(side_effect=AssertionError('Never invent human quality confirmations'));monkeypatch.setattr(CloudVideoService,'send_to_review',human)
    def fake_request(prompt,schema,max_tokens=9000):
        if 'beats' in schema.get('properties',{}):
            count=schema['properties']['beats']['minItems']
            return {'beats':[{'actor_id':'ada' if i%2==0 else 'ben','speech':'The old map reveals a hidden coastal path beneath the tower.',
                'action':'The keeper walks along a stone wall and lifts a brass lantern.','camera':'A slow moving tracking camera.'} for i in range(count)],'ending_state':'Ada holds the map in the tower while Ben sees the coastal path.'}
        return {'summary':'The actual scripted keepers locate a coastal path using the recovered map.','knowledge':{'ada':['A coastal path exists'],'ben':['A coastal path exists']},'locations':{'ada':'tower','ben':'tower'},'props':{'map':'ada'},'threads_advanced':['bell'],'threads_resolved':[]}
    planner=Mock();planner.request.side_effect=fake_request
    def no_wait(_):raise AssertionError('Mocked coordinator must complete synchronously')
    service=EpisodeService(db,settings,planner=planner,sleep=no_wait)
    run=service.enqueue(series['series_id'],1,confirmed=True,validation_duration=240);service.process(run['run_id'])
    result=service.get(run['run_id']);assert result['status']=='awaiting_quality_review',result.get('error')
    job=JobService(db).get_job(result['job_id']);assert job['status']=='awaiting_quality_review'
    assert validate_clip(Path(job['video_path']),240,require_audio=True)['duration_seconds']>=239.9
    with service.storage.connect() as c:
        saved=c.execute('SELECT summary_text,state_after_json FROM series_episodes WHERE series_id=? AND ep_no=1',(series['series_id'],)).fetchone()
        assert saved['summary_text'] and json.loads(saved['state_after_json'])['script_sha256']
        assert c.execute('SELECT count(*) FROM automation_episode_receipts').fetchone()[0]==3
        assert c.execute('SELECT count(*) FROM automation_gpu_measurements WHERE is_seed=0').fetchone()[0]==0
    production=videos.storage.get(result['production_id'])
    for shot in production['production_plan']['shots']:
        image_id=production['configuration']['scene_image_reference_ids'][str(shot['scene_number'])]
        assert videos.references.get(image_id)['character_id']==shot['character_ids'][0]
    with service.storage.connect() as c:
        assert json.loads(c.execute('SELECT story_state_json FROM series WHERE series_id=?',(series['series_id'],)).fetchone()[0])['state_episode']==1
    human.assert_not_called()


def test_reference_geometry_preserves_entire_approved_image():
    from PIL import Image
    from app.automation.series.reference_frames import fit_reference
    # A face marker near the top of a portrait must survive landscape preparation.
    source=Image.new('RGB',(200,400),'navy')
    for x in range(40,160):
        for y in range(15,90):source.putpixel((x,y),(255,0,0))
    output=fit_reference(source,(320,180))
    assert output.size==(320,180)
    assert output.getpixel((160,20))[0]>240
    assert source.size==(200,400) and source.getpixel((100,20))==(255,0,0)


def test_concurrent_episode_reservation_is_idempotent_under_capacity_limit(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    s,identifier,_=setup(tmp_path,monkeypatch);s.settings.config.gpu.nightly_window.end='04:30'
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(lambda _:s.enqueue(identifier,1,confirmed=True),range(8)))
    assert len({r['run_id'] for r in rows})==1
    with s.storage.connect() as c:
        assert c.execute('SELECT count(*) FROM automation_episode_runs').fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM app_work_queue WHERE target_id LIKE 'episode:%'").fetchone()[0]==1


def test_skipping_scripted_episode_preserves_canonical_memory(tmp_path,monkeypatch):
    s,identifier,_=setup(tmp_path,monkeypatch);state={'knowledge':{'ada':['The chart is real']},'script_sha256':'known-script'}
    with s.storage.connect() as c:c.execute('UPDATE series_episodes SET summary_text=?,state_after_json=? WHERE series_id=? AND ep_no=1',('Actual screenplay summary, retained for the next recap.',json.dumps(state),identifier))
    s.skip(identifier,1,'owner','Skip this public slot but preserve the already-written canon',True)
    with s.storage.connect() as c:row=c.execute('SELECT * FROM series_episodes WHERE series_id=? AND ep_no=1',(identifier,)).fetchone()
    assert row['status']=='skipped' and json.loads(row['state_after_json'])==state
    assert s.context(identifier,2)['story_state']==state


def test_skipped_payoff_is_caught_up_in_next_actual_script_state(tmp_path,monkeypatch):
    from app.automation.series.episode_planner import state_from_script,EpisodeState
    from tests.test_long_drama import long_production
    s,identifier,_=setup(tmp_path,monkeypatch)
    with s.storage.connect() as c:c.execute('UPDATE series_episodes SET summary_text=?,state_after_json=? WHERE series_id=? AND ep_no=11',('The keepers still need to answer the bell mystery.',json.dumps({'open_threads':['bell','ledger']}),identifier))
    s.skip(identifier,12,'owner','A failed night requires skipping this unproduced public episode',True)
    context=s.context(identifier,13);assert context['story_state']['deferred_payoffs']==['bell']
    def request(stage,prompt,model,validate,**kwargs):
        result=EpisodeState(summary='The keeper answers the delayed bell question in this actual fixture screenplay.',knowledge={'ada':['The bell answer is known'],'ben':['The bell answer is known']},locations={'ada':'tower','ben':'tower'},props={},threads_advanced=[],threads_resolved=['bell'])
        assert not validate(result);return result
    state=state_from_script(context,long_production(),request)
    assert state['open_threads']==['ledger'] and state['deferred_payoffs']==[]


@pytest.mark.parametrize('number',[True,0,31,'1'])
def test_invalid_episode_number_cannot_leave_an_orphan_intent(tmp_path,monkeypatch,number):
    s,identifier,_=setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='integer'):s.enqueue(identifier,number,confirmed=True)
    with s.storage.connect() as c:
        assert c.execute('SELECT count(*) FROM automation_episode_runs').fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM app_work_queue WHERE target_id LIKE 'episode:%'").fetchone()[0]==0
