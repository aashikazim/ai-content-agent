"""Original story planning with durable receipts and an explicit human cast gate."""
from datetime import date
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any
from uuid import UUID,uuid4
from pydantic import ValidationError
from app.automation.config import StoryLaneConfig,load_settings
from app.automation.events import EventLog
from app.automation.idempotency import canonical_json,episode_id,fingerprint,stable_id
from app.automation.local_planner import LocalJSONPlanner,LocalRequestRejected
from app.automation.reports import data_root
from app.automation.series.models import Bible,Episode,OutlineChunk,Premises,phase,chunk_schema
from app.automation.series.validators import normalized,validate_outline
from app.automation.series.repairs import hook_repair
from app.automation.shorts_service import backup_before_upgrade
from app.automation.storage import AutomationStorage,IdempotencyConflict
from app.services.work_queue import WorkQueue


class PlanningUncertain(RuntimeError):
    """A local request crossed the submission boundary without acknowledgment."""


def read_series(db_path: str | Path,identifier:str | None=None)->Any:
    path=Path(db_path).absolute()
    if not path.exists():return None if identifier else []
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_series_requests'").fetchone():return None if identifier else []
        sql='''SELECT s.*,r.status AS planning_status,r.stage,r.error FROM series s
            JOIN automation_series_requests r USING(series_id)'''
        if not identifier:return [dict(r) for r in conn.execute(sql+' ORDER BY s.created_at DESC LIMIT 100')]
        row=conn.execute(sql+' WHERE s.series_id=?',(identifier,)).fetchone()
        if not row:return None
        result=dict(row)
        result['bible']=json.loads(result.pop('bible_json'));result['cast_package_ids']=json.loads(result.pop('cast_package_ids_json'))
        result['story_state']=json.loads(result.pop('story_state_json'))
        result['episodes']=[json.loads(r[0]) for r in conn.execute('SELECT outline_json FROM series_episodes WHERE series_id=? ORDER BY ep_no',(identifier,))]
        result['receipts']=[dict(r) for r in conn.execute('SELECT stage,attempt_no,status,errors_json FROM automation_series_receipts WHERE series_id=? ORDER BY rowid',(identifier,))]
        for r in result['receipts']:r['errors']=json.loads(r.pop('errors_json'))
        return result


class SeriesService:
    def __init__(self,db_path,settings=None,*,planner=None,cast_service=None,clock=None):
        self.settings=settings or load_settings()
        backup_before_upgrade(db_path)
        self.storage=AutomationStorage(db_path,clock=clock);self.queue=WorkQueue(db_path)
        self.planner=planner;self.cast_service=cast_service

    def enqueue(self,request_id:str,*,genre:str='mystery drama',premise:str='',lane:str='story_series',actor:str='local-owner',confirmed:bool=False,launch:bool=False,source_series_id:str | None=None):
        request_id=str(UUID(request_id));source=None
        source_series_id=str(UUID(source_series_id)) if source_series_id else None
        # An acknowledged intent is immutable even if its source later changes.
        with self.storage.connect() as conn:existing=conn.execute('SELECT * FROM automation_series_requests WHERE request_id=?',(request_id,)).fetchone()
        if existing:
            original=json.loads(existing['request_json'])
            if existing['actor']!=actor or original.get('source_series_id')!=source_series_id or (not source_series_id and any(original[k]!=v for k,v in [('genre',genre.strip()),('premise',premise.strip()),('lane',lane)])):
                raise IdempotencyConflict('Request ID already belongs to different settings or actor')
            if confirmed is not True:raise ValueError('Confirm local planning before repeating an intent')
            return read_series(self.storage.db_path,existing['series_id'])
        if source_series_id:
            source_series_id=str(UUID(source_series_id));source=read_series(self.storage.db_path,source_series_id)
            if not source or source['planning_status']!='failed' or not source['bible'].get('characters'):
                raise ValueError('Only a known-failed outline with a valid frozen bible can be revised; reconcile uncertain requests first')
            with self.storage.connect() as conn:
                parent=conn.execute('SELECT actor,request_json FROM automation_series_requests WHERE series_id=?',(source_series_id,)).fetchone()
                if parent['actor']!=actor:raise IdempotencyConflict('Source series belongs to another actor')
                if conn.execute("SELECT 1 FROM series_episodes WHERE series_id=? AND (status!='outlined' OR job_id IS NOT NULL OR script_plan_id IS NOT NULL)",(source_series_id,)).fetchone():raise ValueError('Cannot revise produced episodes')
            bible=Bible.model_validate(source['bible'])
            prefix=[Episode.model_validate(e) for e in source['episodes']]
            if fingerprint(bible.model_dump())!=source['bible_sha256'] or len(prefix)%5 or len(prefix)>=30:raise ValueError('Source bible/prefix changed or is not resumable')
            if prefix and validate_outline(bible,prefix,complete=False):raise ValueError('Source outline prefix is invalid')
            genre,premise,lane=source['genre'],source['premise'],source['lane']
        config=self.settings.config.lanes.get(lane)
        if not isinstance(config,StoryLaneConfig):raise ValueError('Choose a serialized-story lane')
        if confirmed is not True:raise ValueError('Confirm local story planning; no rendering or publishing is authorized')
        if not 2<=len(genre.strip())<=100 or len(premise)>2000 or (premise and len(premise.strip())<40):raise ValueError('Provide a genre and, optionally, an original premise of 40–2000 characters')
        config=config.model_copy(update={'planner':config.planner.model_copy(update={'model':config.planner.model or os.getenv('OLLAMA_MODEL','qwen3:8b')})})
        payload={'genre':genre.strip(),'premise':premise.strip(),'lane':lane,'config':config.model_dump(mode='json'),'source_series_id':source_series_id}
        identifier=stable_id('series_request',request_id);now=self.storage.timestamp()
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT * FROM automation_series_requests WHERE request_id=?',(request_id,)).fetchone()
            if old:
                original=json.loads(old['request_json'])
                if old['actor']!=actor or any(original.get(k)!=payload.get(k) for k in ['genre','premise','lane','source_series_id']):raise IdempotencyConflict('Request ID already belongs to different settings or actor')
                return read_series(self.storage.db_path,old['series_id'])
            conn.execute('''INSERT INTO series(series_id,lane,title,genre,premise,created_at,updated_at)
                VALUES (?,?,?,?,'',?,?)''',(identifier,lane,'Planning original series',genre.strip(),now,now))
            if source:
                conn.execute('UPDATE series SET title=?,premise=?,bible_json=?,bible_sha256=? WHERE series_id=?',(source['title'],source['premise'],canonical_json(source['bible']),source['bible_sha256'],identifier))
                for ep in source['episodes']:
                    conn.execute('INSERT INTO series_episodes(episode_id,series_id,ep_no,outline_json,created_at,updated_at) VALUES (?,?,?,?,?,?)',(episode_id(identifier,ep['ep_no']),identifier,ep['ep_no'],canonical_json(ep),now,now))
            conn.execute('''INSERT INTO automation_series_requests(series_id,request_id,request_json,actor,created_at,updated_at)
                VALUES (?,?,?,?,?,?)''',(identifier,request_id,canonical_json(payload),actor,now,now))
            stamp=int(time.time())
            conn.execute("INSERT INTO app_work_queue(work_id,kind,target_id,status,created_at,updated_at) VALUES (?,'automation',?,'queued',?,?)",(str(uuid4()),'series:'+identifier,stamp,stamp))
        EventLog(self.storage).append('user','series.queued','series',identifier,{'actor':actor,'provider':'ollama','cost_usd':0})
        if launch:self.launch(identifier)
        return read_series(self.storage.db_path,identifier)

    def launch(self,identifier):
        if os.getenv('DURABLE_WORKERS','false').lower()=='true':return
        folder=data_root()/'worker-logs';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            with (folder/(identifier+'.log')).open('ab') as log:
                subprocess.Popen([sys.executable,'-m','app.workers.application_worker','--drain'],cwd=Path(__file__).parents[3],
                    env=dict(os.environ,DATABASE_PATH=self.storage.db_path),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        except OSError:
            self._state(identifier,'launch_failed','queue','Worker launch failed; existing queued intent is retained')
            raise ValueError('Worker launch failed; inspect the retained queue intent') from None

    def _state(self,identifier,status,stage,error=None):
        with self.storage.connect() as c:c.execute('UPDATE automation_series_requests SET status=?,stage=?,error=?,updated_at=? WHERE series_id=?',(status,stage,error,self.storage.timestamp(),identifier))

    def _request(self,identifier,stage,prompt,model,validate,planner,*,schema=None,transform=None,repair=None):
        errors=[]
        for attempt in range(1,4):
            now=self.storage.timestamp();self._state(identifier,'running',stage)
            with self.storage.connect() as c:c.execute('INSERT INTO automation_series_receipts VALUES (?,?,?,\'submitting\',NULL,\'[]\',?,?)',(identifier,stage,attempt,now,now))
            body=None;parsed=None
            try:
                body=planner.request(prompt+'\nFINAL REQUIREMENT: Correct these exact previous validation failures; never repeat the invalid fields unchanged: '+canonical_json(errors),schema or model.model_json_schema(),max_tokens=12000)
            except LocalRequestRejected as exc:
                with self.storage.connect() as c:c.execute("UPDATE automation_series_receipts SET status='rejected',errors_json=?,updated_at=? WHERE series_id=? AND stage=? AND attempt_no=?",(canonical_json([str(exc)]),self.storage.timestamp(),identifier,stage,attempt))
                raise ValueError(str(exc)) from None
            except json.JSONDecodeError:
                errors=['Received invalid JSON (local result acknowledged); return the exact schema']
            except Exception:
                with self.storage.connect() as c:c.execute("UPDATE automation_series_receipts SET status='needs_reconciliation',errors_json=?,updated_at=? WHERE series_id=? AND stage=? AND attempt_no=?",(canonical_json(['Local request completion uncertain; no automatic retry']),self.storage.timestamp(),identifier,stage,attempt))
                raise PlanningUncertain('Local request completion uncertain') from None
            else:
                # Persist acknowledged output BEFORE parsing or validating it.
                with self.storage.connect() as c:c.execute("UPDATE automation_series_receipts SET body_json=?,status='received' WHERE series_id=? AND stage=? AND attempt_no=?",(canonical_json(body),identifier,stage,attempt))
                try:
                    parsed=model.model_validate(transform(body) if transform else body);errors=validate(parsed)
                except ValidationError as exc:errors=[f"{'.'.join(map(str,e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False,include_url=False)]
            with self.storage.connect() as c:c.execute('UPDATE automation_series_receipts SET status=?,errors_json=?,updated_at=? WHERE series_id=? AND stage=? AND attempt_no=?',('invalid' if errors else 'valid',canonical_json(errors),self.storage.timestamp(),identifier,stage,attempt))
            if not errors:return parsed
            narrowed=repair(parsed,errors) if repair and parsed is not None else None
            if narrowed:prompt,schema,transform=narrowed
            else:prompt+='\nPrevious received candidate (data): '+canonical_json(body)
        raise ValueError('Local planning exhausted three received-output attempts at '+stage+'; owner revision required')

    def process(self,identifier):
        with self.storage.connect() as c:row=c.execute('SELECT * FROM automation_series_requests WHERE series_id=?',(identifier,)).fetchone()
        if not row:raise ValueError('Unknown series')
        claim,fresh=self.storage.claim('series_planning_execution',identifier,{'series_id':identifier})
        if not fresh:return
        request=json.loads(row['request_json']);config=StoryLaneConfig.model_validate(request['config'])
        planner=self.planner or LocalJSONPlanner(config.planner.model,context_size=16384,temperature=.9,repeat_penalty=1.2)
        try:
            current=read_series(self.storage.db_path,identifier)
            if request.get('source_series_id'):
                bible=Bible.model_validate(current['bible']);premise=current['premise']
                episodes=[Episode.model_validate(e) for e in current['episodes']]
            else:
                with self.storage.connect() as c:
                    past=[dict(r) for r in c.execute("SELECT title,premise FROM series WHERE status!='planning' AND series_id!=?",(identifier,))]
                    themes=[r[0] for r in c.execute('SELECT title FROM trend_signals ORDER BY collected_at DESC LIMIT 12')]
                def unique(value):
                    texts=[normalized(x.premise) for x in value.candidates];titles=[normalized(x.title) for x in value.candidates]
                    if len(set(titles))!=3:return ['Produce three different titles']
                    for i,text in enumerate(texts):
                        if any(SequenceMatcher(None,text,old).ratio()>.8 for old in texts[:i]):return ['Premise candidates are near-duplicates']
                        if any(normalized(old['title'])==titles[i] or SequenceMatcher(None,text,normalized(old['premise'])).ratio()>.8 for old in past):return ['Premise duplicates a previous series']
                    return []
                premises=self._request(identifier,'premises','Create three DISTINCT original fictional 30-day series premises, not adaptations. Genre '+request['genre']+'. Owner premise (use as candidate 1 if given): '+request['premise']+'\nUntrusted current themes: '+canonical_json(themes)+'\nAvoid past titles/premises: '+canonical_json(past[-30:]),Premises,unique,planner)
                chosen=premises.candidates[0]
                premise=request['premise'] or chosen.premise
                with self.storage.connect() as c:c.execute('UPDATE series SET title=?,premise=?,updated_at=? WHERE series_id=?',(chosen.title,premise,self.storage.timestamp(),identifier))
                bible=self._request(identifier,'bible','Create the frozen story bible for '+chosen.title+': '+premise+'. Exactly 2 recurring actors present for the whole series: introduced=1 and removed_after=30 for EACH actor. Original names and lowercase ASCII IDs. Specific appearance, wardrobe, voice, motivation and secret. At least 2 distinct mystery threads, planted before their explicit payoff day. Reserve a payoff for day30. Character introduction/removal days bound appearances. 30 episodes, setup1–8, escalation9–22, climax23–29, resolution30. Compact descriptions. World rules are strings.',Bible,lambda _:[],planner)
                with self.storage.connect() as c:c.execute('UPDATE series SET bible_json=?,bible_sha256=?,updated_at=? WHERE series_id=?',(canonical_json(bible.model_dump()),fingerprint(bible.model_dump()),self.storage.timestamp(),identifier))
                episodes=[]
            duration=(config.episode_duration_s.min+config.episode_duration_s.max)//2
            for start in range(len(episodes)+1,31,5):
                def compile_chunk(chunk):
                    compiled=[]
                    for i,(n,draft) in enumerate(zip(range(start,start+5),chunk.episodes)):
                        prior=episodes[-1].hook if i==0 and episodes else chunk.episodes[i-1].hook if i else ''
                        fields=draft.model_dump()
                        # A literal recap guarantees that the opening actually
                        # acknowledges the prior cliffhanger, even with a small LLM.
                        if prior:fields['opening_ack']='Previously: '+prior
                        compiled.append(Episode(**fields,ep_no=n,phase=phase(n),duration_seconds=duration,previous_hook=prior))
                    return compiled
                context={'bible':bible.model_dump(),'premise':premise,'previous_episodes':[e.model_dump() for e in episodes[-3:]],
                    'days':[{'day':n,'phase':phase(n),'required_plant':[t.thread_id for t in bible.threads if t.plant_day==n],
                    'required_payoff':[t.thread_id for t in bible.threads if t.payoff_day==n]} for n in range(start,start+5)],
                    'resolved_threads':sorted({t for ep in episodes for t in ep.resolves}),'used_hooks':[ep.hook for ep in episodes]}
                schema=chunk_schema(bible,start)
                chunk=self._request(identifier,'outline_'+str(start),
                    'Write exactly FIVE ordered original episode drafts for the days in context. '+str(duration)+' seconds per episode. Each needs a goal, conflict, reveal/turn and a small completed mini_arc. opening_ack explicitly addresses the previous ending (day1 introduces the premise). Days1–29: unresolved=true and UNIQUE concrete dangerous/question hook, at least six words, with named people/objects. Never generic teasers. Day30: unresolved=false, resolved satisfying ending, no new cliffhanger. Only known character, location and thread IDs. advances MUST include threads on their plant day; resolves MUST include threads exactly on their payoff day. Do not resolve early, reuse resolved threads, or advance before planting. Use only actors within introduction/removal days. Return drafts in order, no episode numbers or metadata. Context (data): '+canonical_json(context),OutlineChunk,
                    lambda value:[error for error in validate_outline(bible,episodes+compile_chunk(value),complete=start==26,minimum=config.episode_duration_s.min,maximum=config.episode_duration_s.max) if 'hook duplicates' not in error and 'hook is generic' not in error],planner,schema=schema,transform=lambda body:body if isinstance(body,dict) and 'episodes' in body else {'episodes':[body.get(f'day_{n}') if isinstance(body,dict) else None for n in range(start,start+5)]})
                errors=validate_outline(bible,episodes+compile_chunk(chunk),complete=start==26,minimum=config.episode_duration_s.min,maximum=config.episode_duration_s.max)
                if errors:
                    focused=hook_repair(chunk,errors,start,episodes,bible)
                    if not focused:raise ValueError('; '.join(errors))
                    EventLog(self.storage).append('director','series.hooks_rejected','series',identifier,{'start':start,'errors':errors})
                    prompt,hook_schema,merge=focused
                    chunk=self._request(identifier,'hooks_'+str(start),prompt,OutlineChunk,
                        lambda value:validate_outline(bible,episodes+compile_chunk(value),complete=start==26,minimum=config.episode_duration_s.min,maximum=config.episode_duration_s.max),
                        planner,schema=hook_schema,transform=merge,repair=lambda value,errors:hook_repair(value,errors,start,episodes,bible))
                compiled=compile_chunk(chunk)
                with self.storage.connect() as c:
                    for ep in compiled:c.execute('INSERT INTO series_episodes(episode_id,series_id,ep_no,outline_json,created_at,updated_at) VALUES (?,?,?,?,?,?)',(episode_id(identifier,ep.ep_no),identifier,ep.ep_no,canonical_json(ep.model_dump()),self.storage.timestamp(),self.storage.timestamp()))
                    c.execute('UPDATE series SET updated_at=? WHERE series_id=?',(self.storage.timestamp(),identifier))
                episodes.extend(compiled)
            with self.storage.connect() as c:c.execute("UPDATE series SET status='awaiting_cast',updated_at=? WHERE series_id=?",(self.storage.timestamp(),identifier))
            self._state(identifier,'completed','awaiting_cast')
            self.storage.complete_claim(claim['claim_token'],{'series_id':identifier,'episodes':30})
            EventLog(self.storage).append('director','series.outlined','series',identifier,{'episodes':30,'cast_approved':False,'provider':'ollama'})
        except ValueError as exc:
            # Received invalid outputs are known; still require an explicit NEW request.
            self._state(identifier,'failed','owner_revision',str(exc));return
        except Exception:
            self._state(identifier,'needs_reconciliation','inspect_receipts','Local completion is uncertain; inspect receipts and Ollama before any new request');return

    def edit(self,identifier,episodes,expected_updated_at):
        current=read_series(self.storage.db_path,identifier)
        if not current or current['planning_status']!='completed':raise ValueError('Only a completed outline can be edited')
        request=self._config(identifier)
        parsed=[Episode.model_validate(e) for e in episodes]
        errors=validate_outline(Bible.model_validate(current['bible']),parsed,minimum=request.episode_duration_s.min,maximum=request.episode_duration_s.max)
        if errors:raise ValueError('; '.join(errors))
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT updated_at FROM series WHERE series_id=?',(identifier,)).fetchone()
            if old['updated_at']!=expected_updated_at:raise IdempotencyConflict('Series changed; reload before saving')
            if c.execute("SELECT 1 FROM series_episodes WHERE series_id=? AND (status!='outlined' OR job_id IS NOT NULL OR script_plan_id IS NOT NULL)",(identifier,)).fetchone():raise ValueError('Produced episodes lock the outline; do not rewrite their continuity')
            for ep in parsed:c.execute('UPDATE series_episodes SET outline_json=?,updated_at=? WHERE series_id=? AND ep_no=?',(canonical_json(ep.model_dump()),self.storage.timestamp(),identifier,ep.ep_no))
            c.execute('UPDATE series SET updated_at=? WHERE series_id=?',(self.storage.timestamp(),identifier))
        EventLog(self.storage).append('user','series.outline_edited','series',identifier,{'episodes':30})
        return read_series(self.storage.db_path,identifier)

    def _config(self,identifier):
        with self.storage.connect() as c:row=c.execute('SELECT request_json FROM automation_series_requests WHERE series_id=?',(identifier,)).fetchone()
        if not row:raise ValueError('Unknown series')
        return StoryLaneConfig.model_validate(json.loads(row[0])['config'])

    def approve_cast(self,identifier,package_id,expected_updated_at,*,confirmed=False,actor='local-owner',start_date:date | None=None):
        if confirmed is not True:raise ValueError('Inspect and explicitly approve the cast for this series')
        current=read_series(self.storage.db_path,identifier)
        if not current or current['planning_status']!='completed' or current['status']!='awaiting_cast':raise ValueError('Series must finish planning and await cast approval')
        from app.services.cinematic_cast_service import CinematicCastService
        cast=(self.cast_service or CinematicCastService(self.storage.db_path)).get(str(UUID(str(package_id))))
        bible=Bible.model_validate(current['bible'])
        if fingerprint(bible.model_dump())!=current['bible_sha256']:raise ValueError('Frozen bible hash changed')
        actors={a.character_id:a for a in bible.characters};members={a['character_id']:a for a in cast['characters']}
        if set(actors)!=set(members):raise ValueError('Cast IDs must exactly match the frozen bible; use the actor instructions shown on this page')
        for key,actor_bible in actors.items():
            if any(members[key].get(field)!=getattr(actor_bible,field) for field in ['name','description','wardrobe','voice_notes']):raise ValueError('Cast names, appearance, wardrobe and voice must match the frozen bible')
        episodes=[Episode.model_validate(x) for x in current['episodes']];cfg=self._config(identifier)
        if validate_outline(bible,episodes,minimum=cfg.episode_duration_s.min,maximum=cfg.episode_duration_s.max):raise ValueError('Outline no longer passes validation')
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM series WHERE lane=? AND status='active'",(current['lane'],)).fetchone():raise ValueError('This lane already has an active series')
            changed=c.execute("UPDATE series SET status='active',cast_package_ids_json=?,start_date=?,updated_at=? WHERE series_id=? AND status='awaiting_cast' AND updated_at=?",(canonical_json([cast['package_id']]),start_date.isoformat() if start_date else None,self.storage.timestamp(),identifier,expected_updated_at))
            if changed.rowcount!=1:raise IdempotencyConflict('Series changed; reload before approving')
        EventLog(self.storage).append('user','series.cast_approved','series',identifier,{'actor':actor,'package_id':cast['package_id'],'sha256':cast['sha256']})
        return read_series(self.storage.db_path,identifier)
