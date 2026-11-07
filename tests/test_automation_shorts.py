"""Simulated days and actual orchestration with mocked local inference only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.automation.clock import FrozenClock
from app.automation.config import load_settings,Recipe
from app.automation.gpu_planner import GPUPlanner
from app.automation.reports import read_day,write_report
from app.automation.shorts_service import ShortsService
from app.automation.lanes.shorts import ShortsPlanner,recipe_configuration
from app.automation.trends.collector import TrendCollector
from app.automation.trends import TrendSignal
from app.models.production_plan import ProductionPlan
from tests.test_generation_api import generation_api,sign_in

@pytest.fixture(autouse=True)
def forbid_real_models_and_requests(monkeypatch):
    forbidden=Mock(side_effect=AssertionError('Real inference/network forbidden in automated tests'))
    monkeypatch.setattr('ollama.Client.chat',forbidden)
    monkeypatch.setattr('ollama.Client.generate',forbidden)
    monkeypatch.setattr('requests.sessions.Session.send',forbidden)
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.submit',forbidden)


ROOT=Path(__file__).parents[1]
NOW=datetime(2026,11,7,1,tzinfo=timezone.utc)


def setup(tmp_path,monkeypatch,producer=None):
    monkeypatch.setenv('AUTOMATION_DATA_DIR',str(tmp_path/'automation'))
    settings=load_settings(ROOT/'config/lanes.yaml',environ={'AUTOMATION_ENABLED':'true'})
    service=ShortsService(tmp_path/'existing.db',settings,clock=FrozenClock(NOW),producer=producer)
    titles=['Funny kitten at dinner','Comedian loses a lunchbox','Election senate policy debate','Drama rescue at midnight','Rocket engine discovery',
        'Painter restores ancient mural','Ocean tide reveals canyon','Cyclist repairs mountain wheel','Chef tries exotic spices','Robot plants a garden']
    source=Mock();source.collect.return_value=[TrendSignal(source='fixture',external_id=str(i),title=t,timestamp=NOW,score=1,velocity=3) for i,t in enumerate(titles)]
    TrendCollector(service.storage,{'fixture':source}).collect()
    return service


def test_recipe_uses_only_local_and_compiles_exact_shots():
    topic={'title':'A missing lunchbox','category':'comedy','urls':['https://example.org/lead']}
    recipe=Recipe(style='comedy_satire',duration_s=30)
    config=recipe_configuration(recipe,topic)
    fake=Mock();fake.request.return_value={'title':'The missing sandwich','description':'An original office comedy.','hook':'Who took lunch?',
        'beats':[{'narration':'A hungry office worker discovers that the lunchbox is empty.','action':'A worker opens an empty lunchbox and gasps.','camera':'Slow dolly forward.'}]*3}
    value=ShortsPlanner(config,topic,recipe,fake).generate_production_plan('not another model call')
    assert isinstance(value,ProductionPlan) and value.content_plan.total_duration==30
    assert [s.duration_seconds for s in value.shots]==[10,10,10]
    assert value.factual_status=='satire' and config.max_cost_usd==0
    assert fake.request.call_args.args[1]['properties']['beats']['minItems']==3
    assert fake.request.call_args.args[1]['properties']['beats']['maxItems']==3
    assert config.script_provider=='ollama' and config.video_provider=='local_cinematic'


def test_atomic_plan_and_execution_are_idempotent(tmp_path,monkeypatch):
    producer=Mock(side_effect=lambda identifier,*_: {'status':'awaiting_quality_review','job_id':'job-'+identifier})
    service=setup(tmp_path,monkeypatch,producer)
    def enqueue(_):return service.enqueue_day(date(2026,11,6),confirmed=True)
    with ThreadPoolExecutor(max_workers=3) as pool:plans=list(pool.map(enqueue,range(5)))
    assert len({p['run']['run_id'] for p in plans})==1
    run=plans[0]['run']['run_id'];service.process_day(run);service.process_day(run)
    assert producer.call_count==5
    assert all(row['status']=='in_review' for row in read_day(service.storage.db_path,date(2026,11,6))['slots'])
    assert service.enqueue_day(date(2026,11,6),confirmed=True)['run']['status']=='completed'
    with service.storage.connect() as conn:
        assert conn.execute("SELECT count(*) FROM app_work_queue WHERE kind='automation'").fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM automation_slot_attempts').fetchone()[0]==5
    assert 'No approval or publishing' in write_report(service.storage.db_path,date(2026,11,6))


def test_known_failure_substitutes_once_and_uncertain_holds(tmp_path,monkeypatch):
    count=0
    def known(identifier,attempt,*_):
        nonlocal count;count+=1
        return {'status':'failed','error':'Technical fixture failure'} if attempt==0 else {'status':'awaiting_quality_review','job_id':'replacement-'+identifier}
    service=setup(tmp_path,monkeypatch,known);plan=service.enqueue_day(date(2026,11,6),confirmed=True)
    service.process_day(plan['run']['run_id'])
    with service.storage.connect() as conn:
        assert conn.execute('SELECT max(attempt_no) FROM automation_slot_attempts').fetchone()[0]<=1
    assert count<=10
    other=setup(tmp_path/'other',monkeypatch,Mock(return_value={'status':'needs_reconciliation','error':'Unknown result'}))
    day=other.enqueue_day(date(2026,11,6),confirmed=True);other.process_day(day['run']['run_id'])
    assert read_day(other.storage.db_path,date(2026,11,6))['run']['status']=='needs_reconciliation'
    other.process_day(day['run']['run_id']);assert other.producer.call_count==1


def test_gpu_seed_measurements_degradation_and_dst(tmp_path,monkeypatch):
    service=setup(tmp_path,monkeypatch);gpu=service.gpu
    assert gpu.rate('ltx-2.5','text')==(29,'seed')
    gpu.record('measured-job','ltx-2.5','text',30,300,10,350)
    gpu.record('measured-job','ltx-2.5','text',30,900)
    assert gpu.rate('ltx-2.5','text')==(10,'measured')
    items=[{'slot':i,'category':'wildcard' if i==5 else 'comedy','model':'ltx-2.5','input_mode':'text','duration_s':45} for i in range(1,6)]
    forecast=gpu.forecast(date(2026,11,1),items)
    assert forecast['capacity_seconds']==9*3600  # NYC fall-back night.
    service.settings.config.gpu.nightly_window.end='00:40'
    degraded=gpu.forecast(date(2026,11,7),items)
    assert degraded['fits'] and any(i.get('skipped') for i in degraded['items'])
    assert all(i['duration_s']==30 for i in degraded['items'])
    with pytest.raises(ValueError):gpu.record('bad','ltx-2.5','text',30,float('inf'))


def test_simulated_days_no_duplicate_daily_requests(tmp_path,monkeypatch):
    producer=Mock(side_effect=lambda identifier,*_: {'status':'awaiting_quality_review','job_id':'j-'+identifier})
    service=setup(tmp_path,monkeypatch,producer)
    themes=[
      ['Funny gardener waters a scarecrow','Comedian chases a runaway suitcase','Election council budget standoff','Drama lighthouse keeper discovers a signal','Telescope maps a distant comet'],
      ['Funny baker mixes up the orders','Comedy astronaut discovers a hiccup','Parliament debates a city tram','Mystery locked attic contains a violin','Deep ocean creatures glow in darkness'],
      ['Prank robot joins a dance lesson','Comedian struggles with an umbrella','Senate weighs an education proposal','Drama mountain hikers uncover a shelter','Volcano sensor detects tiny vibrations'],
      ['Funny penguin visits a barbershop','Comedy detective forgets a password','Government discusses coastal protection','Mystery train passenger swaps a ticket','Solar powered boat crosses a lake'],
      ['Prank chef serves invisible pasta','Comedian learns to ride a unicycle','President addresses transport reform','Drama violin maker repairs an heirloom','Desert astronomer photographs a meteor'],
      ['Funny mechanic invents a square wheel','Comedy librarian rescues a noisy cart','Minister proposes a clean water bill','Mystery musician hears a forgotten melody','Microscope reveals living coral patterns'],
      ['Prank painter decorates the wrong door','Comedian negotiates with a vending machine','Politics mayor announces a river crossing','Drama nurse finds a childhood photograph','Glacier expedition measures a hidden cavern'],
    ]
    for offset,titles in enumerate(themes):
        now=NOW+timedelta(days=offset);service.storage.clock=FrozenClock(now)
        source=Mock();source.collect.return_value=[TrendSignal(source='simulation',external_id=f'{offset}-{i}',title=t,timestamp=now,score=100,velocity=100) for i,t in enumerate(titles)]
        TrendCollector(service.storage,{'simulation':source}).collect()
        plan=service.enqueue_day(now.date(),confirmed=True)
        service.process_day(plan['run']['run_id']);calls=producer.call_count
        service.enqueue_day(now.date(),confirmed=True);service.process_day(plan['run']['run_id'])
        assert producer.call_count==calls and read_day(service.storage.db_path,now.date())['run']['status']=='completed'
    assert producer.call_count==35
    with service.storage.connect() as conn:
        rows=conn.execute('SELECT plan_date,lane,slot,count(*) FROM daily_plans GROUP BY plan_date,lane,slot').fetchall()
        assert len(rows)==35 and all(r[3]==1 for r in rows)


def test_disabled_run_and_read_only_empty_endpoints(generation_api,monkeypatch):
    api=generation_api;headers=sign_in(api)
    monkeypatch.setenv('AUTOMATION_LANES_FILE',str(ROOT/'config/lanes.yaml'));monkeypatch.setenv('AUTOMATION_ENABLED','false')
    assert api.client.post('/api/v1/automation/plan/2026-11-06/run',headers=headers,json={'confirmed':True}).status_code==422
    response=api.client.get('/api/v1/automation/plan/2026-11-06')
    assert response.status_code==200 and response.json()['slots']==[]
    api.execute.assert_not_called();api.publisher.assert_not_called()


def test_local_planner_transport_uncertainty_no_second_request(monkeypatch,tmp_path):
    import app.automation.local_planner as module
    from app.services.openai_script_service import PaidScriptError
    monkeypatch.setattr(module,'runtime_root',lambda:tmp_path)
    client=Mock();client.chat.side_effect=TimeoutError('fixture timeout')
    with pytest.raises(PaidScriptError) as caught:module.LocalJSONPlanner('local',client).request('prompt',{})
    assert caught.value.uncertain and client.chat.call_count==1


def test_existing_workflow_with_mocked_local_model_and_actual_ffmpeg(tmp_path,monkeypatch):
    import shutil
    from uuid import uuid4
    from tests.test_cloud_media import fixture_video
    from app.services.cloud_video_service import CloudVideoService
    import app.automation.lanes.shorts as recipes
    from app.services.cloud_video_renderer import validate_clip
    source=tmp_path/'moving.mp4';fixture_video(source,10)
    monkeypatch.setenv('CONTENT_PLANS_DIR',str(tmp_path/'plans'))
    monkeypatch.setenv('CLOUD_VIDEOS_DIR',str(tmp_path/'videos'))
    monkeypatch.setenv('CHARACTER_REFERENCES_DIR',str(tmp_path/'references'))
    monkeypatch.setenv('DRIVING_REFERENCES_DIR',str(tmp_path/'driving'))
    planner=Mock();planner.request.return_value={'title':'The disappearing sandwich','description':'An original office story.','hook':'Where did lunch go?',
        'beats':[{'narration':'The office worker opens the empty lunchbox and looks surprised.','action':'A surprised worker opens an empty lunchbox and gestures.','camera':'A moving cinematic camera.'}]*3}
    monkeypatch.setattr(recipes,'LocalJSONPlanner',lambda model:planner)
    class Provider:
        def check_model(self):return {'status':'fixture'}
        def submit(self,*args,**kwargs):return {'provider':'local_cinematic','interaction_id':str(uuid4()),'status':'completed','usage':{'ltx_seconds':1.5}}
        def retrieve(self,identifier):return {'provider':'local_cinematic','interaction_id':identifier,'status':'completed','usage':{'ltx_seconds':1.5}}
        def download(self,snapshot,destination):destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    fake=Provider();monkeypatch.setattr(CloudVideoService,'provider_for',lambda self,config,model=None:fake)
    human=Mock(side_effect=AssertionError('Human quality checks must not be invented'))
    monkeypatch.setattr(CloudVideoService,'send_to_review',human)
    monkeypatch.setattr('app.services.speech_analysis.analyze_audio',lambda *args,**kwargs:{'method':'fixture_asr','duration_seconds':10,'words':[{'word':'fixture','start':0,'end':1}], 'human_review_required':True})
    service=setup(tmp_path,monkeypatch)
    day=service.enqueue_day(date(2026,11,6),confirmed=True)
    row=day['slots'][0]
    with service.storage.connect() as conn:conn.execute("INSERT INTO automation_slot_attempts(daily_plan_id,attempt_no,topic_json,status,created_at,updated_at) VALUES (?,0,?,'starting',?,?)",(row['daily_plan_id'],json.dumps(row['recipe']['topic']),service.storage.timestamp(),service.storage.timestamp()))
    recipe=Recipe.model_validate(row['recipe']['configuration']).model_copy(update={'duration_s':30,'speech_profile_id':None})
    result=service.produce_slot(row['daily_plan_id'],0,row['recipe']['topic'],recipe)
    assert result['status']=='awaiting_quality_review'
    from app.services.job_service import JobService
    job=JobService(service.storage.db_path).get_job(result['job_id'])
    assert job['status']=='awaiting_quality_review'
    assert validate_clip(Path(job['video_path']),30,require_audio=True)['duration_seconds']>=29.9
    with service.storage.connect() as conn:assert conn.execute('SELECT count(*) FROM automation_gpu_measurements WHERE is_seed=0').fetchone()[0]==1
    human.assert_not_called()


def test_lane_protocol_and_durable_queue_dispatch(tmp_path,monkeypatch):
    from app.automation.lanes import Lane,LaneContext
    from app.automation.lanes.shorts import ShortsLane
    from app.automation.selector import TopicSelector
    service=setup(tmp_path,monkeypatch,Mock(side_effect=lambda identifier,*_:{'status':'awaiting_quality_review','job_id':'job-'+identifier}))
    lane=ShortsLane(TopicSelector(service.storage.db_path,service.storage.clock),service)
    assert isinstance(lane,Lane)
    context=LaneContext('shorts_daily',date(2026,11,6),NOW,service.settings.config.lanes['shorts_daily'])
    items=lane.plan_day(context);assert len(items)==5
    intent=lane.produce(context,items[0]);assert intent
    assert service.queue.run_next({'automation':lambda target:service.process_day(target.split(':',1)[1])})
    assert not service.queue.run_next({'automation':Mock()})
    assert lane.publishable(context)==()  # No human approvals in this fixture.


def test_run_api_confirms_generation_only(generation_api,monkeypatch):
    api=generation_api;headers=sign_in(api)
    monkeypatch.setenv('AUTOMATION_LANES_FILE',str(ROOT/'config/lanes.yaml'));monkeypatch.setenv('AUTOMATION_ENABLED','true')
    service=setup(api.tmp_path,monkeypatch,Mock());seed_path=service.storage.db_path
    # The existing session stays in its original DB; seed that same application's DB.
    with service.storage.connect() as source, __import__('sqlite3').connect(api.db_path) as target:
        signals=source.execute('SELECT * FROM trend_signals').fetchall()
    from app.automation.storage import AutomationStorage
    target=AutomationStorage(api.db_path,clock=FrozenClock(NOW))
    with target.connect() as conn:
        for row in signals:conn.execute('INSERT INTO trend_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(row))
    monkeypatch.setattr(ShortsService,'launch_drain',Mock())
    url='/api/v1/automation/plan/2026-11-06/run'
    assert api.client.post(url,headers=headers,json={}).status_code==422
    response=api.client.post(url,headers=headers,json={'confirmed':True})
    assert response.status_code==202 and len(response.json()['slots'])==5
    again=api.client.post(url,headers=headers,json={'confirmed':True})
    assert again.json()['run']['run_id']==response.json()['run']['run_id']
    assert api.client.post(url,headers=headers,json={'confirmed':True,'video_provider':'google'}).status_code==422
    api.publisher.assert_not_called()
