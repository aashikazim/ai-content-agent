"""Generation-only day orchestration, serialized through the existing durable queue."""
from datetime import date,datetime,timezone,timedelta
from zoneinfo import ZoneInfo
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any,Callable
from uuid import uuid4
from app.automation.config import AutomationSettings,Recipe,ShortsLaneConfig,load_settings
from app.automation.events import EventLog
from app.automation.gpu_planner import GPUPlanner
from app.automation.idempotency import canonical_json,daily_slot_id,stable_id
from app.automation.lanes.shorts import ShortsPlanner,recipe_configuration
from app.automation.reports import data_root,read_day,write_report
from app.automation.selector import TopicSelector
from app.automation.storage import AutomationStorage
from app.services.script_generation_service import ScriptGenerationService
from app.services.cloud_video_service import CloudVideoService
from app.services.job_service import JobService
from app.services.work_queue import WorkQueue
from app.workflows.content_workflow import ContentWorkflow

logger=logging.getLogger(__name__)


def backup_before_upgrade(path: str | Path) -> None:
    source=Path(path).absolute()
    if not source.exists():return
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as conn:
        names={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        from app.automation.schema import AUTOMATION_TABLES
        if AUTOMATION_TABLES<=names:return
        folder=data_root()/'backups'/str(uuid4());folder.mkdir(parents=True,mode=0o700)
        with sqlite3.connect(folder/'before-automation.db') as output:conn.backup(output)
        (folder/'before-automation.db').chmod(0o600)


class ShortsService:
    def __init__(self,db_path: str | Path,settings: AutomationSettings | None=None,*,clock: Any=None,
                 producer: Callable[...,dict[str,Any]] | None=None,sleep: Callable[[float],None]=time.sleep) -> None:
        backup_before_upgrade(db_path)
        self.storage=AutomationStorage(db_path,clock=clock)
        self.settings=settings or load_settings();self.queue=WorkQueue(db_path)
        self.gpu=GPUPlanner(self.storage,self.settings.config)
        self.producer=producer or self.produce_slot;self.sleep=sleep

    def enqueue_day(self,day: date,lane_id: str='shorts_daily',actor: str='local-owner',*,confirmed: bool=False,launch: bool=False,region: str='US') -> dict[str,Any]:
        if not self.settings.enabled or confirmed is not True:raise ValueError('Enable automation and explicitly confirm local rendering')
        lane=self.settings.config.lanes.get(lane_id)
        if not isinstance(lane,ShortsLaneConfig) or not lane.enabled:raise ValueError('Choose an enabled Shorts lane')
        existing=read_day(self.storage.db_path,day,lane_id)
        if existing['run']:return existing
        preview=TopicSelector(self.storage.db_path,self.storage.clock).preview(lane,region)
        if len(preview['topics'])!=lane.daily_count:raise ValueError('Not enough fresh topics; collect more signals before creating a day')
        items=[]
        for index,topic in enumerate(preview['topics'],1):
            recipe=lane.recipes.get(topic['category']) or lane.recipes['wildcard']
            recipe_configuration(recipe,topic)  # Reject unsupported/paid routes before reserving work.
            items.append({'slot':index,'category':topic['category'],'duration_s':recipe.duration_s,'model':recipe.model,'input_mode':recipe.input_mode})
        forecast=self.gpu.forecast(day,items)
        if not forecast['fits']:raise ValueError('Day cannot fit the configured GPU window/degradation policy')
        run_id=stable_id('shorts_day',day.isoformat(),lane_id)
        now=self.storage.timestamp();target='shorts:'+run_id
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM automation_day_runs WHERE run_id=?',(run_id,)).fetchone():return read_day(self.storage.db_path,day,lane_id)
            cutoff=(self.storage.clock.now()-timedelta(days=lane.cooldown_days)).isoformat()
            if any(conn.execute('SELECT 1 FROM topic_candidates WHERE topic_id=? AND last_used_at>=?',(t['topic_id'],cutoff)).fetchone() for t in preview['topics']):
                raise ValueError('Topics were reserved concurrently; refresh preview before another request')
            conn.execute('''INSERT INTO automation_day_runs(run_id,plan_date,lane,request_json,backup_topics_json,forecast_json,actor,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',(run_id,day.isoformat(),lane_id,canonical_json(lane.model_dump(mode='json')),canonical_json(preview['backups']),canonical_json(forecast),actor,now,now))
            for index,(topic,planned) in enumerate(zip(preview['topics'],forecast['items']),1):
                recipe=(lane.recipes.get(topic['category']) or lane.recipes['wildcard']).model_copy(update={'duration_s':planned['duration_s']})
                payload={'topic':topic,'configuration':recipe.model_dump(mode='json')}
                conn.execute('''INSERT INTO topic_candidates(topic_id,normalized_topic,category,rank_score,signal_ids_json,status,last_used_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,'selected',?,?,?) ON CONFLICT(topic_id) DO UPDATE SET status='selected',last_used_at=excluded.last_used_at,updated_at=excluded.updated_at''',
                    (topic['topic_id'],topic['normalized_topic'],topic['category'],topic['score'],canonical_json(topic['signal_ids']),now,now,now))
                conn.execute('''INSERT INTO daily_plans(daily_plan_id,plan_date,lane,slot,topic_id,recipe,status,planned_publish_at,notes,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(daily_slot_id(day,lane_id,index),day.isoformat(),lane_id,index,topic['topic_id'],canonical_json(payload),
                    'skipped' if planned.get('skipped') else 'planned',datetime.fromisoformat(day.isoformat()+'T'+lane.publish_slots[index-1]).replace(tzinfo=ZoneInfo(self.settings.config.timezone)).astimezone(timezone.utc).isoformat(),'; '.join(planned['degradations']),now,now))
            stamp=int(time.time())
            conn.execute("INSERT INTO app_work_queue(work_id,kind,target_id,status,created_at,updated_at) VALUES (?,'automation',?,'queued',?,?) ON CONFLICT(kind,target_id) DO NOTHING",(str(uuid4()),target,stamp,stamp))
        EventLog(self.storage).append('director','day.queued','daily_plan',run_id,{'slots':lane.daily_count,'provider_cost_usd':0})
        write_report(self.storage.db_path,day,lane_id)
        if launch:self.launch_drain(run_id)
        return read_day(self.storage.db_path,day,lane_id)

    def launch_drain(self,run_id: str) -> None:
        if os.getenv('DURABLE_WORKERS','false').lower()=='true':return
        folder=data_root()/'worker-logs';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        env=dict(os.environ,DATABASE_PATH=self.storage.db_path)
        try:
            with (folder/(run_id+'.log')).open('ab') as log:
                subprocess.Popen([sys.executable,'-m','app.workers.application_worker','--drain'],cwd=Path(__file__).parents[2],env=env,
                    stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        except OSError:
            with self.storage.connect() as conn:conn.execute("UPDATE automation_day_runs SET status='launch_failed',updated_at=? WHERE run_id=?",(self.storage.timestamp(),run_id))
            raise ValueError('Worker launch failed; queued work is retained for explicit inspection') from None

    def process_day(self,run_id: str) -> None:
        with self.storage.connect() as conn:run=conn.execute('SELECT * FROM automation_day_runs WHERE run_id=?',(run_id,)).fetchone()
        if not run:raise ValueError('Unknown automation day')
        day=date.fromisoformat(run['plan_date']);lane=ShortsLaneConfig.model_validate_json(run['request_json'])
        claim,fresh=self.storage.claim('shorts_day_execution',run_id,{'run_id':run_id})
        if not fresh:return  # A crash never grants another execution token.
        with self.storage.connect() as conn:conn.execute("UPDATE automation_day_runs SET status='running',updated_at=? WHERE run_id=?",(self.storage.timestamp(),run_id))
        started=time.monotonic();capacity=json.loads(run['forecast_json'])['capacity_seconds']
        try:
            for row in read_day(self.storage.db_path,day,run['lane'])['slots']:
                if row['status']=='skipped':continue
                topic=row['recipe']['topic'];recipe=Recipe.model_validate(row['recipe']['configuration'])
                rate,_=self.gpu.rate(recipe.model,recipe.input_mode)
                if time.monotonic()-started+recipe.duration_s*rate*1.25+120>capacity:
                    with self.storage.connect() as conn:conn.execute("UPDATE daily_plans SET status='skipped',notes=notes||' Remaining nightly capacity exhausted; no submission.' WHERE daily_plan_id=?",(row['daily_plan_id'],))
                    continue
                for attempt in (0,1):
                    now=self.storage.timestamp()
                    with self.storage.connect() as conn:
                        conn.execute('''INSERT INTO automation_slot_attempts(daily_plan_id,attempt_no,topic_json,status,created_at,updated_at)
                            VALUES (?,?,?,'starting',?,?)''',(row['daily_plan_id'],attempt,canonical_json(topic),now,now))
                        conn.execute("UPDATE daily_plans SET status='rendering',updated_at=? WHERE daily_plan_id=?",(now,row['daily_plan_id']))
                    try:result=self.producer(row['daily_plan_id'],attempt,topic,recipe)
                    except Exception as exc:
                        # A controller exception may have crossed submission; no blind substitution.
                        result={'status':'needs_reconciliation','error':'Generation controller stopped; inspect saved IDs before recovery','error_type':type(exc).__name__}
                    status=result['status']
                    if status not in {'awaiting_quality_review','failed','needs_reconciliation'}:
                        status='needs_reconciliation';result['error']='Unexpected generation state; inspect existing IDs'
                    uncertain=status=='needs_reconciliation'
                    with self.storage.connect() as conn:
                        conn.execute('''UPDATE automation_slot_attempts SET status=?,error=?,job_id=COALESCE(?,job_id),updated_at=?
                            WHERE daily_plan_id=? AND attempt_no=?''',(status,result.get('error'),result.get('job_id'),self.storage.timestamp(),row['daily_plan_id'],attempt))
                        conn.execute('''UPDATE daily_plans SET status=?,job_id=COALESCE(?,job_id),notes=notes||?,updated_at=? WHERE daily_plan_id=?''',
                            ('in_review' if status=='awaiting_quality_review' else 'needs_reconciliation' if uncertain else 'failed',result.get('job_id'),
                             ' '+(result.get('error') or ''),self.storage.timestamp(),row['daily_plan_id']))
                    if status=='awaiting_quality_review':
                        with self.storage.connect() as conn:conn.execute("UPDATE topic_candidates SET status='used',updated_at=? WHERE topic_id=?",(self.storage.timestamp(),topic['topic_id']))
                        break
                    if uncertain:raise RuntimeError('Uncertain work holds the day; no automatic retry')
                    replacement=self.next_backup(run_id) if attempt==0 else None
                    if not replacement:
                        with self.storage.connect() as conn:conn.execute("UPDATE daily_plans SET status='skipped',notes=notes||' Bounded substitution exhausted.' WHERE daily_plan_id=?",(row['daily_plan_id'],))
                        break
                    topic=replacement;recipe=lane.recipes.get(topic['category']) or lane.recipes['wildcard']
                    recipe=recipe.model_copy(update={'duration_s':min(recipe.duration_s,row['recipe']['configuration']['duration_s'])})
                    with self.storage.connect() as conn:conn.execute('UPDATE daily_plans SET topic_id=?,recipe=?,notes=notes||? WHERE daily_plan_id=?',
                        (topic['topic_id'],canonical_json({'topic':topic,'configuration':recipe.model_dump(mode='json')}),' Substituted one backup topic after known technical failure.',row['daily_plan_id']))
                write_report(self.storage.db_path,day,run['lane'])
            with self.storage.connect() as conn:conn.execute("UPDATE automation_day_runs SET status='completed',updated_at=? WHERE run_id=?",(self.storage.timestamp(),run_id))
            self.storage.complete_claim(claim['claim_token'],{'status':'completed'})
        except Exception:
            self.storage.mark_uncertain(claim['claim_token'])
            with self.storage.connect() as conn:conn.execute("UPDATE automation_day_runs SET status='needs_reconciliation',updated_at=? WHERE run_id=?",(self.storage.timestamp(),run_id))
        finally:write_report(self.storage.db_path,day,run['lane'])

    def next_backup(self,run_id: str) -> dict[str,Any] | None:
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            run=conn.execute('SELECT * FROM automation_day_runs WHERE run_id=?',(run_id,)).fetchone()
            used=json.loads(run['used_backups_json'])
            from app.automation.selector import similar
            cooldown=ShortsLaneConfig.model_validate_json(run['request_json']).cooldown_days
            cutoff=(self.storage.clock.now()-timedelta(days=cooldown)).isoformat()
            recent=[r[0] for r in conn.execute('SELECT normalized_topic FROM topic_candidates WHERE last_used_at>=?',(cutoff,))]
            topic=next((t for t in json.loads(run['backup_topics_json']) if t['topic_id'] not in used and not any(similar(t['title'],r) for r in recent)),None)
            if topic:
                used.append(topic['topic_id']);now=self.storage.timestamp()
                conn.execute('UPDATE automation_day_runs SET used_backups_json=?,updated_at=? WHERE run_id=?',(canonical_json(used),now,run_id))
                conn.execute('''INSERT INTO topic_candidates(topic_id,normalized_topic,category,rank_score,signal_ids_json,status,last_used_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,'selected',?,?,?) ON CONFLICT(topic_id) DO UPDATE SET last_used_at=excluded.last_used_at,status='selected' ''',
                    (topic['topic_id'],topic['normalized_topic'],topic['category'],topic['score'],canonical_json(topic['signal_ids']),now,now,now))
            return topic

    def link(self,identifier: str,attempt: int,**values: Any) -> None:
        allowed={'script_attempt_id','production_id','job_id'}
        if not set(values)<=allowed:raise ValueError('Invalid slot linkage')
        with self.storage.connect() as conn:
            for key,value in values.items():conn.execute(f'UPDATE automation_slot_attempts SET {key}=?,updated_at=? WHERE daily_plan_id=? AND attempt_no=?',(value,self.storage.timestamp(),identifier,attempt))

    def produce_slot(self,identifier: str,attempt: int,topic: dict[str,Any],recipe: Recipe) -> dict[str,Any]:
        config=recipe_configuration(recipe,topic);start=time.monotonic()
        def receipt(number: int,body: dict[str,Any],errors: list[str]) -> None:
            now=self.storage.timestamp()
            with self.storage.connect() as conn:
                conn.execute('''INSERT INTO automation_short_receipts VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(daily_plan_id,attempt_no,receipt_no) DO UPDATE SET errors_json=excluded.errors_json,updated_at=excluded.updated_at''',
                    (identifier,attempt,number,canonical_json(body),canonical_json(errors),now,now))
        scripts=ScriptGenerationService(self.storage.db_path,local_factory=lambda model:ShortsPlanner(config,topic,recipe,receipt=receipt))
        request=scripts.request(config,category='mixed',purpose='video')
        script_id=scripts.submit(request,'automation:shorts');self.link(identifier,attempt,script_attempt_id=script_id)
        scripts.run(script_id);draft=scripts.get(script_id)
        if draft['status']!='completed':return {'status':'needs_reconciliation' if draft['status']=='needs_reconciliation' else 'failed','error':draft.get('error') or 'Local screenplay did not validate'}
        videos=CloudVideoService(self.storage.db_path);quote=videos.quote(script_id,'automation:shorts')
        if quote.get('estimate_usd') not in ('0','0.0',0):raise ValueError('Automation cannot authorize a paid video quote')
        production=videos.create(quote['quote_id'],'automation:shorts',True)
        production_id=production['production_id'];job_id=production['job_id']
        self.link(identifier,attempt,production_id=production_id,job_id=job_id)
        with self.storage.connect() as conn:conn.execute('UPDATE daily_plans SET job_id=? WHERE daily_plan_id=?',(job_id,identifier))
        ContentWorkflow(job_service=JobService(self.storage.db_path)).run(job_id)
        deadline=time.monotonic()+4*3600
        while time.monotonic()<deadline:
            value=videos.process(production_id,submit_queued=False)
            if value['status']=='technical_ready':
                self.record_metrics(value,config,job_id,start)
                return {'status':'awaiting_quality_review','job_id':job_id}
            if value['status'] in {'needs_reconciliation','continuity_stale'}:return {'status':'needs_reconciliation','job_id':job_id,'error':value.get('error') or 'Inspect generation state'}
            if value['status'] in {'failed','assembly_failed'}:
                self.record_metrics(value,config,job_id,start)
                if any(s['status'] in {'submitting','processing','needs_reconciliation'} for s in value['shots']):
                    return {'status':'needs_reconciliation','job_id':job_id,'error':'Failed batch contains active/uncertain tasks; no substitution'}
                videos.discard_queued(production_id,True)
                return {'status':'failed','job_id':job_id,'error':value.get('error') or 'Technical generation failed'}
            self.sleep(8)
        return {'status':'needs_reconciliation','job_id':job_id,'error':'Local batch deadline reached; inspect existing workers and IDs, never resubmit'}


    def record_metrics(self,value: dict[str,Any],config: Any,job_id: str,start: float) -> None:
        selected=[s for s in value['shots'] if s['selected'] and (s.get('usage') or {}).get('ltx_seconds',0)+(s.get('usage') or {}).get('wan_seconds',0)>0]
        neural=sum((s.get('usage') or {}).get('ltx_seconds',0)+(s.get('usage') or {}).get('wan_seconds',0) for s in selected)
        queue=sum((s.get('usage') or {}).get('queue_wait_seconds',0) for s in selected)
        numbers={s['shot_number'] for s in selected}
        generated=sum(s['duration_seconds'] for s in value['production_plan']['shots'] if s['shot_number'] in numbers)
        duration=(value.get('quality') or {}).get('duration_seconds',generated)
        if neural>0 and duration>0:self.gpu.record(job_id,config.video_model,config.video_input_mode,duration,neural,queue,time.monotonic()-start)


def dispatch_automation(target: str) -> None:
    if target.startswith('shorts:'):
        ShortsService(os.getenv('DATABASE_PATH','data/trends.db')).process_day(target.split(':',1)[1]);return
    raise ValueError('Unknown automation generation task')
