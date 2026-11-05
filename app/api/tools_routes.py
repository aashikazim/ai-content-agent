"""Authenticated production utilities; no approval/publishing or arbitrary paths."""
import json
import os
from typing import Literal
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field,model_validator

from app.api.admin_routes import require_session
from app.services.production_tools import ProductionTools

router=APIRouter(prefix='/api/v1/tools',dependencies=[Depends(require_session)])


def invoke(action):
    try:return action()
    except ValueError as error:raise HTTPException(422,str(error)) from None


class ToolRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',hide_input_in_errors=True)
    request_id:UUID
    kind:Literal['speech','stock','captions','regenerate','research','illustration']
    text:str=Field(default='',max_length=5000)
    profile_id:str|None=Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,80}$')
    candidate_id:UUID|None=None
    job_id:UUID|None=None
    confirmed:bool=Field(default=False,strict=True)
    rights_confirmed:bool=Field(default=False,strict=True)
    forced:bool=Field(default=False,strict=True)
    model:Literal['tiny','base','small','medium']='base'
    expected:str=Field(default='',max_length=50000)
    scene_number:int=Field(default=1,ge=1,le=600,strict=True)
    asset:Literal['image','narration']='narration'
    aspect_ratio:Literal['16:9','9:16','1:1']='1:1'
    seed:int=Field(default=42,ge=0,le=2147483647,strict=True)
    query:str=Field(default='',max_length=200)

    @model_validator(mode='after')
    def valid_action(self):
        if self.confirmed is not True:raise ValueError('Confirm starting the selected production tool')
        if self.kind in ('speech','illustration') and not self.text.strip():raise ValueError('Enter narration text')
        if self.kind=='stock' and self.rights_confirmed is not True:raise ValueError('Review the Pexels license and confirm intended-use rights')
        if self.kind=='stock' and not self.candidate_id:raise ValueError('Choose a saved stock search result')
        if self.kind in ('captions','regenerate') and not self.job_id:raise ValueError('Choose a source video job')
        if self.kind=='captions' and self.forced and not self.expected.strip():raise ValueError('Forced alignment needs the intended transcript')
        if self.kind=='research' and not self.query.strip():raise ValueError('Enter a research query')
        return self


class StockQuery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=1,max_length=200)
    kind:Literal['video','image']='video'
    orientation:Literal['portrait','landscape','square']='portrait'


class ScriptCheck(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(min_length=1,max_length=50000)
    duration_seconds:int|None=Field(default=None,ge=1,le=1800,strict=True)


@router.get('/readiness')
def readiness():
    from app.services.operations_service import readiness
    return readiness(os.getenv('DATABASE_PATH','data/trends.db'))


@router.get('/speech-profiles')
def speech_profiles():
    from app.services.speech_profiles import catalog
    return {'profiles':catalog()}


@router.get('/jobs')
def jobs(principal=Depends(require_session)):
    return {'jobs':ProductionTools().list(principal['username'])}


@router.get('/jobs/{identifier}')
def job(identifier:UUID,principal=Depends(require_session)):
    return invoke(lambda:ProductionTools().get(str(identifier),principal['username']))


@router.post('/jobs',status_code=202)
def create(body:ToolRequest,principal=Depends(require_session)):
    params=body.model_dump(mode='json',exclude={'request_id','kind'})
    return invoke(lambda:ProductionTools().enqueue(body.kind,params,principal['username'],body.request_id))


@router.get('/jobs/{identifier}/asset')
def asset(identifier:UUID,principal=Depends(require_session)):
    path=invoke(lambda:ProductionTools().asset(str(identifier),principal['username']))
    return FileResponse(path,filename='production-'+str(identifier)+path.suffix,headers={'Cache-Control':'no-store'})


@router.post('/stock/search')
def stock(body:StockQuery,principal=Depends(require_session)):
    from app.services.stock_service import StockService
    service=ProductionTools();items=invoke(lambda:StockService().search(body.query,body.kind,body.orientation))
    public=[]
    with service.queue.connect() as conn:
        for item in items:
            identifier=str(uuid4())
            import time
            conn.execute('INSERT INTO production_tool_candidates VALUES (?,?,?,?)',(identifier,principal['username'],json.dumps(item),int(time.time())))
            public.append({**{k:v for k,v in item.items() if k!='download_url'},'candidate_id':identifier})
    return {'results':public,'provider_url':'https://www.pexels.com','license_url':'https://www.pexels.com/license/'}


@router.post('/script-quality')
def script_check(body:ScriptCheck):
    from app.services.research_service import script_quality
    return invoke(lambda:script_quality(body.text,body.duration_seconds))


class ResearchClaim(BaseModel):
    model_config=ConfigDict(extra='forbid')
    claim:str=Field(min_length=1,max_length=2000)
    sources:list[str]=Field(min_length=1,max_length=5)


class DossierRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    topic:str=Field(min_length=1,max_length=200)
    claims:list[ResearchClaim]=Field(min_length=1,max_length=30)
    confirmed:bool=Field(default=False,strict=True)


@router.get('/research-dossiers')
def dossiers():
    from app.services.research_dossiers import list_dossiers
    return {'dossiers':list_dossiers()}


@router.post('/research-dossiers',status_code=201)
def save_dossier(body:DossierRequest):
    from app.services.research_dossiers import create
    return invoke(lambda:create(body.topic,[c.model_dump() for c in body.claims],body.confirmed))
