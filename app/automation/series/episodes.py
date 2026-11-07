"""Durable local episode production and ordered series lifecycle; never publishes."""
from datetime import date,datetime,timedelta,timezone
import json
import math
import os
from pathlib import Path
import time
from typing import Any,Callable,TypeVar
from uuid import uuid4
from zoneinfo import ZoneInfo
from pydantic import BaseModel,ValidationError
from app.automation.clock import Clock,local_day
from app.automation.config import AutomationSettings,StoryLaneConfig,load_settings
from app.automation.events import EventLog
from app.automation.gpu_planner import GPUPlanner
from app.automation.idempotency import canonical_json,fingerprint,stable_id
from app.automation.local_planner import LocalJSONPlanner,LocalRequestRejected
from app.automation.reports import data_root
from app.automation.series.episode_planner import EpisodePlanner,hook_check,packaging,state_from_script
from app.automation.series.service import SeriesService,read_series
from app.automation.shorts_service import backup_before_upgrade,ShortsService
from app.automation.storage import AutomationStorage
from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan
from app.services.cinematic_cast_service import CinematicCastService
from app.services.script_generation_service import ScriptGenerationService
from app.services.cloud_video_service import CloudVideoService
from app.services.job_service import JobService
from app.services.work_queue import WorkQueue
from app.workflows.content_workflow import ContentWorkflow


Model=TypeVar("Model",bound=BaseModel)


class EpisodeService:
    def __init__(self,db_path:str | Path,settings:AutomationSettings | None=None,*,clock:Clock | None=None,planner:Any=None,cast:Any=None,producer:Callable[[str,dict],dict] | None=None,sleep:Callable[[float],None]=time.sleep)->None:
        self.settings=settings or load_settings();backup_before_upgrade(db_path)
        self.storage=AutomationStorage(db_path,clock=clock);self.queue=WorkQueue(db_path)
        self.planner=planner;self.cast=cast;self.producer=producer or self.produce;self.sleep=sleep
        self.gpu=GPUPlanner(self.storage,self.settings.config)

    def context(self,identifier:str,number:int)->dict:
        series=read_series(self.storage.db_path,identifier)
        if not series or series['status']!='active' or series['planning_status']!='completed':raise ValueError('An active series with human-approved cast is required')
        if not series['cast_package_ids']:raise ValueError('Series cast approval is missing')
        cast=(self.cast or CinematicCastService(self.storage.db_path)).get(series['cast_package_ids'][0])
        if fingerprint(series['bible'])!=series['bible_sha256']:raise ValueError('Frozen series bible changed')
        known={a['character_id']:a for a in series['bible']['characters']}
        if set(known)!={a['character_id'] for a in cast['characters']}:raise ValueError('Approved cast differs from the frozen series')
        for actor in cast['characters']:
            if any(actor[k]!=known[actor['character_id']][k] for k in ('name','description','wardrobe','voice_notes')):raise ValueError('Approved cast appearance/voice differs from bible')
        with self.storage.connect() as c:
            previous=[dict(r) for r in c.execute('SELECT ep_no,summary_text,state_after_json,status FROM series_episodes WHERE series_id=? AND ep_no<? ORDER BY ep_no DESC LIMIT 3',(identifier,number))][::-1]
        if number>1 and (not previous or previous[-1]['ep_no']!=number-1 or not previous[-1]['summary_text']):raise ValueError('Previous episode script/state must complete before buffering the next')
        outline=next((e for e in series['episodes'] if e['ep_no']==number),None)
        if not outline:raise ValueError('Unknown episode')
        following=next((e for e in series['episodes'] if e['ep_no']==number+1),None)
        return {'series':{k:series[k] for k in ('series_id','title','genre','premise','lane','bible_sha256')},'bible':series['bible'],'cast':cast,
            'outline':outline,'previous_summaries':previous,'story_state':json.loads(previous[-1]['state_after_json']) if previous else {},
            'next_outline':following,'packaging':packaging(series,outline,previous,following)}

    def enqueue(self,identifier:str,number:int,*,actor:str='automation:episodes',confirmed:bool=False,launch:bool=False,validation_duration:int | None=None)->dict:
        if type(number) is not int or not 1<=number<=30:raise ValueError('Episode number must be an integer from1to30')
        if confirmed is not True:raise ValueError('Confirm local episode generation')
        run_id=stable_id('series_episode_run',identifier,number)
        with self.storage.connect() as c:old=c.execute('SELECT * FROM automation_episode_runs WHERE run_id=?',(run_id,)).fetchone()
        if old:return self.get(run_id)
        context=self.context(identifier,number);lane=self.settings.config.lanes[context['series']['lane']]
        if not isinstance(lane,StoryLaneConfig):raise ValueError('Choose a serialized-story lane')
        duration=context['outline']['duration_seconds'];validation=validation_duration is not None
        if validation:
            if not 180<=validation_duration<=240 or not Path(self.storage.db_path).is_relative_to(data_root()/'validation'):
                raise ValueError('180–240s validation overrides are confined to private AUTOMATION_DATA_DIR/validation paths')
            duration=validation_duration
        config=GenerationConfig(content_type='long_drama',script_provider='ollama',script_model=lane.planner.model or os.getenv('OLLAMA_MODEL','qwen3:8b'),
            video_provider='local_cinematic',video_model=lane.episode_render.model,video_input_mode='image',continuity_mode=lane.episode_render.continuity,
            cast_package_id=context['cast']['package_id'],narration='character_tts',captions='asr',duration_seconds=duration,
            aspect_ratio='16:9',resolution='720p',fps=24,max_shots=sum(math.ceil((duration//math.ceil(duration/180)+(i<duration%math.ceil(duration/180)))/10) for i in range(math.ceil(duration/180))),source_mode='topic',topic=context['series']['premise'][:1000],max_cost_usd=0,require_motion=True)
        # Wan-only needs explicit driving footage not supplied by the daily lane.
        if config.video_model!='ltx-2.5':raise ValueError('Daily episodes currently require LTX image conditioning; keep manual Wan/hybrid workflows separate')
        now=self.storage.timestamp();day=local_day(self.storage.clock,self.settings.config.timezone)
        payload={'configuration':config.model_dump(mode='json'),'bible_sha256':context['series']['bible_sha256'],'validation_only':validation}
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM automation_episode_runs WHERE run_id=?',(run_id,)).fetchone():return self.get(run_id)
            ep=c.execute('SELECT * FROM series_episodes WHERE series_id=? AND ep_no=?',(identifier,number)).fetchone()
            if ep['job_id'] or ep['script_plan_id'] or ep['status']!='outlined':raise ValueError('Episode already has production work; inspect the saved IDs')
            if not validation:
                forecast=self.gpu.forecast(day,[{'slot':1,'kind':'episode','category':'episode','duration_s':duration,'model':config.video_model,'input_mode':'image'}])
                reserved=0
                for saved in c.execute('SELECT request_json FROM automation_episode_runs WHERE plan_date=?',(day.isoformat(),)):
                    other=json.loads(saved[0])['configuration'];rate,_=self.gpu.rate(other['video_model'],'image')
                    reserved+=other['duration_seconds']*rate*1.25+120
                reserved+=sum(json.loads(r[0])['estimated_seconds'] for r in c.execute('SELECT forecast_json FROM automation_day_runs WHERE plan_date=?',(day.isoformat(),)))
                if not forecast['fits'] or forecast['items'][0].get('skipped') or reserved+forecast['estimated_seconds']>forecast['capacity_seconds']:
                    raise ValueError('Nightly GPU capacity is reserved; reuse the existing buffer or wait for the next window')
            c.execute('''INSERT OR IGNORE INTO automation_episode_runs(run_id,series_id,ep_no,request_json,memory_json,actor,plan_date,validation_only,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',(run_id,identifier,number,canonical_json(payload),canonical_json(context),actor,day.isoformat(),int(validation),now,now))
            stamp=int(time.time())
            c.execute("INSERT OR IGNORE INTO app_work_queue(work_id,kind,target_id,status,created_at,updated_at) VALUES (?,'automation',?,'queued',?,?)",(str(uuid4()),'episode:'+run_id,stamp,stamp))
        EventLog(self.storage).append('director','episode.queued','episode',run_id,{'series_id':identifier,'ep_no':number,'validation_only':validation,'provider_cost_usd':0})
        if launch:
            try:ShortsService.launch_drain(self,run_id)
            except ValueError:
                self.update(run_id,status='launch_failed',error='Queued intent retained; inspect worker launch');raise
        return self.get(run_id)

    def get(self,identifier:str)->dict:
        with self.storage.connect() as c:
            row=c.execute('SELECT * FROM automation_episode_runs WHERE run_id=?',(identifier,)).fetchone()
            if not row:raise ValueError('Unknown episode run')
            result=dict(row);result['request']=json.loads(result.pop('request_json'));result['memory']=json.loads(result.pop('memory_json'))
            result['receipts']=[dict(r) for r in c.execute('SELECT stage,attempt_no,status,errors_json FROM automation_episode_receipts WHERE run_id=? ORDER BY rowid',(identifier,))]
        return result

    def process(self,identifier:str)->None:
        row=self.get(identifier);claim,fresh=self.storage.claim('episode_execution',identifier,{'run_id':identifier})
        if not fresh:return
        self.update(identifier,status='running')
        try:
            # Reverify approved asset hashes before any inference, not just on enqueue.
            current=self.context(row['series_id'],row['ep_no'])
            if fingerprint(current['cast'])!=fingerprint(row['memory']['cast']):raise ValueError('Approved cast changed after episode reservation')
            result=self.producer(identifier,row)
            status=result.get('status')
            if status not in {'awaiting_quality_review','failed','needs_reconciliation'}:raise ValueError('Unexpected episode production state')
            self.update(identifier,status=status,error=result.get('error'),job_id=result.get('job_id'))
            with self.storage.connect() as c:c.execute('UPDATE series_episodes SET status=?,job_id=COALESCE(?,job_id),updated_at=? WHERE series_id=? AND ep_no=?',
                ('in_review' if status=='awaiting_quality_review' else status,result.get('job_id'),self.storage.timestamp(),row['series_id'],row['ep_no']))
            if status=='needs_reconciliation':self.storage.mark_uncertain(claim['claim_token'])
            else:self.storage.complete_claim(claim['claim_token'],{'status':status,'job_id':result.get('job_id')})
        except Exception as exc:
            uncertain=getattr(exc,'uncertain',not isinstance(exc,ValueError))
            self.update(identifier,status='needs_reconciliation' if uncertain else 'failed',error=str(exc)[:300] if isinstance(exc,ValueError) else 'Episode controller stopped; inspect saved task IDs')
            if uncertain:self.storage.mark_uncertain(claim['claim_token'])
        # Buffer progression is a subsequent durable intent, never eager GPU calls.
        self.tick(series_id=row['series_id'])

    def update(self,identifier:str,**values:Any)->None:
        allowed={'status','error','script_attempt_id','production_id','job_id'}
        if not set(values)<=allowed:raise ValueError('Invalid episode update')
        with self.storage.connect() as c:
            for key,value in values.items():
                if value is not None:c.execute(f'UPDATE automation_episode_runs SET {key}=?,updated_at=? WHERE run_id=?',(value,self.storage.timestamp(),identifier))

    def request(self,run_id:str,planner:Any,stage:str,prompt:str,model:type[Model],validate:Callable[[Model],list[str]],*,schema:dict | None=None)->Model:
        errors=[]
        for attempt in range(1,4):
            now=self.storage.timestamp();self.update(run_id,status=f'planning:{stage}:{attempt}')
            with self.storage.connect() as c:c.execute('INSERT INTO automation_episode_receipts VALUES (?,?,?,\'submitting\',NULL,\'[]\',?,?)',(run_id,stage,attempt,now,now))
            body=None;parsed=None
            try:
                body=planner.request(prompt+'\nCorrect these received validation errors: '+canonical_json(errors),schema or model.model_json_schema(),max_tokens=9000)
                with self.storage.connect() as c:c.execute("UPDATE automation_episode_receipts SET body_json=?,status='received' WHERE run_id=? AND stage=? AND attempt_no=?",(canonical_json(body),run_id,stage,attempt))
                parsed=model.model_validate(body);errors=validate(parsed)
            except ValidationError as exc:errors=[e['msg'] for e in exc.errors(include_input=False)]
            except json.JSONDecodeError:errors=['Acknowledged response was invalid JSON']
            except LocalRequestRejected:
                self._receipt(run_id,stage,attempt,'rejected',['Local schema rejected; explicit revision required']);raise
            except Exception:
                self._receipt(run_id,stage,attempt,'needs_reconciliation',['Local completion uncertain; no retry']);raise
            self._receipt(run_id,stage,attempt,'invalid' if errors else 'valid',errors)
            if not errors:return parsed
        raise ValueError('Episode local planning exhausted bounded received-output attempts at '+stage)

    def _receipt(self,run_id:str,stage:str,attempt:int,status:str,errors:list[str])->None:
        with self.storage.connect() as c:c.execute('UPDATE automation_episode_receipts SET status=?,errors_json=?,updated_at=? WHERE run_id=? AND stage=? AND attempt_no=?',(status,canonical_json(errors),self.storage.timestamp(),run_id,stage,attempt))

    def produce(self,identifier:str,row:dict)->dict:
        config=GenerationConfig.model_validate(row['request']['configuration']);context=row['memory'];start=time.monotonic()
        planner=self.planner or LocalJSONPlanner(config.script_model,context_size=16384,temperature=.9,repeat_penalty=1.2)
        request=lambda stage,prompt,model,validate,**kwargs:self.request(identifier,planner,stage,prompt,model,validate,**kwargs)
        adapter=EpisodePlanner(config,context,request)
        scripts=ScriptGenerationService(self.storage.db_path,local_factory=lambda model:adapter)
        script_id=scripts.submit(scripts.request(config,category='mixed',purpose='video'),'automation:episodes');self.update(identifier,script_attempt_id=script_id)
        scripts.run(script_id);draft=scripts.get(script_id)
        if draft['status']!='completed':return {'status':'needs_reconciliation' if draft['status']=='needs_reconciliation' else 'failed','error':draft.get('error')}
        plan=ProductionPlan.model_validate(draft['production_plan']);ending=hook_check(plan,context['outline']['hook'],context['packaging']['end_card'])
        state=state_from_script(context,plan,request)
        with self.storage.connect() as c:
            c.execute('UPDATE series_episodes SET script_plan_id=?,summary_text=?,state_after_json=?,status=\'scripted\',updated_at=? WHERE series_id=? AND ep_no=?',
                (script_id,state['summary'],canonical_json(state),self.storage.timestamp(),row['series_id'],row['ep_no']))
        return self.render_saved_episode(identifier,row,script_id,plan,config,start)

    def render_saved_episode(self,identifier:str,row:dict,script_id:str,plan:ProductionPlan,config:GenerationConfig,start:float)->dict:
        """Render an already validated/authorized local script; never regenerate it."""
        context=row['memory'];ending=hook_check(plan,context['outline']['hook'],context['packaging']['end_card'])
        if config.video_provider!='local_cinematic' or config.script_provider!='ollama':raise ValueError('Episode rendering is local-only')
        # Each single-actor scene retains the complete approved portrait in a
        # native-aspect frame; unmodified identity images stay in the cast package.
        videos=CloudVideoService(self.storage.db_path)
        saved_config,saved_plan=videos.script(script_id)
        if fingerprint(saved_config.model_dump(mode='json'))!=fingerprint(config.model_dump(mode='json')) or fingerprint(saved_plan.model_dump(mode='json'))!=fingerprint(plan.model_dump(mode='json')):
            raise ValueError('Saved screenplay/configuration changed; inspect before rendering')
        with self.storage.connect() as c:state_row=c.execute('SELECT state_after_json FROM series_episodes WHERE series_id=? AND ep_no=?',(row['series_id'],row['ep_no'])).fetchone()
        state=json.loads(state_row[0])
        if state.get('script_sha256')!=fingerprint(plan.model_dump(mode='json')):raise ValueError('Episode state does not belong to this screenplay')
        with self.storage.connect() as c:c.execute('UPDATE series SET story_state_json=?,updated_at=? WHERE series_id=?',(canonical_json(state|{'state_episode':row['ep_no']}),self.storage.timestamp(),row['series_id']))
        from app.automation.series.reference_frames import scene_frames
        from app.services.local_cinematic_models import dimensions
        frames=scene_frames(context['cast'],videos.references,videos.root/'series_reference_frames',dimensions(config.aspect_ratio,config.video_model))
        images={s.scene_number:frames[s.character_ids[0]] for s in plan.shots}
        quote=videos.quote(script_id,'automation:episodes',scene_image_reference_ids=images)
        if str(quote['estimate_usd']) not in {'0','0.0','0.00'}:raise ValueError('Episode automation only authorizes zero-cost local video quotes')
        production=videos.create(quote['quote_id'],'automation:episodes',True);self.update(identifier,production_id=production['production_id'],job_id=production['job_id'])
        with self.storage.connect() as c:c.execute('UPDATE series_episodes SET job_id=?,status=\'rendering\',updated_at=? WHERE series_id=? AND ep_no=?',(production['job_id'],self.storage.timestamp(),row['series_id'],row['ep_no']))
        ContentWorkflow(job_service=JobService(self.storage.db_path)).run(production['job_id'])
        deadline=time.monotonic()+8*3600
        while time.monotonic()<deadline:
            value=videos.process(production['production_id'],submit_queued=False)
            if value['status']=='technical_ready':
                ShortsService.record_metrics(self,value,config,production['job_id'],start)
                return {'status':'awaiting_quality_review','job_id':production['job_id'],'last30':ending}
            if value['status'] in {'needs_reconciliation','continuity_stale'}:return {'status':'needs_reconciliation','job_id':production['job_id'],'error':'Inspect existing shot IDs and continuity'}
            if value['status'] in {'failed','assembly_failed'}:
                return {'status':'needs_reconciliation' if any(s['status'] in {'processing','submitting','needs_reconciliation'} for s in value['shots']) else 'failed','job_id':production['job_id'],'error':value.get('error')}
            self.sleep(8)
        return {'status':'needs_reconciliation','job_id':production['job_id'],'error':'Episode deadline reached; inspect existing task IDs, never resubmit'}

    def sync_publications(self,identifier:str)->None:
        with self.storage.connect() as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'publications' not in tables:return
            # Uploaded/private/submitted are NOT proof of public publication.
            rows=c.execute("SELECT e.ep_no,p.status FROM series_episodes e JOIN publications p ON p.job_id=e.job_id AND p.platform='youtube' WHERE e.series_id=? AND e.status!='published'",(identifier,)).fetchall()
            for r in rows:
                if r['status']=='published':c.execute("UPDATE series_episodes SET status='published',actual_publish_at=COALESCE(actual_publish_at,?),updated_at=? WHERE series_id=? AND ep_no=?",(self.storage.timestamp(),self.storage.timestamp(),identifier,r['ep_no']))

    def publishable(self,identifier:str,number:int)->bool:
        with self.storage.connect() as c:
            rows=c.execute('SELECT ep_no,status FROM series_episodes WHERE series_id=? AND ep_no<? ORDER BY ep_no',(identifier,number)).fetchall()
        return len(rows)==number-1 and all(r['status'] in {'published','skipped'} for r in rows)

    def skip(self,identifier:str,number:int,actor:str,notes:str,confirmed:bool=False)->None:
        if confirmed is not True or len(notes.strip())<10:raise ValueError('Explicitly confirm a skip and provide a reason')
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM series_episodes WHERE series_id=? AND ep_no=?',(identifier,number)).fetchone()
            if not row or row['status']=='published':raise ValueError('Cannot skip missing or published episode')
            if row['summary_text']:
                # Already-written canon remains available for the next recap.
                # Skipping its public slot must not rewrite buffered memories.
                c.execute("UPDATE series_episodes SET status='skipped',updated_at=? WHERE series_id=? AND ep_no=?",(self.storage.timestamp(),identifier,number))
            else:
                previous=c.execute("SELECT state_after_json FROM series_episodes WHERE series_id=? AND ep_no<? AND summary_text!='' ORDER BY ep_no DESC LIMIT 1",(identifier,number)).fetchone()
                state=json.loads(previous[0]) if previous else {}
                bible=json.loads(c.execute('SELECT bible_json FROM series WHERE series_id=?',(identifier,)).fetchone()[0])
                state['skip_note']=notes
                state['deferred_payoffs']=sorted(set(state.get('deferred_payoffs',[]))|{t['thread_id'] for t in bible['threads'] if t['payoff_day']==number})
                state['deferred_plants']=sorted(set(state.get('deferred_plants',[]))|{t['thread_id'] for t in bible['threads'] if t['plant_day']==number})
                c.execute("UPDATE series_episodes SET status='skipped',summary_text=?,state_after_json=?,updated_at=? WHERE series_id=? AND ep_no=?",(f'Episode {number} explicitly skipped: {notes}. No missing screenplay events are asserted; catch up deferred threads in the next episode.',canonical_json(state),self.storage.timestamp(),identifier,number))
        EventLog(self.storage).append('user','episode.skipped','series',identifier,{'ep_no':number,'actor':actor,'notes':notes})

    def tick(self,*,series_id:str | None=None,launch:bool=False)->dict:
        day=local_day(self.storage.clock,self.settings.config.timezone);result={'date':day.isoformat(),'queued':[],'held':[],'rollovers':[]}
        if not self.settings.enabled:return result
        with self.storage.connect() as c:series=[dict(r) for r in c.execute("SELECT * FROM series WHERE status IN ('active','completed')"+(' AND series_id=?' if series_id else ''),(series_id,) if series_id else ())]
        for item in series:
            cfg=self.settings.config.lanes[item['lane']]
            if not cfg.enabled:continue
            self.sync_publications(item['series_id'])
            with self.storage.connect() as c:eps=[dict(r) for r in c.execute('SELECT * FROM series_episodes WHERE series_id=? ORDER BY ep_no',(item['series_id'],))]
            if len(eps)==30 and eps[-1]['status']=='published' and all(e['status'] in {'published','skipped'} for e in eps):
                self.rollover(item,cfg,day,result,published_at=eps[-1]['actual_publish_at']);continue
            if item['status']!='active':continue
            start=date.fromisoformat(item['start_date']) if item['start_date'] else day
            if not item['start_date']:
                with self.storage.connect() as c:c.execute('UPDATE series SET start_date=?,updated_at=? WHERE series_id=? AND start_date IS NULL',(start.isoformat(),self.storage.timestamp(),item['series_id']))
            if day<start:continue
            next_ep=next((e['ep_no'] for e in eps if e['status'] not in {'published','skipped'}),31)
            end=min(30,next_ep+cfg.buffer_ahead_episodes)
            for ep in eps:
                if not next_ep<=ep['ep_no']<=end or ep['status']!='outlined':continue
                with self.storage.connect() as c:old=c.execute('SELECT status FROM automation_episode_runs WHERE series_id=? AND ep_no=?',(item['series_id'],ep['ep_no'])).fetchone()
                if old:
                    if old[0] in {'failed','needs_reconciliation'}:result['held'].append({'series_id':item['series_id'],'ep_no':ep['ep_no'],'reason':old[0]})
                    if old[0]=='needs_reconciliation':break
                    continue
                if ep['ep_no']>1 and not eps[ep['ep_no']-2]['summary_text']:break
                forecast=self.gpu.forecast(day,[{'slot':1,'category':'episode','kind':'episode','duration_s':json.loads(ep['outline_json'])['duration_seconds'],'model':cfg.episode_render.model,'input_mode':'image'}])
                with self.storage.connect() as c:
                    reserved=sum(json.loads(r[0])['configuration']['duration_seconds']*self.gpu.rate(cfg.episode_render.model,'image')[0]*1.25+120 for r in c.execute('SELECT request_json FROM automation_episode_runs WHERE plan_date=?',(day.isoformat(),)))
                    shorts=[json.loads(r[0]) for r in c.execute('SELECT forecast_json FROM automation_day_runs WHERE plan_date=?',(day.isoformat(),))]
                remaining=forecast['capacity_seconds']-reserved-sum(r['estimated_seconds'] for r in shorts)
                if forecast['estimated_seconds']>remaining or not forecast['fits'] or forecast['items'][0].get('skipped'):
                    result['held'].append({'series_id':item['series_id'],'ep_no':ep['ep_no'],'reason':'GPU budget exhausted; reuse existing buffer'});break
                queued=self.enqueue(item['series_id'],ep['ep_no'],confirmed=True);result['queued'].append(queued['run_id']);break
        if launch and result['queued']:
            try:ShortsService.launch_drain(self,result['queued'][0])
            except ValueError:
                self.update(result['queued'][0],status='launch_failed',error='Queued intent retained; inspect worker launch');raise
        return result

    def rollover(self,item:dict,cfg:StoryLaneConfig,day:date,result:dict,*,published_at:str | None=None)->None:
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE');now=self.storage.timestamp()
            row=c.execute('SELECT * FROM automation_series_rollovers WHERE series_id=?',(item['series_id'],)).fetchone()
            if not row:
                publication_day=datetime.fromisoformat(published_at).astimezone(ZoneInfo(self.settings.config.timezone)).date() if published_at else day
                eligible=publication_day+timedelta(days=cfg.next_series_gap_days)
                c.execute("UPDATE series SET status='completed',updated_at=? WHERE series_id=?",(now,item['series_id']))
                c.execute('INSERT INTO automation_series_rollovers VALUES (?,?,NULL,?,?,?)',(item['series_id'],stable_id('series_rollover_request',item['series_id']),eligible.isoformat(),now,now))
                row=c.execute('SELECT * FROM automation_series_rollovers WHERE series_id=?',(item['series_id'],)).fetchone()
        if day>=date.fromisoformat(row['eligible_date']) and not row['next_series_id']:
            next_series=SeriesService(self.storage.db_path,self.settings,clock=self.storage.clock).enqueue(row['next_request_id'],genre=item['genre'],lane=item['lane'],actor='automation:series',confirmed=True)
            with self.storage.connect() as c:c.execute('UPDATE automation_series_rollovers SET next_series_id=?,updated_at=? WHERE series_id=?',(next_series['series_id'],self.storage.timestamp(),item['series_id']))
            result['rollovers'].append(next_series['series_id'])
            # New series planning remains queued; its cast can NEVER self-approve.
