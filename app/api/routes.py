
import os
from pathlib import Path
from typing import Literal

import secrets

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agents.trend_agent import CATEGORIES
from app.services.job_service import JobService
from app.services.content_storage import ContentStorage
from app.workflows.content_workflow import ContentWorkflow
from app.services.publishing_service import PublishingService
from app.services.publishing_storage import PublishingStorage
from app.services.review_dashboard_service import ReviewDashboardService
from app.api.admin_routes import browser_session, ensure_csrf

router = APIRouter(prefix="/api/v1")

DB_PATH = os.getenv(
    "DATABASE_PATH", "data/trends.db"
)


class JobRequest(BaseModel):
    category: str = "mixed"
    region: str = Field(
        default="US",
        pattern=r"^[A-Z]{2}$"
    )
    limit: int = Field(default=20, ge=1, le=50)
    image_mode: Literal[
        "text", "diffusion"
    ] = "text"


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


def execute_job(job_id):
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


@router.post(
    "/jobs",
    status_code=202,
    dependencies=[Depends(require_api_key)]
)
def create_job(
    request: JobRequest,
    background_tasks: BackgroundTasks
):
    if request.category not in (
        *CATEGORIES.keys(), "mixed"
    ):
        raise HTTPException(
            status_code=422,
            detail="Unsupported category"
        )

    jobs = get_jobs()

    job_id = jobs.create_job(
        category=request.category,
        region=request.region,
        limit=request.limit,
        image_mode=request.image_mode
    )

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
