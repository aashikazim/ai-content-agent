"""Recorded/private fixtures only: never call a platform, model, or scheduler."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from app.automation.authorizations import StandingAuthorizations
from app.automation.config import load_settings
from app.automation.events import EventLog
from app.automation.gates import GateEvaluator
from app.automation.governor import Governor
from app.automation.idempotency import canonical_json
from app.automation.runtime import register_jobs,window_remaining,DirectorRuntime
from app.services.cloud_video_renderer import media_sha256
from app.services.cloud_video_service import CloudVideoService
from app.services.job_service import JobService
from tests.test_gemini_video import cloud_production
from tests.test_generation_api import generation_api,sign_in

ROOT=Path(__file__).parents[1]
class Clock:
    def __init__(self):self.instant=datetime(2026,11,8,14,tzinfo=timezone.utc)
    def now(self):return self.instant

@pytest.fixture(autouse=True)
def forbid_network_and_inference(monkeypatch):
    forbidden=Mock(side_effect=AssertionError('No external call in Governor tests'))
    monkeypatch.setattr('requests.sessions.Session.send',forbidden)
    monkeypatch.setattr('ollama.Client.chat',forbidden)
    monkeypatch.setattr('app.services.youtube_publisher.YouTubePublisher.upload',forbidden)
    monkeypatch.setattr('app.services.facebook_publisher.FacebookPublisher.upload',forbidden)
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.submit',forbidden)


def setup(tmp_path,monkeypatch,*,live=False):
    for name,suffix in [('AUTOMATION_DATA_DIR','automation'),('CONTENT_PLANS_DIR','plans'),('CLOUD_VIDEOS_DIR','videos'),('CHARACTER_REFERENCES_DIR','refs'),('DRIVING_REFERENCES_DIR','driving')]:monkeypatch.setenv(name,str(tmp_path/suffix))
    monkeypatch.setenv('YOUTUBE_PRIVACY_STATUS','private')
    settings=load_settings(ROOT/'config/lanes.yaml',environ={'AUTOMATION_ENABLED':'true','AUTOMATION_GOVERNOR_ENABLED':'true','AUTOMATION_GOVERNOR_DRY_RUN':'false' if live else 'true'})
    settings.kill_switch_file=tmp_path/'KILL';clock=Clock()
    gov=Governor(tmp_path/'existing.db',settings,clock=clock)
    gov.set_lane('shorts_daily','auto_with_gates',confirmed=True)
    return gov,clock


def add_job(gov,tmp_path,*,slot=1,title=None,kind='topic_shorts',validation=False):
    production=cloud_production();production.content_plan.title=title or 'Original lunar clock '+str(uuid4())
    plan=production.content_plan;identifier=str(uuid4());pid=str(uuid4());directory=tmp_path/identifier;directory.mkdir()
    output=directory/'final_video.mp4';output.write_bytes(b'private moving fixture '+identifier.encode())
    digest=media_sha256(output);quality=dict(technical_passed=True,sha256=digest,duration_seconds=45,width=720,height=1280,has_audio=True,motion_samples_distinct=20)
    config={'video_provider':'local_cinematic','video_model':'ltx-2.5','source_mode':'topic','topic':'Moon clock', 'duration_seconds':45,'captions':'asr','fps':24,'aspect_ratio':'9:16','resolution':'720p'}
    plan_id,_=gov.productions.content_storage.save_plan(plan)
    jid=gov.jobs.create_job();gov.jobs.update_job(jid,'awaiting_quality_review',plan_id=plan_id,video_path=str(output))
    now=gov.storage.timestamp()
    with gov.productions.storage.connect() as c:
        c.execute('INSERT INTO video_productions VALUES (?,?,?,?,?,?,?,?,?,?)',(pid,jid,'script-fixture',json.dumps(config),production.model_dump_json(),'technical_ready',json.dumps(quality),None,1,1))
        for shot in production.shots:
            clip=directory/f'{shot.shot_number}.mp4';clip.write_bytes(b'fixture clip')
            q=json.dumps({'sha256':media_sha256(clip)})
            c.execute('''INSERT INTO video_shots(attempt_id,production_id,shot_number,scene_number,version,operation,request_json,quote_id,status,clip_path,selected,media_quality_json,estimate_usd,created_at,updated_at)
                VALUES (?,?,?,?,1,'generate','{}','fixture','completed',?,1,?,'0',1,1)''',(str(uuid4()),pid,shot.shot_number,shot.scene_number,str(clip),q))
    with gov.storage.connect() as c:
        c.execute('''INSERT INTO daily_plans(daily_plan_id,plan_date,lane,slot,job_id,recipe,status,planned_publish_at,created_at,updated_at)
            VALUES (?,?,?, ?,?,'{}','in_review',?,?,?)''',(identifier,gov.storage.clock.now().date().isoformat(),'shorts_daily',slot,jid,now,now,now))
    scenes=[]
    for scene in plan.scenes:
        scenes.append(dict(scene_number=scene.scene_number,duration=scene.duration_seconds,speech_quality={'word_error_rate':.01,'words':[{'start':0,'end':1,'word':'fixture'}]},captions=[[0,1,'fixture']]))
        (directory/f'captions_{scene.scene_number}.ass').write_text('fixture captions')
    (directory/'manifest.json').write_text(json.dumps({'scenes':scenes}))
    gov.gates.media_validator=lambda *a,**kw:dict(quality)
    return dict(job_id=jid,production_id=pid,output=output,plan=plan,quality=quality,directory=directory)


def test_seven_day_dry_run_real_publish_validation_no_mutations_or_duplicates(tmp_path,monkeypatch):
    gov,clock=setup(tmp_path,monkeypatch);calls=[];decisions=[]
    original=gov.publisher_factory
    def factory(identifier,dry):
        service=original(identifier,dry);publish=service.publish
        def call(*args,**kwargs):calls.append((args[0],args[1],kwargs['dry_run']));return publish(*args,**kwargs)
        service.publish=call;return service
    gov.publisher_factory=factory
    for day in range(7):
        clock.instant=datetime(2026,11,8,14,tzinfo=timezone.utc)+timedelta(days=day)
        item=add_job(gov,tmp_path,slot=1)
        # Each fixture has its own media validator, so evaluate only today's job.
        gov.candidates=lambda i=item:[dict(job_id=i['job_id'],lane='shorts_daily',kind='topic_shorts',planned_at=clock.now().isoformat(),validation_only=False)]
        decisions.extend(gov.tick()['decisions']);gov.tick();gov.tick()
        assert gov.jobs.get_job(item['job_id'])['status']=='awaiting_quality_review'
    assert len(calls)==14 and len(set(calls))==14 and all(c[2] for c in calls)
    with gov.storage.connect() as c:
        assert c.execute('SELECT count(*) FROM publications').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM automation_governor_actions').fetchone()[0]==14
    assert all(d['status']=='dry_run' for d in decisions)
    (tmp_path/'seven-day-decisions.json').write_text(json.dumps({'simulated_days':7,'decisions':decisions,'publish_validation_calls':calls,'duplicate_calls':len(calls)-len(set(calls)),'actual_upload_calls':0,'publication_rows':0,'real_approvals':0},indent=2))
    assert len([e for e in EventLog(gov.storage).list(limit=1000) if e.action=='governor.publish_decision'])==14


def test_manual_default_and_kill_switches(tmp_path,monkeypatch):
    gov,_=setup(tmp_path,monkeypatch);add_job(gov,tmp_path)
    gov.set_lane('shorts_daily','manual',confirmed=True)
    assert gov.tick()['decisions'][0]['reasons']==['Lane remains manual']
    gov.set_state('global',kill_switch=True,confirmed=True)
    assert gov.tick()['status']=='killed'
    gov.set_state('global',kill_switch=False,confirmed=True)
    gov.settings.kill_switch_file.touch();assert gov.tick()['status']=='killed'
    with pytest.raises(ValueError):gov.set_lane('shorts_daily','auto_with_gates')


def test_publish_slot_late_approval_waits_until_next_day(tmp_path,monkeypatch):
    gov,clock=setup(tmp_path,monkeypatch);item={'planned_at':clock.now().isoformat()}
    assert gov.due_slot(item)
    clock.instant+=timedelta(minutes=16);assert gov.due_slot(item) is None
    clock.instant+=timedelta(days=1,minutes=-16);assert gov.due_slot(item)


def test_live_path_uses_existing_review_with_mocked_publishers_only(tmp_path,monkeypatch):
    gov,_=setup(tmp_path,monkeypatch,live=True);item=add_job(gov,tmp_path)
    calls=[]
    def factory(identifier,dry):
        assert not dry and gov.jobs.get_job(identifier)['status']=='approved'
        def publish(job_id,platform,dry_run):
            assert not dry_run;calls.append((job_id,platform))
            publication=gov.publications.reserve_publication(job_id,platform)
            gov.publications.update(publication,'published',platform_video_id='mocked-'+platform)
            return {'publication_id':publication,'status':'published'}
        return SimpleNamespace(publish=publish)
    gov.publisher_factory=factory
    result=gov.tick();gov.tick()
    assert len(calls)==2 and all(r['status']=='completed' for r in result['decisions'])
    assert gov.jobs.get_job(item['job_id'])['status']=='approved'
    assert all(s['continuity_status']=='pending' for s in gov.productions.storage.get(item['production_id'])['shots'])
    events=gov.productions.storage.get(item['production_id'])['events']
    assert any(e['event']=='governor_quality_gated' for e in events)
    assert not any(e['event']=='quality_reviewed' for e in events)


def test_uncertain_action_never_replays_and_platform_auto_trips(tmp_path,monkeypatch):
    gov,_=setup(tmp_path,monkeypatch,live=True);add_job(gov,tmp_path)
    publisher=Mock();publisher.publish.side_effect=TimeoutError('secret must not enter event log')
    gov.publisher_factory=Mock(return_value=publisher)
    gov.tick();gov.tick()
    assert publisher.publish.call_count==2 # One per distinct platform, no retry.
    assert all(p['paused'] for p in gov.status()['platforms'] if p['scope']!='global')
    assert 'secret' not in canonical_json(gov.status())


def test_gate_holds_wer_captions_keywords_validation_and_changed_media(tmp_path,monkeypatch):
    gov,_=setup(tmp_path,monkeypatch);item=add_job(gov,tmp_path)
    from app.automation.config import HoldRule
    gov.settings.config.governor.hold_rules=[HoldRule(kind='keyword',expression='lunar')]
    manifest=json.loads((item['directory']/'manifest.json').read_text());manifest['scenes'][0]['speech_quality']['word_error_rate']=.9;manifest['scenes'][0]['captions']=[[2,1,'backwards']]
    (item['directory']/'manifest.json').write_text(json.dumps(manifest))
    candidate=gov.candidates()[0];candidate['validation_only']=True
    reasons,_=gov.gates.evaluate(candidate,gov.jobs.get_job(item['job_id']),gov.productions.storage.get(item['production_id']))
    assert {'Speech WER gate failed','Caption timing is invalid','Owner keyword hold'}<=set(reasons)
    assert any('validation' in r for r in reasons)
    item['output'].write_bytes(b'changed');assert gov.tick()['decisions'][0]['status']=='held' # Existing WER/keyword holds block before any upload.


def test_auto_trip_three_known_failures_and_reconciliation_observation(tmp_path,monkeypatch):
    gov,_=setup(tmp_path,monkeypatch)
    for _ in range(2):gov.trip('youtube','Known failed request')
    assert not next(p for p in gov.status()['platforms'] if p['scope']=='youtube')['paused']
    gov.trip('youtube','Known failed request')
    assert next(p for p in gov.status()['platforms'] if p['scope']=='youtube')['paused']
    publication=gov.publications.reserve_publication('manual-job','facebook');gov.publications.update(publication,'needs_reconciliation')
    gov.observe_publications()
    with pytest.raises(ValueError,match='Reconcile'):gov.set_state('facebook',paused=False,confirmed=True)


def test_atomic_caps_and_reservations_no_duplicate_or_over_cap(tmp_path,monkeypatch):
    gov,clock=setup(tmp_path,monkeypatch);gov.settings.config.governor.daily_publish_cap['youtube']=1
    item=dict(job_id='job-a',lane='shorts_daily')
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:gov.reserve(item,'youtube',clock.now(),'hash'),range(5)))
    assert len([r for r in results if r])==1
    assert gov.reserve(dict(item,job_id='job-b'),'youtube',clock.now(),'hash') is None


def test_scheduler_only_registers_in_existing_process_and_window_guard(tmp_path,monkeypatch):
    gov,clock=setup(tmp_path,monkeypatch);scheduler=Mock()
    ids=register_jobs(scheduler,gov.settings)
    assert len(ids)==5 and len(set(ids))==5 and not scheduler.start.called
    assert all(c.kwargs['max_instances']==1 for c in scheduler.add_job.call_args_list)
    assert window_remaining(gov.settings,clock)==0
    assert DirectorRuntime(gov.storage.db_path,gov.settings,clock=clock).tick(launch=False)['status']=='outside_gpu_window'
    clock.instant=datetime(2026,11,8,6,tzinfo=timezone.utc)
    assert window_remaining(gov.settings,clock)>0


def test_standing_authorization_quote_caps_expiry_and_no_replay(tmp_path,monkeypatch):
    gov,clock=setup(tmp_path,monkeypatch);gov.settings.daily_budget_usd=__import__('decimal').Decimal('2')
    auth=StandingAuthorizations(gov.storage,gov.settings)
    identifier=auth.create(['openai'],'1','2',(clock.now()+timedelta(days=2)).isoformat(),actor='owner',confirmed=True)
    actor='automation:standing:'+identifier
    with gov.storage.connect() as c:
        c.execute('CREATE TABLE script_generation_quotes(quote_id TEXT PRIMARY KEY,actor TEXT,maximum_cost_usd TEXT,consumed_at INTEGER,expires_at INTEGER)')
        for q in ('a','b'):c.execute('INSERT INTO script_generation_quotes VALUES (?,?,?,NULL,?)',(q,actor,'.75',int(clock.now().timestamp()+600)))
    service=Mock();service.submit.return_value='saved-script'
    request={'configuration':{'script_provider':'openai'}}
    assert auth.submit_script(identifier,'a',request,service)=='saved-script'
    service.submit.assert_called_once_with(request,actor,quote_id='a',confirmed=True)
    with pytest.raises(ValueError,match='already reserved'):auth.submit_script(identifier,'a',request,service)
    with pytest.raises(ValueError,match='budget'):auth.submit_script(identifier,'b',request,service)
    clock.instant+=timedelta(days=3)
    with pytest.raises(ValueError,match='expired'):auth.submit_script(identifier,'b',request,service)


def test_governor_api_requires_admin_and_confirmation(generation_api,monkeypatch):
    api=generation_api;monkeypatch.setenv('AUTOMATION_DATA_DIR',str(api.tmp_path/'automation'));monkeypatch.setenv('AUTOMATION_LANES_FILE',str(ROOT/'config/lanes.yaml'))
    assert api.client.get('/api/v1/automation/governor').status_code==401
    headers=sign_in(api)
    assert api.client.get('/api/v1/automation/governor').json()['dry_run'] is True
    route='/api/v1/automation/lanes/shorts_daily/approval'
    assert api.client.put(route,json={'approval_mode':'auto_with_gates'},headers=headers).status_code==422
    assert api.client.put(route,json={'approval_mode':'auto_with_gates','confirmed':True},headers=headers).status_code==200
    assert api.client.post('/api/v1/automation/governor/state',json={'scope':'global','kill_switch':True,'confirmed':True},headers=headers).status_code==200


def test_episode_order_platform_mapping_and_private_validation_hold(tmp_path,monkeypatch):
    gov,clock=setup(tmp_path,monkeypatch);item=add_job(gov,tmp_path)
    with gov.storage.connect() as c:
        c.execute("INSERT INTO series(series_id,lane,title,genre,premise,created_at,updated_at) VALUES ('story','story_series','Title','drama','Premise',?,?)",(gov.storage.timestamp(),gov.storage.timestamp()))
        for n in (1,2):c.execute("INSERT INTO series_episodes(episode_id,series_id,ep_no,outline_json,created_at,updated_at) VALUES (?,'story',?,'{}',?,?)",(str(n),n,gov.storage.timestamp(),gov.storage.timestamp()))
    candidate=dict(job_id=item['job_id'],lane='story_series',kind='serialized_story',series_id='story',ep_no=2,planned_at=clock.now().isoformat(),validation_only=True)
    holds=gov.operational_holds(candidate,'youtube',gov.jobs.get_job(item['job_id']),item['plan'].model_dump())
    assert any('Earlier episode' in h for h in holds)
    with gov.storage.connect() as c:c.execute("UPDATE series_episodes SET status='skipped' WHERE ep_no=1")
    assert not gov.operational_holds(candidate,'youtube',gov.jobs.get_job(item['job_id']),item['plan'].model_dump())
    gov.set_lane('story_series','auto_with_gates',confirmed=True);gov.candidates=lambda:[candidate]
    assert [d['platform'] for d in gov.tick()['decisions']]==['youtube']
    assert any('validation' in r for r in gov.tick()['decisions'][0]['reasons'])


def test_no_automatic_retry_even_after_manual_retry_authorization(tmp_path,monkeypatch):
    gov,_=setup(tmp_path,monkeypatch);item=add_job(gov,tmp_path)
    identifier=gov.publications.reserve_publication(item['job_id'],'facebook');gov.publications.update(identifier,'retry_authorized')
    decisions=gov.tick()['decisions']
    assert next(d for d in decisions if d['platform']=='facebook')['status']=='held'
    assert gov.publications.get_by_job(item['job_id'],'facebook')['status']=='retry_authorized'


def test_uncertain_standing_spend_retains_cap_and_cannot_retry(tmp_path,monkeypatch):
    gov,clock=setup(tmp_path,monkeypatch);gov.settings.daily_budget_usd=__import__('decimal').Decimal('5')
    auth=StandingAuthorizations(gov.storage,gov.settings)
    identifier=auth.create(['openai'],'2','5',(clock.now()+timedelta(days=1)).isoformat(),actor='owner',confirmed=True)
    actor='automation:standing:'+identifier
    with gov.storage.connect() as c:
        c.execute('CREATE TABLE script_generation_quotes(quote_id TEXT PRIMARY KEY,actor TEXT,maximum_cost_usd TEXT,consumed_at INTEGER,expires_at INTEGER)')
        c.execute("INSERT INTO script_generation_quotes VALUES ('unknown',?,'1',NULL,?)",(actor,int(clock.now().timestamp()+600)))
    service=Mock();service.submit.side_effect=TimeoutError()
    request={'configuration':{'script_provider':'openai'}}
    with pytest.raises(TimeoutError):auth.submit_script(identifier,'unknown',request,service)
    with pytest.raises(ValueError,match='already reserved'):auth.submit_script(identifier,'unknown',request,service)
    assert service.submit.call_count==1 and auth.list()[0]['consumed_total_usd']=='1'
    with gov.storage.connect() as c:assert c.execute('SELECT status FROM automation_spend_reservations').fetchone()[0]=='needs_reconciliation'


def test_analytics_weights_use_latest_snapshots_and_preserve_quota_selection(tmp_path,monkeypatch):
    from app.automation.feedback import category_weights
    from app.services.analytics_storage import AnalyticsStorage
    gov,_=setup(tmp_path,monkeypatch);analytics=AnalyticsStorage(gov.storage.db_path)
    with gov.storage.connect() as c:
        for i in range(6):
            j='analytics-job-'+str(i);p='publication-'+str(i);category='comedy' if i<3 else 'drama'
            c.execute("INSERT INTO daily_plans(daily_plan_id,plan_date,lane,slot,job_id,recipe,created_at,updated_at) VALUES (?,?,'fixture',?,?,?, ?,?)",(j,'2026-11-08',i+1,j,json.dumps({'topic':{'category':category}}),gov.storage.timestamp(),gov.storage.timestamp()))
            c.execute("INSERT INTO publications(publication_id,job_id,platform,status,created_at,updated_at) VALUES (?,?,'youtube','published',?,?)",(p,j,gov.storage.timestamp(),gov.storage.timestamp()))
    for i in range(6):analytics.save_snapshot('publication-'+str(i),'youtube','video-'+str(i),dict(views=10000 if i<3 else 10,likes=300 if i<3 else 0,comments=50 if i<3 else 0))
    weights=category_weights(gov.storage.db_path)
    assert .5<=weights['drama']<weights['comedy']<=2


def test_daily_report_records_holds_modes_and_measured_only_times(tmp_path,monkeypatch):
    from app.automation.daily_report import write_daily_report
    gov,clock=setup(tmp_path,monkeypatch);add_job(gov,tmp_path);gov.tick()
    report=write_daily_report(gov.storage.db_path,clock.now().date())
    assert 'dry_run' in report and 'Unmeasured jobs have no invented GPU time' in report
    assert (tmp_path/'automation/reports/2026-11-08.md').read_text()==report


def test_changed_final_bytes_are_not_uploaded_even_after_a_cached_technical_result(tmp_path,monkeypatch):
    gov,_=setup(tmp_path,monkeypatch,live=True);item=add_job(gov,tmp_path)
    item['output'].write_bytes(b'altered after technical checks')
    publisher=Mock();gov.publisher_factory=Mock(return_value=publisher)
    assert all(d['status']=='needs_reconciliation' for d in gov.tick()['decisions'])
    assert not publisher.publish.called and gov.jobs.get_job(item['job_id'])['status']=='awaiting_quality_review'


def test_owner_closure_never_reopens_an_automatic_upload_claim(tmp_path,monkeypatch):
    gov,clock=setup(tmp_path,monkeypatch,live=True)
    item=dict(job_id='job-without-external-submission',lane='shorts_daily')
    identifier=gov.reserve(item,'youtube',clock.now(),'hash')
    gov.close_action(identifier,notes='Owner inspected: no publication was reserved.',actor='owner',confirmed=True)
    assert gov.reserve(item,'youtube',clock.now(),'hash') is None
    assert gov.status()['actions'][0]['status']=='owner_closed'


def test_live_path_calls_real_publishing_service_with_mocked_upload_objects(tmp_path,monkeypatch):
    gov,_=setup(tmp_path,monkeypatch,live=True);item=add_job(gov,tmp_path)
    youtube=Mock();youtube.upload.return_value={'video_id':'mock-youtube','status':'uploaded'}
    facebook=Mock();facebook.upload.return_value={'video_id':'mock-facebook','status':'submitted'}
    def factory(identifier,dry):
        publisher=gov._publisher(identifier,dry);publisher.youtube=youtube;publisher.facebook=facebook;return publisher
    gov.publisher_factory=factory
    result=gov.tick();gov.tick()
    assert all(d['status']=='completed' for d in result['decisions'])
    assert youtube.upload.call_count==facebook.upload.call_count==1
    assert youtube.upload.call_args.kwargs['privacy']=='private'
    with gov.storage.connect() as c:
        assert c.execute('SELECT count(*) FROM publications').fetchone()[0]==2
        assert {r[0] for r in c.execute('SELECT status FROM publications')}=={'uploaded','submitted'}
    assert gov.jobs.get_job(item['job_id'])['status']=='approved' # Mocked fixture only.



def test_governor_api_and_scheduler_ticks_cannot_race_shared_approval(tmp_path,monkeypatch):
    import fcntl
    gov,_=setup(tmp_path,monkeypatch)
    with Path(gov.storage.db_path).with_suffix('.governor.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert gov.tick()=={'status':'busy','decisions':[]}
    assert gov.tick()['status']=='dry_run'


def test_director_nightly_runtime_reuses_day_and_durable_queue(tmp_path,monkeypatch):
    from tests.test_automation_shorts import setup as shorts_setup
    shorts=shorts_setup(tmp_path,monkeypatch)
    clock=Clock();clock.instant=datetime(2026,11,8,6,tzinfo=timezone.utc)
    director=DirectorRuntime(shorts.storage.db_path,shorts.settings,clock=clock,shorts=shorts)
    first=director.tick(launch=False);second=director.tick(launch=False)
    assert first['lanes']['shorts_daily']['status']=='queued'
    assert second['lanes']['shorts_daily']['status']=='existing'
    with director.storage.connect() as c:
        assert c.execute('SELECT count(*) FROM automation_day_runs').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM daily_plans').fetchone()[0]==5
        assert c.execute("SELECT count(*) FROM app_work_queue WHERE kind='automation'").fetchone()[0]==1
