"""Local admin browser sessions. Passwords and session tokens never reach the UI."""

import os
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.services.admin_auth_service import (
    AdminAuthService,
    AuthInvalidCredentials,
    AuthNotConfigured,
    AuthRateLimited,
)
from app.services.content_storage import ContentStorage
from app.services.job_service import JobService
from app.services.web_dashboard_service import WebDashboardService


router = APIRouter(prefix="/api/v1")
COOKIE_NAME = "admin_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_auth_service():
    return AdminAuthService()


def get_jobs():
    return JobService(os.getenv("DATABASE_PATH", "data/trends.db"))


def get_storage():
    return ContentStorage(
        os.getenv("DATABASE_PATH", "data/trends.db"),
        output_dir=os.getenv("CONTENT_PLANS_DIR", "data/plans"),
    )


def ensure_same_origin(request: Request):
    """Reject foreign browser origins, including sibling same-site origins."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-origin requests are not allowed")
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            raise HTTPException(403, "Cross-origin requests are not allowed") from exc
        if (
            parsed.scheme != request.url.scheme
            or parsed.netloc != request.url.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(403, "Cross-origin requests are not allowed")


def browser_session(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    return get_auth_service().get_session(token) if token else None


def ensure_csrf(request: Request, session: dict):
    if request.method in UNSAFE_METHODS:
        ensure_same_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not secrets.compare_digest(
            supplied.encode("utf-8"), session["csrf_token"].encode("utf-8")
        ):
            raise HTTPException(403, "Invalid or missing CSRF token")


def require_session(request: Request):
    session = browser_session(request)
    if session is None:
        raise HTTPException(401, "Admin login required")
    ensure_csrf(request, session)
    return session


def session_payload(session):
    return {
        "authenticated": True,
        "configured": True,
        "username": session["username"],
        "csrf_token": session["csrf_token"],
        "expires_at": session["expires_at"],
    }


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


@router.get("/auth/session")
def get_session(request: Request):
    auth = get_auth_service()
    token = request.cookies.get(COOKIE_NAME)
    session = auth.get_session(token) if token else None
    if session:
        return session_payload(session)
    return {"authenticated": False, "configured": auth.configured}


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    ensure_same_origin(request)
    auth = get_auth_service()
    try:
        session = auth.login(
            body.username,
            body.password,
            request.client.host if request.client else "local",
        )
    except AuthNotConfigured as exc:
        raise HTTPException(
            503, "Admin account is not configured. Run python scripts/setup_admin.py."
        ) from exc
    except AuthRateLimited as exc:
        raise HTTPException(
            429,
            "Too many sign-in attempts. Please try again later.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except AuthInvalidCredentials as exc:
        raise HTTPException(401, "Invalid username or password") from exc

    # Rotate rather than reuse a previously authenticated browser session.
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        auth.logout(previous)
    import time

    response.set_cookie(
        COOKIE_NAME,
        session["token"],
        max_age=max(1, int(session["expires_at"] - time.time())),
        httponly=True,
        secure=(
            request.url.scheme == "https"
            or os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true"
        ),
        samesite="strict",
        path="/",
    )
    return session_payload(session)


@router.post("/auth/logout")
def logout(
    request: Request, response: Response, session=Depends(require_session)
):
    get_auth_service().logout(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return {"authenticated": False}


def get_dashboard_service():
    return WebDashboardService(get_jobs(), get_storage())


@router.get("/dashboard", dependencies=[Depends(require_session)])
def dashboard():
    return get_dashboard_service().overview()


@router.get("/jobs/{job_id}/details", dependencies=[Depends(require_session)])
def details(job_id: str):
    try:
        return get_dashboard_service().details(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Job not found") from exc
