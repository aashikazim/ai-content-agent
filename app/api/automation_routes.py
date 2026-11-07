"""Additive automation API; same administrator session and CSRF protection."""
import os
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
