
import os
from pathlib import Path
from typing import Literal

import secrets
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agents.trend_agent import CATEGORIES
from app.services.job_service import JobService
from app.services.content_storage import ContentStorage
from app.workflows.content_workflow import ContentWorkflow
from app.services.publishing_service import PublishingService
from app.services.publishing_storage import PublishingStorage
from app.services.review_dashboard_service import ReviewDashboardService
from app.api.admin_routes import browser_session, ensure_csrf, require_session
from app.models.generation_config import GenerationConfig
from app.services.generation_storage import GenerationStorage
from app.services.provider_catalog import (
    get_catalog, validate_for_draft, validate_for_execution,
)
from app.services.script_generation_service import ScriptGenerationService
from app.services.openai_script_service import OpenAIScriptService, PaidScriptError

router = APIRouter(prefix="/api/v1")

DB_PATH = os.getenv(
    "DATABASE_PATH", "data/trends.db"
)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = "mixed"
    region: str = Field(
        default="US",
        pattern=r"^[A-Z]{2}$"
    )
    limit: int = Field(default=20, ge=1, le=50)
    image_mode: Literal[
        "text", "diffusion"
    ] = "text"
    generation_config: GenerationConfig | None = None
    script_quote_id: UUID | None = None
    script_cost_confirmed: bool = Field(default=False, strict=True)


class ConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration: GenerationConfig


class ScriptRequest(ConfigurationRequest):
    category: str = "mixed"
    region: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    limit: int = Field(default=5, ge=1, le=50)
    purpose: Literal["script", "video"] = "script"
    parent_attempt_id: UUID | None = None
    revision_instructions: str = Field(default="", max_length=2000)
    quote_id: UUID | None = None
    confirmed: bool = Field(default=False, strict=True)


class ScriptReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str = Field(min_length=1, max_length=500)
    confirmed: bool = Field(default=False, strict=True)


def script_request(body):
    if body.category not in (*CATEGORIES.keys(), "mixed"):
        raise ValueError("Unsupported category")
    return ScriptGenerationService.request(
        body.configuration, category=body.category, region=body.region,
        limit=body.limit, purpose=body.purpose, parent_attempt_id=body.parent_attempt_id,
        revision_instructions=body.revision_instructions,
    )


def _execute_script_inline(attempt_id):
    ScriptGenerationService(DB_PATH).run(attempt_id)


def execute_script(attempt_id):
    if os.getenv('DURABLE_WORKERS','false').lower()=='true':
        from app.services.work_queue import WorkQueue
        queue=WorkQueue(DB_PATH)
        # Chapter continuation is a new explicit dispatch of the same draft ID.
        # Preserve the prior queue attempt; only the script service's queued state
        # (set by its separately authorized resume/revision) can rearm dispatch.
        with queue.connect() as conn:
            prior=conn.execute("SELECT status FROM app_work_queue WHERE kind='script' AND target_id=?",(str(attempt_id),)).fetchone()
            state=conn.execute('SELECT status FROM script_generation_attempts WHERE attempt_id=?',(str(attempt_id),)).fetchone()
        from uuid import uuid4
        dispatch=str(uuid4()) if prior and prior['status'] in ('finished','interrupted') and state and state['status']=='queued' else None
        queue.enqueue('script',attempt_id,dispatch_key=dispatch)
    else:
        _execute_script_inline(attempt_id)


class ReviewRequest(BaseModel):
    decision: Literal[
        "approved", "rejected"
    ]


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None)
):
    session = browser_session(request)
    if session:
        ensure_csrf(request, session)
        return {"kind": "session", **session}

    expected = os.getenv("CONTENT_API_KEY")

    if not expected:
        raise HTTPException(
            status_code=503,
            detail="CONTENT_API_KEY is not configured"
        )

    if not x_api_key or not secrets.compare_digest(
        x_api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )
    return {"kind": "api_key"}


def require_review_key(
    request: Request,
    x_review_key: str | None = Header(default=None)
):
    session = browser_session(request)
    if session:
        ensure_csrf(request, session)
        return {"kind": "session", **session}
    expected = os.getenv("CONTENT_REVIEW_API_KEY")
    if not expected or not x_review_key or not secrets.compare_digest(
        x_review_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(403, "Admin login or review permission required")
    return {"kind": "review_key"}


def require_publish_key(
    request: Request,
    x_publish_key: str | None = Header(default=None)
):
    session = browser_session(request)
    if session:
        ensure_csrf(request, session)
        return {"kind": "session", **session}
    expected = os.getenv("CONTENT_PUBLISH_API_KEY")
    if not expected or not x_publish_key or not secrets.compare_digest(
        x_publish_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(403, "Admin login or publishing permission required")
    return {"kind": "publish_key"}


def get_jobs():
    return JobService(DB_PATH)


def get_storage():
    return ContentStorage(
        DB_PATH, output_dir=os.getenv("CONTENT_PLANS_DIR", "data/plans")
    )


def _execute_job_inline(job_id):
    try:
        workflow = ContentWorkflow()
        workflow.run(job_id)
    except Exception:
        # Workflow.run() records the failure in SQLite.
        # Avoid exposing the full exception to API clients.
        import logging
        logging.exception(
            "Content job %s failed", job_id
        )
        # Initialization may fail before Workflow.run() can record the error.
        job = get_jobs().get_job(job_id)
        if job and job["status"] == "queued":
            get_jobs().update_job(
                job_id, "failed", error="Generation could not start. Check server logs."
            )


def execute_job(job_id):
    if os.getenv('DURABLE_WORKERS','false').lower()=='true':
        from app.services.work_queue import WorkQueue
        WorkQueue(DB_PATH).enqueue('job',job_id)
    else:
        _execute_job_inline(job_id)


@router.get("/generation/catalog", dependencies=[Depends(require_api_key)])
def generation_catalog():
    return get_catalog()


@router.post("/generation/preflight", dependencies=[Depends(require_api_key)])
def generation_preflight(body: ConfigurationRequest):
    config = body.configuration
    errors = []
    try:
        validate_for_execution(config)
    except ValueError as exc:
        errors.append(str(exc))
    warnings = ["Account/model access is unverified. Cloud video requires a completed shot plan and a separate video cost quote."]
    if config.script_provider in {"openai", "anthropic"}:
        warnings.append("Paid script generation requires an administrator's current cost quote and explicit confirmation.")
    if config.video_provider == "local":
        warnings.append(
            "Local output uses still images or text cards, not moving footage or animated actors."
        )
    return {
        "executable": not errors,
        "errors": errors,
        "warnings": warnings,
        "configuration": config.model_dump(mode="json"),
        "estimated_cost_usd": None,
        "paid_request": False,
        "submission_route": "script_then_video_quote" if config.video_provider in {"google", "runway", "local_cinematic"} else "local_job",
    }


@router.post(
    "/generation/configurations", status_code=201,
    dependencies=[Depends(require_api_key)]
)
def save_generation_configuration(body: ConfigurationRequest):
    try:
        validate_for_draft(body.configuration)
        return GenerationStorage(DB_PATH).save_draft(body.configuration)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/generation/configurations", dependencies=[Depends(require_api_key)])
def generation_configurations():
    return {"configurations": GenerationStorage(DB_PATH).list_drafts()}


@router.get("/generation/openai-models", dependencies=[Depends(require_session)])
def available_openai_models():
    try:
        return {"models": OpenAIScriptService().available_models(), "generation_submitted": False}
    except PaidScriptError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/generation/script-quotes")
def quote_script(body: ScriptRequest, principal=Depends(require_session)):
    try:
        return ScriptGenerationService(DB_PATH).quote(script_request(body), principal["username"])
    except (ValueError, PaidScriptError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/generation/scripts", status_code=202)
def create_script(body: ScriptRequest, background_tasks: BackgroundTasks,
                  principal=Depends(require_session)):
    if body.purpose != "script":
        raise HTTPException(422, "Use the job endpoint for video production")
    try:
        identifier = ScriptGenerationService(DB_PATH).submit(
            script_request(body), principal["username"], quote_id=body.quote_id,
            confirmed=body.confirmed,
        )
    except (ValueError, PaidScriptError) as exc:
        raise HTTPException(422, str(exc)) from exc
    background_tasks.add_task(execute_script, identifier)
    return {"attempt_id": identifier, "status": "queued"}


@router.get("/generation/scripts", dependencies=[Depends(require_session)])
def list_scripts():
    return {"attempts": ScriptGenerationService(DB_PATH).list_attempts()}


class ScriptContinuation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: bool = Field(default=False, strict=True)


@router.post('/generation/scripts/{attempt_id}/continue', status_code=202)
def continue_script(attempt_id: UUID, body: ScriptContinuation, background_tasks: BackgroundTasks,
                    principal=Depends(require_session)):
    try:
        identifier = ScriptGenerationService(DB_PATH).resume_chapters(str(attempt_id), principal['username'], body.confirmed)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    background_tasks.add_task(execute_script, identifier)
    return {'attempt_id': identifier, 'status': 'queued'}


class ChapterRevisionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision_instructions: str=Field(default='',max_length=2000)


class ChapterRevisionSubmission(BaseModel):
    model_config=ConfigDict(extra='forbid')
    quote_id: UUID
    confirmed: bool=Field(default=False,strict=True)


@router.post('/generation/scripts/{attempt_id}/parts/{part_number}/quote')
def quote_chapter(attempt_id: UUID, part_number: int, body: ChapterRevisionRequest, principal=Depends(require_session)):
    try:
        return ScriptGenerationService(DB_PATH).quote_chapter_revision(str(attempt_id),part_number,principal['username'],body.revision_instructions)
    except (ValueError,PaidScriptError) as error:
        raise HTTPException(422,str(error)) from None


@router.post('/generation/scripts/{attempt_id}/parts/revisions',status_code=202)
def revise_chapter(attempt_id: UUID, body: ChapterRevisionSubmission, background_tasks: BackgroundTasks,principal=Depends(require_session)):
    try:
        identifier=ScriptGenerationService(DB_PATH).submit_chapter_revision(str(attempt_id),str(body.quote_id),principal['username'],body.confirmed)
    except ValueError as error:
        raise HTTPException(422,str(error)) from None
    background_tasks.add_task(execute_script,identifier)
    return {'attempt_id':identifier,'status':'queued'}


@router.post('/generation/scripts/{attempt_id}/parts/revisions/{revision_id}/apply')
def apply_chapter(attempt_id: UUID,revision_id: UUID,body: ScriptContinuation,principal=Depends(require_session)):
    try:
        return ScriptGenerationService(DB_PATH).apply_saved_chapter_revision(str(attempt_id),str(revision_id),principal['username'],body.confirmed)
    except ValueError as error:
        raise HTTPException(422,str(error)) from None


@router.post('/generation/scripts/{attempt_id}/parts/{part_number}/reconcile')
def reconcile_chapter(attempt_id: UUID,part_number: int,body: ScriptReconciliation,principal=Depends(require_session)):
    try:
        return ScriptGenerationService(DB_PATH).reconcile_chapter(str(attempt_id),part_number,principal['username'],body.notes,body.confirmed)
    except ValueError as error:
        raise HTTPException(422,str(error)) from None


@router.get("/generation/scripts/{attempt_id}", dependencies=[Depends(require_session)])
def get_script(attempt_id: UUID):
    attempt = ScriptGenerationService(DB_PATH).get(str(attempt_id))
    if not attempt:
        raise HTTPException(404, "Script attempt not found")
    return attempt


@router.post("/generation/scripts/{attempt_id}/reconcile")
def reconcile_script(attempt_id: UUID, body: ScriptReconciliation,
                     principal=Depends(require_session)):
    try:
        return ScriptGenerationService(DB_PATH).reconcile(
            str(attempt_id), principal["username"], body.notes, body.confirmed
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post(
    "/jobs",
    status_code=202,
)
def create_job(
    request: JobRequest,
    background_tasks: BackgroundTasks,
    principal=Depends(require_api_key),
):
    if request.category not in (
        *CATEGORIES.keys(), "mixed"
    ):
        raise HTTPException(
            status_code=422,
            detail="Unsupported category"
        )

    jobs = get_jobs()
    config = request.generation_config
    if config and config.script_provider in {"openai", "anthropic"} and principal["kind"] != "session":
        raise HTTPException(403, "Paid script generation requires an administrator session and cost confirmation")
    if config is not None and "image_mode" in request.model_fields_set:
        if request.image_mode != config.image_mode:
            raise HTTPException(422, "image_mode conflicts with generation_config.image_mode")
    try:
        job_id = jobs.create_job(
            category=request.category,
            region=request.region,
            limit=request.limit,
            image_mode=request.image_mode,
            generation_config=config,
            script_quote_id=request.script_quote_id,
            script_cost_confirmed=request.script_cost_confirmed,
            actor=principal.get("username"),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    background_tasks.add_task(
        execute_job, job_id
    )

    return {
        "job_id": job_id,
        "status": "queued"
    }


@router.get(
    "/jobs/{job_id}",
    dependencies=[Depends(require_api_key)]
)
def get_job(job_id: str):
    job = get_jobs().get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return job


@router.get(
    "/jobs/{job_id}/plan",
    dependencies=[Depends(require_api_key)]
)
def get_plan(job_id: str):
    job = get_jobs().get_job(job_id)

    if job is None:
        raise HTTPException(404, "Job not found")

    if not job["plan_id"]:
        raise HTTPException(
            409, "Plan not generated yet"
        )

    return get_storage().get_plan(
        job["plan_id"]
    )


@router.get(
    "/jobs/{job_id}/video",
    dependencies=[Depends(require_api_key)]
)
def get_video(job_id: str):
    job = get_jobs().get_job(job_id)

    if job is None:
        raise HTTPException(404, "Job not found")

    if not job["video_path"]:
        raise HTTPException(
            409, "Video not generated yet"
        )

    path = Path(job["video_path"])

    if not path.is_file():
        raise HTTPException(
            404, "Video file is missing"
        )

    return FileResponse(
        path,
        media_type="video/mp4",
        filename="final_video.mp4"
    )


@router.get(
    "/jobs/{job_id}/scenes/{scene_number}/{asset_type}",
    dependencies=[Depends(require_api_key)]
)
def get_scene_asset(job_id: str, scene_number: int, asset_type: Literal["image", "audio"]):
    job = get_jobs().get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.get("video_path") or not job.get("plan_id"):
        raise HTTPException(404, "Scene asset unavailable")
    try:
        plan = get_storage().get_plan(job["plan_id"])
    except (KeyError, OSError, ValueError) as exc:
        raise HTTPException(404, "Scene plan unavailable") from exc
    if scene_number not in [s["scene_number"] for s in plan.get("scenes", [])]:
        raise HTTPException(404, "Scene not found")
    extension, content_type = (
        ("png", "image/png") if asset_type == "image" else ("wav", "audio/wav")
    )
    path = Path(job["video_path"]).parent / f"scene_{scene_number}.{extension}"
    if not path.is_file():
        raise HTTPException(404, "Scene asset unavailable")
    return FileResponse(path, media_type=content_type)


@router.post(
    "/jobs/{job_id}/review",
    dependencies=[Depends(require_review_key)]
)
def review_job(
    job_id: str,
    request: ReviewRequest
):
    jobs = get_jobs()

    if jobs.get_job(job_id) is None:
        raise HTTPException(
            404, "Job not found"
        )

    try:
        if request.decision == "rejected":
            return jobs.review_job(job_id, request.decision)
        return ReviewDashboardService(jobs, get_storage()).review(
            job_id,
            request.decision
        )
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(
            409, "Content plan is unavailable" if isinstance(exc, (KeyError, OSError)) else str(exc)
        ) from exc


class PublishRequest(BaseModel):
    platform: Literal["youtube", "facebook"]
    dry_run: bool = True
    confirmed: bool = False


@router.post(
    "/jobs/{job_id}/publish",
)
def publish_job(
    job_id: str,
    request: PublishRequest,
    principal=Depends(require_publish_key)
):
    if principal["kind"] == "session" and not request.dry_run and not request.confirmed:
        raise HTTPException(409, "Explicit confirmation is required for a live upload")
    service = PublishingService(
        jobs=get_jobs(),
        content_storage=get_storage(),
        publications=PublishingStorage(DB_PATH),
    )

    try:
        return service.publish(
            job_id=job_id,
            platform=request.platform,
            dry_run=request.dry_run
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc)
        ) from exc

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        ) from exc

    except Exception as exc:
        import logging
        logging.exception(
            "Publishing failed for job %s", job_id
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Publishing failed or its outcome "
                "is uncertain. Inspect the "
                "publication record."
            )
        ) from exc


@router.get(
    "/publications/{publication_id}",
    dependencies=[Depends(require_api_key)]
)
def get_publication(publication_id: str):
    storage = PublishingStorage(DB_PATH)

    publication = storage.get(publication_id)

    if publication is None:
        raise HTTPException(
            status_code=404,
            detail="Publication not found"
        )

    return publication
