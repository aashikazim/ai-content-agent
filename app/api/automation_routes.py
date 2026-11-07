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
