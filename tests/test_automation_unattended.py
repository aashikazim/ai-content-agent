"""Opt-in unattended mode: overlay, paid-script gating, auto cast, observer."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4
import pytest
import yaml
from PIL import Image
from app.automation.authorizations import StandingAuthorizations
from app.automation.config import load_settings
from app.automation.lanes.shorts import recipe_configuration
from app.automation.publication_observer import PublicationObserver,facebook_status
from app.automation.series.auto_cast import AutoCastService
from app.automation.series.service import SeriesService,read_series
from app.automation.shorts_service import ShortsService
from app.automation.unattended import publish_slots,scaled_quotas
from app.services.facebook_video_publisher import FacebookVideoPublisher
from app.services.publishing_storage import PublishingStorage
from tests.test_automation_series import fake_planner,no_real_calls

ROOT=Path(__file__).parents[1]


class Clock:
    def __init__(self):self.instant=datetime(2026,11,8,6,30,tzinfo=timezone.utc)
    def now(self):return self.instant


def settings(tmp_path,monkeypatch,**overrides):
    monkeypatch.setenv('AUTOMATION_DATA_DIR',str(tmp_path/'automation'))
    monkeypatch.setenv('CHARACTER_REFERENCES_DIR',str(tmp_path/'references'))
    path=tmp_path/'unattended.yaml';path.write_text(yaml.safe_dump({'enabled':True,**overrides}))
    # Never read the host's real kill-switch file from a test.
    return load_settings(ROOT/'config/lanes.yaml',environ={'AUTOMATION_ENABLED':'true','AUTOMATION_UNATTENDED_FILE':str(path),
        'AUTOMATION_KILL_SWITCH_FILE':str(tmp_path/'KILL')})


def test_shipped_file_is_opt_in_and_overlay_scales_lanes(tmp_path,monkeypatch):
    off=tmp_path/'off.yaml';off.write_text('enabled: false\nshorts: {per_day: 9}\n')
    assert load_settings(ROOT/'config/lanes.yaml',environ={}).unattended is None  # No variable, no overlay.
    shipped=load_settings(ROOT/'config/lanes.yaml',environ={'AUTOMATION_UNATTENDED_FILE':str(off)})
    assert shipped.unattended is None and shipped.config.lanes['shorts_daily'].daily_count==5
    assert shipped.config.lanes['story_series'].platforms==['youtube']
    assert load_settings(ROOT/'config/lanes.yaml',environ={'AUTOMATION_UNATTENDED_FILE':str(tmp_path/'missing.yaml')}).unattended is None
    value=settings(tmp_path,monkeypatch,scripts={'provider':'claude'},shorts={'per_day':8})
    shorts,story=value.config.lanes['shorts_daily'],value.config.lanes['story_series']
    assert shorts.daily_count==8==sum(shorts.quotas.values())==len(set(shorts.publish_slots))
    assert shorts.publish_slots[0]=='09:00' and shorts.publish_slots[-1]=='21:00' and shorts.approval_mode=='auto_with_gates'
    assert all(r.script_provider=='anthropic' for r in shorts.recipes.values())
    assert story.platforms==['youtube','facebook'] and story.series_start_mode=='auto' and story.approval_mode=='auto_with_gates'
    assert value.config.governor.daily_publish_cap=={'youtube':10,'facebook':9}
    # Enabling the file alone never enables the Governor or live mode.
    assert not value.governor_enabled and value.governor_dry_run and value.daily_budget_usd==0
    assert scaled_quotas({'comedy':2,'politics':1,'drama':1,'wildcard':1},10)=={'comedy':4,'politics':2,'drama':2,'wildcard':2}
    assert len(set(publish_slots('09:00','10:20',5)))==5


@pytest.mark.parametrize('bad',[{'shorts':{'per_day':11}},{'shorts':{'per_day':4}},{'scripts':{'provider':'gemini'}},
    {'series':{'platforms':['facebook']}},{'shorts':{'per_day':10,'first_slot':'09:00','last_slot':'10:00'}},{'unknown':1}])
def test_invalid_unattended_values_fail_loudly(tmp_path,monkeypatch,bad):
    with pytest.raises(ValueError):settings(tmp_path,monkeypatch,**bad)


def test_paid_script_needs_budget_and_standing_authorization(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-key-not-real')
    value=settings(tmp_path,monkeypatch,scripts={'provider':'openai'})
    service=ShortsService(tmp_path/'existing.db',value)
    recipe=value.config.lanes['shorts_daily'].recipes['comedy']
    config=recipe_configuration(recipe,{'title':'A cat learns to drive','category':'comedy'})
    assert config.script_provider=='openai' and config.max_cost_usd==1 and 'Structure:' in config.topic
    factory=Mock();scripts=__import__('app.services.script_generation_service',fromlist=['x']).ScriptGenerationService(service.storage.db_path,openai_factory=factory)
    scripts.run=Mock()
    # No budget and no authorization: nothing is quoted, reserved or submitted.
    assert service.paid_script('slot',0,config,scripts) is None
    auth=StandingAuthorizations(service.storage,value)
    identifier=auth.create(['openai'],'1','2',(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),actor='owner',confirmed=True)
    assert service.paid_script('slot',0,config,scripts) is None  # Global budget is still zero.
    factory.assert_not_called();scripts.run.assert_not_called()
    value.daily_budget_usd=Decimal('2')
    draft=service.paid_script('slot',0,config,scripts)
    assert draft['status']=='queued' and draft['actor']=='automation:standing:'+identifier
    scripts.run.assert_called_once_with(draft['attempt_id'])
    with service.storage.connect() as c:
        rows=c.execute('SELECT provider,status,amount_usd FROM automation_spend_reservations').fetchall()
    assert len(rows)==1 and rows[0]['provider']=='openai' and Decimal(rows[0]['amount_usd'])<=1
    # The authorization's daily cap (1 USD) bounds further paid scripts.
    for _ in range(6):service.paid_script('slot',0,config.model_copy(update={'topic':config.topic+str(uuid4())[:8]}),scripts)
    with service.storage.connect() as c:
        total=sum(Decimal(r[0]) for r in c.execute('SELECT amount_usd FROM automation_spend_reservations'))
    assert total<=1


def portrait(prompt,output,*,aspect_ratio,seed):
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(256,256),(seed%255,80,120)).save(output)
    return {'model':'fixture','sha256':'x'*64,'seed':seed}


def planned(tmp_path,monkeypatch):
    value=settings(tmp_path,monkeypatch);clock=Clock()
    story=SeriesService(tmp_path/'existing.db',value,clock=clock,planner=fake_planner())
    row=story.enqueue(str(uuid4()),confirmed=True);story.process(row['series_id'])
    return value,clock,story,row['series_id']


def test_auto_cast_freezes_synthetic_package_and_activates_once(tmp_path,monkeypatch):
    value,clock,story,identifier=planned(tmp_path,monkeypatch)
    auto=AutoCastService(story.storage.db_path,value,clock=clock,generate=Mock(side_effect=portrait))
    assert auto.tick()['cast_queued']==[identifier] and auto.tick()['cast_queued']==[]
    auto.process(identifier);auto.process(identifier)
    row=read_series(story.storage.db_path,identifier)
    assert row['status']=='active' and row['start_date']=='2026-11-09' and len(row['cast_package_ids'])==1
    assert auto._generate.call_count==2  # One portrait per bible character, never regenerated.
    from app.services.cinematic_cast_service import CinematicCastService
    package=CinematicCastService(story.storage.db_path).get(row['cast_package_ids'][0])
    assert {m['character_id'] for m in package['characters']}=={'ada','ben'} and len(package['assets'])==2
    with story.storage.connect() as c:
        actions={r[0]:json.loads(r[1]) for r in c.execute("SELECT action,detail_json FROM automation_events WHERE action LIKE 'series.%cast%'")}
    assert actions['series.auto_cast_generated']['human_inspected'] is False
    assert actions['series.cast_approved']['actor']=='automation:unattended'
    assert auto.tick()=={'cast_queued':[],'series_started':[],'episodes_skipped':[]}


def test_auto_cast_requires_opt_in_and_failed_cast_starts_a_new_series(tmp_path,monkeypatch):
    value,clock,story,identifier=planned(tmp_path,monkeypatch)
    manual=load_settings(ROOT/'config/lanes.yaml',environ={'AUTOMATION_ENABLED':'true'})
    off=AutoCastService(story.storage.db_path,manual,clock=clock,generate=Mock(side_effect=portrait))
    assert off.tick()=={'cast_queued':[],'series_started':[],'episodes_skipped':[]}
    with pytest.raises(ValueError,match='not enabled'):off.build(identifier)
    broken=AutoCastService(story.storage.db_path,value,clock=clock,generate=Mock(side_effect=ValueError('SDXL setup is missing')),series=story)
    broken.tick();broken.process(identifier)
    assert read_series(story.storage.db_path,identifier)['status']=='awaiting_cast'
    started=broken.tick()['series_started']
    assert len(started)==1 and started[0]!=identifier
    assert broken.tick()['series_started']==[]  # New planning is in progress; at most one per day.


def test_stuck_head_episode_is_skipped_but_never_while_platform_is_paused(tmp_path,monkeypatch):
    value,clock,story,identifier=planned(tmp_path,monkeypatch)
    auto=AutoCastService(story.storage.db_path,value,clock=clock,generate=Mock(side_effect=portrait))
    auto.tick();auto.process(identifier)
    old=(clock.now()-timedelta(hours=60)).isoformat()
    with story.storage.connect() as c:
        c.execute('UPDATE series_episodes SET planned_publish_at=? WHERE series_id=? AND ep_no IN (1,2)',(old,identifier))
        c.execute("INSERT INTO governor_state(scope,paused,created_at,updated_at) VALUES ('youtube',1,?,?)",(old,old))
    assert auto.tick()['episodes_skipped']==[]
    with story.storage.connect() as c:c.execute("UPDATE governor_state SET paused=0 WHERE scope='youtube'")
    assert auto.tick()['episodes_skipped']==[1]  # Only the head of the line per tick.
    with story.storage.connect() as c:
        assert c.execute('SELECT status FROM series_episodes WHERE series_id=? AND ep_no=1',(identifier,)).fetchone()[0]=='skipped'
        PublishingStorage(story.storage.db_path).initialize()
        job=str(uuid4());c.execute('UPDATE series_episodes SET job_id=? WHERE series_id=? AND ep_no=2',(job,identifier))
    PublishingStorage(story.storage.db_path).reserve_publication(job,'youtube')
    assert auto.tick()['episodes_skipped']==[]  # An upload attempt belongs to reconciliation.


def test_observer_only_reads_status_and_leaves_uncertain_rows(tmp_path,monkeypatch):
    value=settings(tmp_path,monkeypatch)
    db=tmp_path/'existing.db';store=PublishingStorage(db);store.initialize()
    ids={}
    for name,platform,status,video in [('yt','youtube','uploaded','v1'),('fb','facebook','submitted','f1'),('lost','youtube','needs_reconciliation',None),('hidden','youtube','uploaded','v2')]:
        ids[name]=store.reserve_publication(str(uuid4()),platform);store.update(ids[name],status=status,platform_video_id=video)
    youtube=Mock();youtube.get_video_status.side_effect=lambda v:{'v1':{'status':'published','external_status':'upload=processed'},'v2':{'status':'unknown','external_status':'not_accessible'}}[v]
    facebook=Mock();facebook.get_status.return_value={'status':{'video_status':'ready','publishing_phase':{'status':'complete'}}}
    observer=PublicationObserver(db,value,youtube=youtube,facebook=facebook)
    result=observer.tick()
    assert result['checked']==3 and result['changed']==2
    assert store.get(ids['yt'])['status']=='published' and store.get(ids['fb'])['status']=='published'
    assert store.get(ids['hidden'])['status']=='uploaded' and store.get(ids['lost'])['status']=='needs_reconciliation'
    assert not youtube.upload.called and not facebook.upload.called
    assert facebook_status('submitted',{'status':{'video_status':'processing'}})=='submitted'
    assert facebook_status('submitted',{'status':{'video_status':'error'}})=='failed'
    youtube.get_video_status.side_effect=RuntimeError('invalid_grant')
    store.update(ids['hidden'],status='uploaded')
    assert observer.tick()['unavailable']==['youtube'] and store.get(ids['hidden'])['status']=='uploaded'
    value.kill_switch_file.touch()
    assert observer.tick()=={'status':'killed','checked':0}


def test_facebook_long_video_is_resumable_without_retries_and_off_by_default(tmp_path):
    video=tmp_path/'episode.mp4';video.write_bytes(b'a'*10)
    replies=[{'video_id':'55','upload_session_id':'s','start_offset':'0','end_offset':'6'},
        {'start_offset':'6','end_offset':'10'},{'start_offset':'10','end_offset':'10'},{'success':True}]
    session=Mock();session.post.side_effect=[Mock(json=Mock(return_value=r),raise_for_status=Mock()) for r in replies]
    publisher=FacebookVideoPublisher('page','token',session=session)
    result=publisher.upload(video,'Title','Description')
    assert result['video_id']=='55' and result['status']=='submitted' and session.post.call_count==4
    assert session.post.call_args_list[0].args[0].startswith('https://graph-video.facebook.com/') and '/page/videos' in session.post.call_args_list[0].args[0]
    assert [c.kwargs['data']['upload_phase'] for c in session.post.call_args_list]==['start','transfer','transfer','finish']
    assert session.post.call_args_list[1].kwargs['files']['video_file_chunk'][1]==b'a'*6
    stalled=Mock();stalled.post.side_effect=[Mock(json=Mock(return_value=r),raise_for_status=Mock()) for r in [replies[0],{'start_offset':'0','end_offset':'6'}]]
    with pytest.raises(RuntimeError,match='advance'):FacebookVideoPublisher('page','token',session=stalled).upload(video,'T','D')
    assert stalled.post.call_count==2
    from app.services.publishing_service import PublishingService
    jobs=Mock();jobs.get_job.return_value={'job_id':'j','status':'approved','plan_id':'p','video_path':str(video)}
    plans=Mock();plans.get_plan.return_value={'title':'T','description':'D','hashtags':[],'scenes':[{'duration_seconds':300}]}
    with pytest.raises(ValueError,match='Long-form Facebook'):PublishingService(jobs=jobs,content_storage=plans,publications=Mock()).publish('j','facebook',dry_run=True)
    assert PublishingService(jobs=jobs,content_storage=plans,publications=Mock(),allow_long_facebook=True).publish('j','facebook',dry_run=True)['status']=='dry_run'


def test_only_credential_refresh_failures_can_be_closed_and_never_retried(tmp_path):
    store=PublishingStorage(tmp_path/'p.db');store.initialize()
    made={}
    for name,event,video in [('auth','RefreshError',None),('timeout','ReadTimeout',None),('accepted','RefreshError','v9')]:
        made[name]=store.reserve_publication('job-'+name,'youtube')
        store.update(made[name],status='needs_reconciliation',platform_video_id=video)
        store.record_event(made[name],'upload_outcome_uncertain',event)
    assert [r['publication_id'] for r in store.unsent_attempts()]==[made['auth']]
    with pytest.raises(ValueError,match='Confirm'):store.close_unsent(made['auth'],notes='checked YouTube Studio')
    for name in ('timeout','accepted'):
        with pytest.raises(ValueError,match='inspect remote state'):store.close_unsent(made[name],confirmed_not_uploaded=True,notes='checked YouTube Studio')
    assert store.close_unsent(made['auth'],confirmed_not_uploaded=True,notes='checked YouTube Studio')['status']=='failed'
    assert store.unsent_attempts()==[] and store.get_by_job('job-auth','youtube')['publication_id']==made['auth']
    with pytest.raises(Exception):store.reserve_publication('job-auth','youtube')  # Uniqueness survives: no second upload.
