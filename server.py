
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from app.api.routes import router

app = FastAPI(
    title="AI Content Agent API",
    description=(
        "Local AI content generation and "
        "video production on DGX Spark"
    ),
    version="0.4.0"
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ai-content-agent"
    }


app.include_router(router)
