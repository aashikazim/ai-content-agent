"""Additive automation API; same administrator session and CSRF protection."""
import os
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from app.api.admin_routes import require_session
from app.automation.config import load_settings,ShortsLaneConfig
from app.automation.selector import TopicSelector

router=APIRouter(prefix='/api/v1/automation',dependencies=[Depends(require_session)])


class PreviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    lane:str=Field(default='shorts_daily',pattern='^[a-z][a-z0-9_]{0,63}$')
    region:str=Field(default='US',pattern='^[A-Z]{2}$')


@router.post('/plan-preview')
def plan_preview(body:PreviewRequest):
    settings=load_settings()
    lane=settings.config.lanes.get(body.lane)
    if not isinstance(lane,ShortsLaneConfig):raise HTTPException(422,'Choose a topic Shorts lane')
    return TopicSelector(os.getenv('DATABASE_PATH','data/trends.db')).preview(lane,body.region)


from datetime import date
from app.automation.reports import read_day,data_root
from app.automation.shorts_service import ShortsService


class RunDayRequest(PreviewRequest):
    confirmed:bool=Field(default=False,strict=True)


@router.get('/plan/{day}')
def daily_plan(day:date,lane:str='shorts_daily'):
    return read_day(os.getenv('DATABASE_PATH','data/trends.db'),day,lane)


@router.post('/plan/{day}/run',status_code=202)
def run_day(day:date,body:RunDayRequest,principal=Depends(require_session)):
    settings=load_settings()
    if not settings.enabled or body.confirmed is not True:raise HTTPException(422,'Enable automation and confirm the local rendering batch')
    try:return ShortsService(os.getenv('DATABASE_PATH','data/trends.db'),settings).enqueue_day(day,body.lane,principal['username'],confirmed=True,launch=True,region=body.region)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


@router.get('/gpu/forecast')
def gpu_forecast(day:date,lane:str='shorts_daily'):
    plan=read_day(os.getenv('DATABASE_PATH','data/trends.db'),day,lane)
    return plan['run']['forecast'] if plan['run'] else {'items':[],'notice':'No persisted day forecast yet. Planning reserves work only after confirmation.'}


@router.get('/reports/{day}')
def daily_report(day:date):
    path=data_root()/'reports'/(day.isoformat()+'.md')
    return {'markdown':path.read_text() if path.is_file() else 'No report yet.'}


@router.get('/lanes')
def lanes():
    settings=load_settings()
    return {'enabled':settings.enabled,'timezone':settings.config.timezone,'lanes':{k:v.model_dump(mode='json') for k,v in settings.config.lanes.items()}}


from uuid import UUID
from app.automation.series.service import SeriesService,read_series
from app.automation.series.models import Episode
from app.automation.storage import IdempotencyConflict

class SeriesRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    source_series_id:UUID | None=None
    genre:str=Field(default='mystery drama',min_length=2,max_length=100)
    premise:str=Field(default='',max_length=2000)
    lane:str=Field(default='story_series',pattern='^[a-z][a-z0-9_]{0,63}$')
    confirmed:bool=Field(default=False,strict=True)

class OutlineEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_updated_at:str=Field(max_length=50)
    episodes:list[Episode]=Field(min_length=30,max_length=30)

class CastApproval(BaseModel):
    model_config=ConfigDict(extra='forbid')
    package_id:UUID
    expected_updated_at:str=Field(max_length=50)
    confirmed:bool=Field(default=False,strict=True)
    start_date:date | None=None

def series_action(action):
    try:return action()
    except IdempotencyConflict as exc:raise HTTPException(409,str(exc)) from None
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@router.get('/series')
def list_series():
    return {'series':read_series(os.getenv('DATABASE_PATH','data/trends.db'))}

@router.post('/series',status_code=202)
def create_series(body:SeriesRequest,principal=Depends(require_session)):
    return series_action(lambda:SeriesService(os.getenv('DATABASE_PATH','data/trends.db')).enqueue(str(body.request_id),genre=body.genre,premise=body.premise,lane=body.lane,actor=principal['username'],confirmed=body.confirmed,launch=True,source_series_id=str(body.source_series_id) if body.source_series_id else None))

@router.get('/series/{identifier}')
def series_detail(identifier:UUID):
    value=read_series(os.getenv('DATABASE_PATH','data/trends.db'),str(identifier))
    if not value:raise HTTPException(404,'Series not found')
    return value

@router.put('/series/{identifier}/outline')
def edit_series(identifier:UUID,body:OutlineEdit):
    return series_action(lambda:SeriesService(os.getenv('DATABASE_PATH','data/trends.db')).edit(str(identifier),[e.model_dump() for e in body.episodes],body.expected_updated_at))

@router.post('/series/{identifier}/approve-cast')
def approve_series_cast(identifier:UUID,body:CastApproval,principal=Depends(require_session)):
    return series_action(lambda:SeriesService(os.getenv('DATABASE_PATH','data/trends.db')).approve_cast(str(identifier),str(body.package_id),body.expected_updated_at,confirmed=body.confirmed,actor=principal['username'],start_date=body.start_date))


class EpisodeRunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed:bool=Field(default=False,strict=True)
    validation_duration:int | None=Field(default=None,ge=180,le=240)

class EpisodeSkipRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed:bool=Field(default=False,strict=True)
    notes:str=Field(min_length=10,max_length=1000)

@router.get('/series/{identifier}/episodes')
def episode_history(identifier:UUID):
    path=__import__('pathlib').Path(os.getenv('DATABASE_PATH','data/trends.db')).absolute()
    import sqlite3,json
    if not path.exists():return {'episodes':[]}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='series_episodes'").fetchone():return {'episodes':[]}
        has_runs=c.execute("SELECT 1 FROM sqlite_master WHERE name='automation_episode_runs'").fetchone()
        has_jobs=c.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone()
        fields='e.*'+(',r.production_id,r.status AS production_status,r.error AS production_error,r.run_id,r.validation_only' if has_runs else '')+(',j.status AS job_status' if has_jobs else '')
        sql='SELECT '+fields+' FROM series_episodes e'+(' LEFT JOIN automation_episode_runs r ON r.series_id=e.series_id AND r.ep_no=e.ep_no' if has_runs else '')+(' LEFT JOIN jobs j ON j.job_id=e.job_id' if has_jobs else '')+' WHERE e.series_id=? ORDER BY e.ep_no'
        return {'episodes':[dict(r) for r in c.execute(sql,(str(identifier),))]}

@router.post('/series/{identifier}/episodes/{number}/run',status_code=202)
def run_episode(identifier:UUID,number:int,body:EpisodeRunRequest,principal=Depends(require_session)):
    from app.automation.series.episodes import EpisodeService
    return series_action(lambda:EpisodeService(os.getenv('DATABASE_PATH','data/trends.db')).enqueue(str(identifier),number,actor=principal['username'],confirmed=body.confirmed,launch=True,validation_duration=body.validation_duration))

@router.post('/series/{identifier}/episodes/{number}/skip')
def skip_episode(identifier:UUID,number:int,body:EpisodeSkipRequest,principal=Depends(require_session)):
    from app.automation.series.episodes import EpisodeService
    def action():
        EpisodeService(os.getenv('DATABASE_PATH','data/trends.db')).skip(str(identifier),number,principal['username'],body.notes,body.confirmed)
        return {'status':'skipped'}
    return series_action(action)

@router.post('/series-tick')
def series_tick(body:EpisodeRunRequest):
    from app.automation.series.episodes import EpisodeService
    if not body.confirmed:raise HTTPException(422,'Confirm local buffer generation')
    return series_action(lambda:EpisodeService(os.getenv('DATABASE_PATH','data/trends.db')).tick(launch=True))

@router.get('/episode-runs/{identifier}')
def episode_run_detail(identifier:UUID):
    import sqlite3,json
    from pathlib import Path
    path=Path(os.getenv('DATABASE_PATH','data/trends.db')).absolute()
    if not path.exists():raise HTTPException(404,'Episode run not found')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='automation_episode_runs'").fetchone():raise HTTPException(404,'Episode run not found')
        row=c.execute('SELECT * FROM automation_episode_runs WHERE run_id=?',(str(identifier),)).fetchone()
        if not row:raise HTTPException(404,'Episode run not found')
        result=dict(row);result['request']=json.loads(result.pop('request_json'));result['memory']=json.loads(result.pop('memory_json'))
        result['receipts']=[dict(r) for r in c.execute('SELECT stage,attempt_no,status,errors_json FROM automation_episode_receipts WHERE run_id=? ORDER BY rowid',(str(identifier),))]
        return result

# Governor is a separate, admin-only authority; generation credentials cannot use it.
def governor_service():
    from app.automation.governor import Governor
    return Governor(os.getenv('DATABASE_PATH','data/trends.db'))

class GovernorStateRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed:bool=Field(default=False,strict=True)
    scope:Literal['global','youtube','facebook']
    kill_switch:bool | None=Field(default=None,strict=True)
    paused:bool | None=Field(default=None,strict=True)
    reason:str=Field(default='',max_length=1000)

class LaneApprovalRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed:bool=Field(default=False,strict=True)
    approval_mode:Literal['manual','auto_with_gates']

class StandingRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed:bool=Field(default=False,strict=True)
    providers:list[Literal['openai','anthropic','google','runway']]=Field(min_length=1,max_length=4)
    max_daily_usd:str=Field(min_length=1,max_length=40)
    max_total_usd:str=Field(min_length=1,max_length=40)
    expires_at:str=Field(min_length=1,max_length=50)

@router.get('/governor')
def governor_status():return governor_service().status()

@router.post('/governor/state')
def governor_state(body:GovernorStateRequest,principal=Depends(require_session)):
    return series_action(lambda:governor_service().set_state(body.scope,kill_switch=body.kill_switch,paused=body.paused,reason=body.reason,confirmed=body.confirmed,actor=principal['username']))

@router.put('/lanes/{lane}/approval')
def lane_approval(lane:str,body:LaneApprovalRequest,principal=Depends(require_session)):
    return series_action(lambda:governor_service().set_lane(lane,body.approval_mode,confirmed=body.confirmed,actor=principal['username']))

@router.post('/governor/tick')
def governor_manual_tick(body:EpisodeRunRequest):
    if not body.confirmed:raise HTTPException(422,'Confirm evaluation using the configured Governor mode')
    return series_action(lambda:governor_service().tick())

@router.get('/events')
def automation_events(after_id:int=0,limit:int=100):
    from dataclasses import asdict
    from app.automation.events import EventLog
    return series_action(lambda:{'events':[asdict(e) for e in EventLog(governor_service().storage).list(after_id=after_id,limit=limit)]})

@router.get('/authorizations')
def standing_list():
    from app.automation.authorizations import StandingAuthorizations
    service=governor_service()
    return {'authorizations':StandingAuthorizations(service.storage,service.settings).list()}

@router.post('/authorizations',status_code=201)
def standing_create(body:StandingRequest,principal=Depends(require_session)):
    from app.automation.authorizations import StandingAuthorizations
    def action():
        service=governor_service()
        identifier=StandingAuthorizations(service.storage,service.settings).create(body.providers,body.max_daily_usd,body.max_total_usd,body.expires_at,actor=principal['username'],confirmed=body.confirmed)
        return {'authorization_id':identifier}
    return series_action(action)

@router.post('/authorizations/{identifier}/revoke')
def standing_revoke(identifier:UUID,body:EpisodeRunRequest,principal=Depends(require_session)):
    from app.automation.authorizations import StandingAuthorizations
    def action():
        service=governor_service();StandingAuthorizations(service.storage,service.settings).revoke(str(identifier),actor=principal['username'],confirmed=body.confirmed)
        return {'status':'revoked'}
    return series_action(action)

class GovernorActionClose(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed:bool=Field(default=False,strict=True)
    notes:str=Field(min_length=10,max_length=1000)

@router.post('/governor/actions/{identifier}/close')
def close_governor_action(identifier:UUID,body:GovernorActionClose,principal=Depends(require_session)):
    return series_action(lambda:governor_service().close_action(str(identifier),notes=body.notes,actor=principal['username'],confirmed=body.confirmed))

@router.get('/operations')
def automation_operations():
    from app.automation.operations import snapshot
    return snapshot(os.getenv('DATABASE_PATH','data/trends.db'))
