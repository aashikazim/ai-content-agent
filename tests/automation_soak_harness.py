"""Private validation tool, NOT application runtime. All providers/media are mocks.

Reuse the tested fixture factories rather than create a second production system.
Requires the repository's existing development/test environment. Never import from
app entrypoints. CLI confines it to a marked new private directory.
"""
from contextlib import ExitStack
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4
from pytest import MonkeyPatch
from app.automation.clock import local_day,utc_now
from app.automation.daily_report import write_daily_report
from app.automation.idempotency import canonical_json,fingerprint,stable_id,episode_id
from app.automation.runtime import DirectorRuntime
from app.automation.series.episodes import EpisodeService
from app.automation.series.service import SeriesService,read_series
from app.automation.shorts_service import ShortsService
from app.automation.trends.collector import TrendCollector
from app.automation.trends.sources import YouTubeSource
from app.models.production_plan import ProductionPlan
from app.services.youtube_service import YouTubeService
from tests.test_automation_governor import setup as governor_fixture,add_job
from tests.test_automation_series import fake_planner,bible_data
from tests.test_long_drama import long_production

ROOT=Path(__file__).parents[1]


class SoakHarness:
    def __init__(self,root,clock):
        self.root=Path(root).resolve();self.clock=clock;self.forbidden_calls=0
        if self.root==ROOT or not (self.root.is_relative_to(ROOT/'data/automation/validation') or self.root.is_relative_to(Path('/tmp'))):raise ValueError('Soak root must be a dedicated private validation directory')
        marker=self.root/'MOCK_ONLY.json'
        if self.root.exists() and any(self.root.iterdir()) and not marker.is_file():raise ValueError('Refusing an unmarked existing directory')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not marker.exists():marker.write_text(json.dumps({'mode':'recorded_sources_mock_generation_dry_run','created_at':utc_now(clock).isoformat(),'never_use_live_database':True},indent=2))
        else:
            if json.loads(marker.read_text()).get('mode')!='recorded_sources_mock_generation_dry_run':raise ValueError('Wrong verification marker')
        self.patches=MonkeyPatch();self.stack=ExitStack();self.calls=[]
        self.call_log=self.root/'dry-run-calls.jsonl'
        if self.call_log.exists():self.calls=[tuple(json.loads(line)) for line in self.call_log.read_text().splitlines()]
        def forbidden(*args,**kwargs):self.forbidden_calls+=1;raise AssertionError('External request/GPU/upload forbidden by soak harness')
        for target in ('requests.sessions.Session.send','httpx.Client.send','httpx.AsyncClient.send','socket.socket.connect',
            'app.services.youtube_publisher.YouTubePublisher.upload','app.services.facebook_publisher.FacebookPublisher.upload',
            'app.services.local_cinematic_service.LocalCinematicService.submit','ollama.Client.chat','ollama.Client.generate','subprocess.Popen'):
            self.stack.enter_context(patch(target,side_effect=forbidden))
        folder=self.root/'fixtures';folder.mkdir(exist_ok=True)
        self.gov,_=governor_fixture(folder,self.patches)
        self.gov.storage.clock=clock
        # Do not change host .env; bound this validation process to fixture paths.
        self.patches.setenv('AUTOMATION_ENABLED','true');self.patches.setenv('AUTOMATION_GOVERNOR_ENABLED','true');self.patches.setenv('AUTOMATION_GOVERNOR_DRY_RUN','true')
        self.patches.setenv('AUTOMATION_CLASSIFY_WITH_OLLAMA','false');self.patches.setenv('DURABLE_WORKERS','false')
        self.settings=self.gov.settings
        self.settings.config.lanes['story_series'].episode_duration_s.min=480
        self.settings.config.lanes['story_series'].episode_duration_s.max=480
        self.patches.setenv('DATABASE_PATH',self.gov.storage.db_path)
        package={'package_id':stable_id('soak_fixture','cast'),'sha256':'MOCK-NOT-A-REAL-CAST','characters':bible_data()['characters']}
        for a in package['characters']:a.update(primary_reference_id=stable_id('soak_actor',a['character_id']),reference_ids=[],tts_voice='en-us')
        self.cast=SimpleNamespace(get=lambda _:package)
        # Identity verification is mocked for this synthetic fixture only.
        self.gov.productions.cast=self.cast;self.gov.productions.references.verified=lambda _:[]
        self.series=SeriesService(self.gov.storage.db_path,self.settings,clock=clock,planner=fake_planner(),cast_service=self.cast)
        with self.gov.storage.connect() as c:row=c.execute("SELECT series_id FROM series ORDER BY created_at LIMIT 1").fetchone()
        if row:self.series_id=row[0]
        else:
            value=self.series.enqueue(stable_id('soak_fixture','initial_series'),confirmed=True,actor='validation:fixture-planner')
            self.series.process(value['series_id']);value=read_series(self.gov.storage.db_path,value['series_id'])
            self.series.approve_cast(value['series_id'],package['package_id'],value['updated_at'],confirmed=True,actor='validation:SIMULATED-owner',start_date=local_day(clock,self.settings.config.timezone))
            self.series_id=value['series_id']
        self.short_producer=self._short
        self.shorts=ShortsService(self.gov.storage.db_path,self.settings,clock=clock,producer=self._short)
        self.episodes=EpisodeService(self.gov.storage.db_path,self.settings,clock=clock,cast=self.cast,producer=self._episode)
        self.director=DirectorRuntime(self.gov.storage.db_path,self.settings,clock=clock,shorts=self.shorts,episodes=self.episodes)
        self.gov.set_lane('story_series','auto_with_gates',confirmed=True,actor='validation:SIMULATED-owner')
        original=self.gov.publisher_factory
        def publishers(identifier,dry):
            assert dry and self.gov.settings.governor_dry_run
            service=original(identifier,True);method=service.publish
            def publish(job_id,platform,dry_run=True):
                assert dry_run
                self.calls.append((job_id,platform))
                with self.call_log.open('a') as log:log.write(json.dumps([job_id,platform])+'\n')
                return method(job_id,platform,dry_run=True)
            service.publish=publish;return service
        self.gov.publisher_factory=publishers
        # CPU media checks deliberately mocked; byte fixtures are not MP4 videos.
        def media(path,*args,**kwargs):
            with self.gov.storage.connect() as c:
                for row in c.execute('SELECT quality_json FROM video_productions'):
                    quality=json.loads(row[0])
                    if quality['sha256']==hashlib.sha256(Path(path).read_bytes()).hexdigest():return quality
            raise ValueError('Mock media fixture not registered')
        self.mock_media=media
        self.gov.gates.media_validator=media

    def _short(self,identifier,attempt,topic,recipe):
        day=local_day(self.clock,self.settings.config.timezone)
        with self.gov.storage.connect() as c:slot=c.execute('SELECT slot FROM daily_plans WHERE daily_plan_id=?',(identifier,)).fetchone()[0]
        # Fixture helper stores an extra daily row by default; these producers reuse
        # the already-reserved slot instead, as the actual producer does.
        item=add_job(self.gov,self.root/'fixtures',title='[MOCK] '+topic['title'][:70],daily=False)
        with self.gov.storage.connect() as c:c.execute('UPDATE automation_slot_attempts SET production_id=? WHERE daily_plan_id=? AND attempt_no=?',(item['production_id'],identifier,attempt))
        return {'status':'awaiting_quality_review','job_id':item['job_id']}

    def _episode(self,identifier,row):
        # Deterministic simulated failure, not a failed real neural job.
        if row['ep_no']==10:return {'status':'failed','error':'MOCK: injected episode10 technical failure'}
        plan=long_production(480).model_dump(mode='json')
        actors={old['character_id']:new for old,new in zip(plan['characters'],bible_data()['characters'])}
        plan['characters']=[{k:a[k] for k in ('character_id','name','description','voice_notes')} for a in actors.values()]
        for line in plan['dialogue']:line['character_id']=actors[line['character_id']]['character_id']
        for shot in plan['shots']:shot['character_ids']=[actors[a]['character_id'] for a in shot['character_ids']]
        plan=ProductionPlan.model_validate(plan)
        item=add_job(self.gov,self.root/'fixtures',title=f'[MOCK] Episode {row["ep_no"]} verification',production=plan,configuration=row['request']['configuration'],daily=False)
        state={'knowledge':{'ada':['MOCK screenplay event '+str(row['ep_no'])]},'open_threads':[],'script_sha256':fingerprint(plan.model_dump(mode='json'))}
        with self.gov.storage.connect() as c:c.execute('UPDATE series_episodes SET summary_text=?,state_after_json=?,script_plan_id=? WHERE series_id=? AND ep_no=?',
            ('MOCK screenplay summary '+str(row['ep_no']),canonical_json(state),'MOCK-script-'+identifier,row['series_id'],row['ep_no']))
        self.episodes.update(identifier,production_id=item['production_id'])
        return {'status':'awaiting_quality_review','job_id':item['job_id']}

    def _handle(self,target):
        kind,identifier=target.split(':',1)
        if kind=='shorts':self.shorts.process_day(identifier)
        elif kind=='episode':self.episodes.process(identifier)
        elif kind=='series':
            planner=fake_planner();responses=list(planner.request.side_effect)
            # New mock premises must pass the real history-dedupe validator.
            responses[0]={'candidates':[
                {'title':'Skyline Signal','premise':'Two tram drivers decode messages hidden inside rooftop antennas before their mountain city loses its last transport link.'},
                {'title':'Glasshouse Riddles','premise':'Rival botanists trace a stolen seed catalogue through abandoned greenhouses while saving the final harvest of their district.'},
                {'title':'Station of Shadows','premise':'Three mechanics investigate an empty railway station whose changing timetables reveal a concealed tunnel beneath the city.'}]}
            SeriesService(self.gov.storage.db_path,self.settings,clock=self.clock,planner=Mock(request=Mock(side_effect=responses)),cast_service=self.cast).process(identifier)
        else:raise ValueError('Unexpected verification queue target')

    def tick(self,*,bootstrap=False):
        body=json.loads((ROOT/'tests/fixtures/automation/youtube.json').read_text())
        client=Mock();client.videos.return_value.list.return_value.execute.return_value=body
        source=YouTubeSource(YouTubeService(client),self.clock)
        collected=TrendCollector(self.gov.storage,{'youtube':source},3).collect()
        if bootstrap:
            # Explicit synthetic setup, analogous to a manual local request. It
            # ensures the24h check covers today's slots before the next night.
            day=local_day(self.clock,self.settings.config.timezone)
            self.shorts.enqueue_day(day,confirmed=True,launch=False,actor='validation:MOCK-bootstrap')
            self.episodes.enqueue(self.series_id,1,confirmed=True,launch=False,actor='validation:MOCK-bootstrap')
        directed=self.director.tick(launch=False)
        drained=0
        while self.shorts.queue.run_next({'automation':self._handle}):
            drained+=1
            if drained>50:raise AssertionError('Unbounded verification queue')
        self.gov.gates.media_validator=self.mock_media
        decisions=self.gov.tick()
        report=write_daily_report(self.gov.storage.db_path,local_day(self.clock,self.settings.config.timezone),self.settings.config.timezone)
        with self.gov.storage.connect() as c:
            publications=c.execute('SELECT count(*) FROM publications').fetchone()[0]
            actions=c.execute('SELECT count(*) FROM automation_governor_actions').fetchone()[0]
            failures=[dict(r) for r in c.execute("SELECT work_id,status,error FROM app_work_queue WHERE status='interrupted'")]
            waiting=c.execute("SELECT count(*) FROM series WHERE status='awaiting_cast'").fetchone()[0]
        if self.forbidden_calls:raise AssertionError('Forbidden external/GPU/upload path attempted')
        if len(self.calls)!=len(set(self.calls)):raise AssertionError('Duplicate publishing validation call')
        return dict(at=utc_now(self.clock).isoformat(),mode='recorded_sources_mock_generation_dry_run',collection=collected,director=directed,
            governor=decisions,drained=drained,action_count=actions,publication_rows=publications,forbidden_calls=self.forbidden_calls,
            validation_calls=len(self.calls),duplicate_calls=0,interrupted=failures,awaiting_cast=waiting)

    def simulate_publish_episode(self,number):
        """35-day fake-clock fixture only. Never used by the24h dry-run CLI."""
        with self.gov.storage.connect() as c:row=c.execute('SELECT * FROM series_episodes WHERE series_id=? AND ep_no=?',(self.series_id,number)).fetchone()
        if number==10:
            self.episodes.skip(self.series_id,10,'validation:SIMULATED-owner','MOCK deliberate skip after injected failure',True);return
        if not row['job_id']:raise AssertionError('Expected mocked episode was not produced')
        assert self.episodes.publishable(self.series_id,number)
        publication=self.gov.publications.reserve_publication(row['job_id'],'youtube')
        self.gov.publications.update(publication,'published',platform_video_id='MOCK-never-uploaded-'+str(number))
        self.episodes.sync_publications(self.series_id)

    def close(self):self.stack.close();self.patches.undo()
