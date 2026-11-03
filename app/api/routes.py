
import os
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agents.trend_agent import CATEGORIES
from app.services.job_service import JobService
from app.services.content_storage import ContentStorage
from app.workflows.content_workflow import ContentWorkflow
from app.services.publishing_service import PublishingService
from app.services.publishing_storage import PublishingStorage

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
    x_api_key: str | None = Header(default=None)
):
    expected = os.getenv("CONTENT_API_KEY")

    if not expected:
        raise HTTPException(
            status_code=503,
            detail="CONTENT_API_KEY is not configured"
        )

    import secrets

    if not x_api_key or not secrets.compare_digest(
        x_api_key, expected
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )


def get_jobs():
    return JobService(DB_PATH)


def get_storage():
    return ContentStorage(DB_PATH)


def execute_job(job_id):
    workflow = ContentWorkflow()

    try:
        workflow.run(job_id)
    except Exception:
        # Workflow.run() records the failure in SQLite.
        # Avoid exposing the full exception to API clients.
        import logging
        logging.exception(
            "Content job %s failed", job_id
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


@router.post(
    "/jobs/{job_id}/review",
    dependencies=[Depends(require_api_key)]
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
        return jobs.review_job(
            job_id,
            request.decision
        )
    except ValueError as exc:
        raise HTTPException(
            409, str(exc)
        ) from exc


class PublishRequest(BaseModel):
    platform: Literal["youtube", "facebook"]
    dry_run: bool = True


@router.post(
    "/jobs/{job_id}/publish",
    dependencies=[Depends(require_api_key)]
)
def publish_job(
    job_id: str,
    request: PublishRequest
):
    service = PublishingService()

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
