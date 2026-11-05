"""Authenticated Clip Studio routes; uploads and work use the existing app/database."""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.admin_routes import require_session
from app.services.clip_studio.schemas import ProjectRequest
from app.services.clip_studio.service import ClipStudioService


class BoundedUploadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request):
            await run_in_threadpool(require_session, request)  # Before multipart parsing.
            maximum = ClipStudioService.upload_limit() + 1024**2  # multipart overhead
            length = request.headers.get('content-length')
            if length and (not length.isdecimal() or int(length) > maximum):
                raise HTTPException(413, 'Clip Studio upload exceeds the configured limit')
            received = 0
            async def receive():
                nonlocal received
                message = await request.receive()
                received += len(message.get('body', b''))
                if received > maximum:
                    raise HTTPException(413, 'Clip Studio upload exceeds the configured limit')
                return message
            return await handler(Request(request.scope, receive=receive))
        return bounded


router = APIRouter(prefix='/api/v1/clip-studio', dependencies=[Depends(require_session)], route_class=BoundedUploadRoute)


def service():
    return ClipStudioService()


def invoke(action):
    try:
        return action()
    except ValidationError:
        raise HTTPException(422, 'Invalid Clip Studio settings or plan') from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    confirmed: bool = Field(default=False, strict=True)
    paid_planning_confirmed: bool = Field(default=False, strict=True)


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    attempt_id: UUID
    confirmed: bool = Field(default=False, strict=True)


@router.get('/catalog')
def catalog():
    return ClipStudioService.catalog()


@router.get('/projects')
def projects(principal=Depends(require_session)):
    return {'projects': service().list_projects(principal['username'])}


@router.post('/projects', status_code=202)
def create_project(payload: str = Form(..., max_length=12000), files: list[UploadFile] = File(default=[]), principal=Depends(require_session)):
    try:
        request = ProjectRequest.model_validate_json(payload)
        # FastAPI runs this synchronous handler in its thread pool. File validation
        # cannot block the event loop serving the existing dashboard/API pages.
        return service().create(request, files, principal['username'])
    except ValidationError:
        raise HTTPException(422, 'Enter valid settings, a source and explicit source-rights confirmation') from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get('/projects/{project_id}')
def project(project_id: UUID, principal=Depends(require_session)):
    value=invoke(lambda: service().get(str(project_id), principal['username']))
    if value['jobs'] and value['jobs'][0]['status'] in {'failed','uncertain'}:
        job=value['jobs'][0]
        logging.getLogger(__name__).error('Clip Studio job %s failed at %s: %s',job['job_id'],job['stage'],job.get('error') or 'Inspect individual clip errors')
    return value


@router.get('/jobs/{job_id}')
def job(job_id: UUID, principal=Depends(require_session)):
    feature = service()
    with feature.storage.connect() as conn:
        row = conn.execute('SELECT project_id FROM clip_studio_jobs WHERE job_id=?', (str(job_id),)).fetchone()
    if not row:
        raise HTTPException(404, 'Clip Studio job not found')
    value = invoke(lambda: feature.get(row[0], principal['username']))
    return next(j for j in value['jobs'] if j['job_id'] == str(job_id))


@router.post('/projects/{project_id}/clips/{clip_id}/regenerate', status_code=202)
def regenerate(project_id: UUID, clip_id: UUID, body: Action, principal=Depends(require_session)):
    if body.confirmed is not True:
        raise HTTPException(422, 'Confirm generating a new version')
    return invoke(lambda: service().enqueue(str(project_id), principal['username'], body.request_id, clip_id=str(clip_id)))


@router.post('/projects/{project_id}/jobs/{job_id}/retry', status_code=202)
def retry(project_id: UUID, job_id: UUID, body: Action, principal=Depends(require_session)):
    if body.confirmed is not True:
        raise HTTPException(422, 'Confirm retrying failed work')
    return invoke(lambda: service().enqueue(str(project_id), principal['username'], body.request_id, retry_job_id=str(job_id),paid_planning_confirmed=body.paid_planning_confirmed))


@router.post('/projects/{project_id}/clips/{clip_id}/select')
def select(project_id: UUID, clip_id: UUID, body: Selection, principal=Depends(require_session)):
    if body.confirmed is not True:
        raise HTTPException(422, 'Confirm selecting this version')
    return invoke(lambda: service().select(str(project_id), str(clip_id), str(body.attempt_id), principal['username']))


@router.get('/attempts/{attempt_id}/{kind}')
def asset(attempt_id: UUID, kind: str, download: bool = False, principal=Depends(require_session)):
    if kind not in {'video', 'thumbnail'}:
        raise HTTPException(404, 'Asset not found')
    path = invoke(lambda: service().asset(str(attempt_id), principal['username'], kind))
    return FileResponse(path, media_type='video/mp4' if kind == 'video' else 'image/jpeg',
                        filename=('clip-'+str(attempt_id)+path.suffix) if download else None,
                        headers={'Cache-Control': 'no-store'})


class Reconcile(Action):
    resume: bool = Field(default=False, strict=True)
    outcomes_checked: bool = Field(default=False, strict=True)


@router.post('/projects/{project_id}/reconcile', status_code=202)
def reconcile(project_id: UUID, body: Reconcile, principal=Depends(require_session)):
    if not body.confirmed or not body.outcomes_checked:
        raise HTTPException(422, 'Confirm you inspected saved generation IDs/provider usage and stopped any abandoned work; reconciliation never submits a replacement')
    return invoke(lambda: service().reconcile(str(project_id),principal['username'],body.request_id,resume=body.resume))


@router.post('/projects/{project_id}/clips/{clip_id}/review')
def review(project_id: UUID, clip_id: UUID, body: Selection, principal=Depends(require_session)):
    if not body.confirmed:
        raise HTTPException(422, 'Confirm sending this version for manual content review')
    feature=service()
    project=invoke(lambda:feature.get(str(project_id),principal['username']))
    clip=next((c for c in project['clips'] if c['clip_id']==str(clip_id)),None)
    attempt=next((a for a in (clip or {}).get('attempts',[]) if a['attempt_id']==str(body.attempt_id)),None)
    if not attempt or attempt['attempt_id']!=clip['selected_attempt_id'] or attempt.get('stale_continuity'):
        raise HTTPException(422,'Choose the current selected version with unchanged continuity dependencies')
    from app.services.clip_studio.review import send_to_review
    return invoke(lambda:send_to_review(feature,str(body.attempt_id),principal['username']))


@router.post('/projects/{project_id}/start-queued', status_code=202)
def start_queued(project_id: UUID, body: Action, principal=Depends(require_session)):
    if not body.confirmed:
        raise HTTPException(422,'Confirm waking the worker for already-authorized queued work')
    feature=service()
    project=invoke(lambda:feature.get(str(project_id),principal['username']))
    queued=next((j for j in project['jobs'] if j['status']=='queued'),None)
    if not queued:
        raise HTTPException(422,'No queued job exists; running or uncertain generations must be polled/reconciled')
    # Claims are atomic and the queue lock serializes launchers. This cannot submit
    # a second copy of a running request, even if the browser repeats the action.
    feature.launch(queued['job_id'])
    return {'job_id':queued['job_id'],'status':'queued'}
