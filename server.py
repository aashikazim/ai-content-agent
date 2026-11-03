
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from app.api.routes import router
from app.api.admin_routes import router as admin_router

app = FastAPI(
    title="AI Content Agent API",
    description=(
        "Local AI content generation and "
        "video production on DGX Spark"
    ),
    version="0.8.0"
)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError):
    # Pydantic's default error JSON echoes submitted values, including accidental
    # credentials. Return useful field locations without reflecting request data.
    return JSONResponse(status_code=422, content={"detail": [
        {"type": error["type"], "loc": list(error["loc"]), "msg": error["msg"]}
        for error in exc.errors()
    ]})


@app.middleware("http")
async def browser_security(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ai-content-agent"
    }


app.include_router(router)
app.include_router(admin_router)

# The web UI shares the API's origin; cookies also authenticate video requests.
# Building the UI does not require Node.js on the production server afterwards.
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="web-assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if path and Path(path).suffix in {".svg", ".ico", ".png", ".webmanifest"}:
        asset = (FRONTEND_DIST / path).resolve()
        if asset.is_relative_to(FRONTEND_DIST.resolve()) and asset.is_file():
            return FileResponse(asset)
        raise HTTPException(404, "Not found")
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        return HTMLResponse(
            "<h1>AI Content Agent</h1><p>Build the dashboard first:</p>"
            "<pre>cd frontend\nnpm ci\nnpm run build</pre>", status_code=503
        )
    return FileResponse(index, headers={"Cache-Control": "no-store"})
