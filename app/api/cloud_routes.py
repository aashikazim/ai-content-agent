"""Session-only cloud production endpoints. Every paid submission is explicit."""
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.admin_routes import require_session
from app.services.character_reference_service import ReferenceUpload
from app.services.cloud_video_service import CloudVideoService
from app.services.gemini_video_service import VideoProviderError
from app.services.driving_reference_service import DrivingUpload

router = APIRouter(prefix='/api/v1/generation', dependencies=[Depends(require_session)])


def service():
    return CloudVideoService(os.getenv('DATABASE_PATH', 'data/trends.db'))


def invoke(action):
    try:
        return action()
    except ValidationError:
        raise HTTPException(422, 'The production script or configuration is invalid; request a validated revision') from None
    except (ValueError, VideoProviderError) as error:
        raise HTTPException(422, str(error)) from None


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    script_attempt_id: UUID
    reference_ids: list[UUID] | None = Field(default=None, max_length=3)
    start_image_reference_id: UUID | None = None
    shot_image_reference_ids: dict[int, UUID] | None = Field(default=None, max_length=240)
    shot_driving_video_ids: dict[int, UUID] | None = Field(default=None, max_length=240)


class PaidSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quote_id: UUID
    confirmed: bool = Field(default=False, strict=True)


@router.get('/references')
def references():
    return {'references': service().references.list_references()}


@router.post('/references', status_code=201)
def upload_reference(body: ReferenceUpload):
    refs = service().references
    return invoke(lambda: refs.public(refs.create(body)))


@router.get('/references/{reference_id}/image')
def reference_image(reference_id: UUID):
    refs = service().references
    value = invoke(lambda: refs.verified([reference_id])[0])
    return FileResponse(value['image_path'], media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.post('/video-quotes')
def video_quote(body: QuoteRequest, principal=Depends(require_session)):
    options = {key: getattr(body, key) for key in ('start_image_reference_id', 'shot_image_reference_ids', 'shot_driving_video_ids') if key in body.model_fields_set}
    return invoke(lambda: service().quote(str(body.script_attempt_id), principal['username'], body.reference_ids,
        **options))


@router.get('/runway/credits')
def runway_credits():
    from app.models.generation_config import GenerationConfig
    from app.services.runway_video_service import RunwayVideoService
    return invoke(lambda: RunwayVideoService(GenerationConfig(video_provider='runway', video_model='gen4.5')).check_model())


@router.get('/driving-references')
def driving_references():
    return {'references': service().driving.list_references()}


@router.post('/driving-references', status_code=201)
def upload_driving_reference(body: DrivingUpload):
    return invoke(lambda: service().driving.create(body))


@router.get('/driving-references/{reference_id}/video')
def driving_video(reference_id: UUID):
    value = invoke(lambda: service().driving.verified(reference_id))
    return FileResponse(value['video_path'], media_type='video/mp4', headers={'Cache-Control': 'no-store'})


@router.post('/videos', status_code=202)
def create_video(body: PaidSubmission, tasks: BackgroundTasks, principal=Depends(require_session)):
    value = invoke(lambda: service().create(str(body.quote_id), principal['username'], body.confirmed))
    # The same existing workflow handles job state. It skips discovery/planning
    # for a production whose existing script and shot authorization were saved.
    from app.api.routes import execute_job
    tasks.add_task(execute_job, value['job_id'])
    return {'production_id': value['production_id'], 'job_id': value['job_id'], 'status': 'queued'}


@router.get('/videos')
def videos():
    return {'productions': service().storage.list_productions()}


@router.get('/videos/{production_id}')
def video(production_id: UUID):
    value = service().storage.get(str(production_id))
    if not value:
        raise HTTPException(404, 'Video production not found')
    return value


@router.post('/videos/{production_id}/refresh')
def refresh_video(production_id: UUID, tasks: BackgroundTasks):
    # GETs an existing interaction and downloads/assembles locally. Never POSTs
    # a paid generation, including for an unsubmitted queued shot.
    instance = service()
    if not instance.storage.get(str(production_id)):
        raise HTTPException(404, 'Video production not found')
    tasks.add_task(instance.process, str(production_id), submit_queued=False)
    return {'production_id': str(production_id), 'status': 'retrieval_requested'}


@router.get('/videos/{production_id}/shots/{attempt_id}/video')
def shot_video(production_id: UUID, attempt_id: UUID):
    instance = service()
    attempt = instance.storage.attempt(str(attempt_id))
    if not attempt or attempt['production_id'] != str(production_id) or not attempt['clip_path']:
        raise HTTPException(404, 'Shot video not available')
    path = Path(attempt['clip_path']).resolve()
    if not path.is_relative_to(instance.root) or not path.is_file():
        raise HTTPException(404, 'Shot video not available')
    return FileResponse(path, media_type='video/mp4', headers={'Cache-Control': 'no-store'})


class ShotQuote(BaseModel):
    model_config = ConfigDict(extra='forbid')
    shot_number: int = Field(ge=1, le=240, strict=True)
    operation: str = Field(pattern=r'^(generate|edit|extend)$')
    instructions: str = Field(default='', max_length=2000)
    parent_attempt_id: UUID | None = None
    extension_seconds: int = Field(default=3, ge=3, le=10, strict=True)
    edit_model: str | None = Field(default=None, max_length=100)


class ShotSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    attempt_id: UUID
    trim_start_seconds: float = Field(default=0, ge=0, le=120, allow_inf_nan=False)


class Reconciliation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notes: str = Field(min_length=1, max_length=500)
    confirmed: bool = Field(default=False, strict=True)
    interaction_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,200}$')
    close_attempt: bool = Field(default=False, strict=True)


class QualityReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    continuity_confirmed: bool = Field(default=False, strict=True)
    captions_confirmed: bool = Field(default=False, strict=True)
    rights_confirmed: bool = Field(default=False, strict=True)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: bool = Field(default=False, strict=True)


@router.post('/videos/{production_id}/shot-quotes')
def shot_quote(production_id: UUID, body: ShotQuote, principal=Depends(require_session)):
    return invoke(lambda: service().quote_change(str(production_id), principal['username'], body.shot_number,
        operation=body.operation, instructions=body.instructions,
        parent_attempt_id=str(body.parent_attempt_id) if body.parent_attempt_id else None,
        extension_seconds=body.extension_seconds, edit_model=body.edit_model))


@router.post('/videos/{production_id}/shots', status_code=202)
def change_shot(production_id: UUID, body: PaidSubmission, tasks: BackgroundTasks, principal=Depends(require_session)):
    instance = service()
    # Bind the path before consuming authorization, preventing cross-production changes.
    with instance.storage.connect() as conn:
        quote = conn.execute('SELECT request_json FROM video_generation_quotes WHERE quote_id=?', (str(body.quote_id),)).fetchone()
    import json
    if not quote or json.loads(quote[0]).get('production_id') != str(production_id):
        raise HTTPException(422, 'Quote does not authorize this production')
    value = invoke(lambda: instance.change(str(body.quote_id), principal['username'], body.confirmed))
    tasks.add_task(instance.process, str(production_id), submit_queued=True, attempt_ids=[value['attempt_id']])
    return value


@router.post('/videos/{production_id}/select')
def select_shot(production_id: UUID, body: ShotSelection):
    return invoke(lambda: service().select(str(production_id), str(body.attempt_id), body.trim_start_seconds))


@router.post('/videos/{production_id}/shots/{attempt_id}/reconcile')
def reconcile_shot(production_id: UUID, attempt_id: UUID, body: Reconciliation, principal=Depends(require_session)):
    return invoke(lambda: service().reconcile(str(production_id), str(attempt_id), principal['username'],
        body.notes, body.confirmed, body.interaction_id, body.close_attempt))


@router.post('/videos/{production_id}/continue', status_code=202)
def continue_video(production_id: UUID, body: Confirmation, tasks: BackgroundTasks):
    instance = service()
    identifiers = invoke(lambda: instance.continue_queued(str(production_id), body.confirmed))
    tasks.add_task(instance.process, str(production_id), submit_queued=True, attempt_ids=identifiers)
    return {'production_id': str(production_id), 'status': 'processing', 'authorized_unsubmitted_shots': len(identifiers)}


@router.post('/videos/{production_id}/review')
def send_to_review(production_id: UUID, body: QualityReview):
    return invoke(lambda: service().send_to_review(str(production_id), **body.model_dump()))


@router.post('/videos/{production_id}/discard-queued')
def discard_queued(production_id: UUID, body: Confirmation):
    return invoke(lambda: service().discard_queued(str(production_id),body.confirmed))
