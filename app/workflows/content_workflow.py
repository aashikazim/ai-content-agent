
import os
from pathlib import Path
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from app.agents.trend_agent import TrendAgent
from app.agents.content_agent import ContentAgent
from app.agents.video_agent import VideoGenerationAgent

from app.services.storage_service import StorageService
from app.services.content_storage import ContentStorage
from app.services.job_service import JobService
from app.services.image_service import ImageService
from app.services.tts_service import TTSService
from app.services.video_renderer import VideoRenderer


class WorkflowState(TypedDict, total=False):
    job_id: str
    category: str
    region: str
    limit: int
    image_mode: str
    videos: list
    plan_id: str
    video_path: str


class ContentWorkflow:
    def __init__(
        self,
        job_service=None,
        trend_agent=None,
        content_agent=None,
        content_storage=None,
        video_agent_factory=None
    ):
        db_path = os.getenv(
            "DATABASE_PATH",
            "data/trends.db"
        )

        self.jobs = (
            job_service
            if job_service is not None
            else JobService(db_path)
        )

        self.trend_agent = (
            trend_agent
            if trend_agent is not None
            else TrendAgent(
                storage=StorageService(db_path)
            )
        )

        self.content_agent = (
            content_agent
            if content_agent is not None
            else ContentAgent()
        )

        self.content_storage = (
            content_storage
            if content_storage is not None
            else ContentStorage(db_path)
        )

        self.video_agent_factory = (
            video_agent_factory
            if video_agent_factory is not None
            else self.default_video_agent
        )

        self.graph = self.build_graph()

    def default_video_agent(self, image_mode):
        return VideoGenerationAgent(
            image_service=ImageService(
                mode=image_mode
            ),
            tts_service=TTSService(
                voice=os.getenv(
                    "TTS_VOICE", "en-us"
                )
            ),
            renderer=VideoRenderer(
                width=int(os.getenv(
                    "VIDEO_WIDTH", "1080"
                )),
                height=int(os.getenv(
                    "VIDEO_HEIGHT", "1920"
                )),
                fps=int(os.getenv(
                    "VIDEO_FPS", "30"
                ))
            )
        )

    def discover(self, state):
        self.jobs.update_job(
            state["job_id"],
            "discovering"
        )

        category = state["category"]

        videos = self.trend_agent.discover(
            region=state["region"],
            category=(
                "all" if category == "mixed"
                else category
            ),
            limit=state["limit"]
        )

        if not videos:
            raise ValueError(
                "No trending videos found"
            )

        return {"videos": videos}

    def plan(self, state):
        self.jobs.update_job(
            state["job_id"],
            "planning"
        )

        plan = self.content_agent.create_plan(
            state["videos"],
            category=state["category"],
            language=os.getenv(
                "CONTENT_LANGUAGE",
                "English"
            )
        )

        plan_id, _ = (
            self.content_storage.save_plan(plan)
        )

        self.jobs.update_job(
            state["job_id"],
            "planning",
            plan_id=plan_id
        )

        return {"plan_id": plan_id}

    def render(self, state):
        self.jobs.update_job(
            state["job_id"],
            "rendering"
        )

        plan_data = (
            self.content_storage.get_plan(
                state["plan_id"]
            )
        )

        agent = self.video_agent_factory(
            state["image_mode"]
        )

        output_dir = (
            Path("data/videos")
            / state["plan_id"]
        )

        output_path = agent.generate(
            plan_data,
            output_dir
        )

        self.jobs.update_job(
            state["job_id"],
            "pending_review",
            video_path=str(output_path)
        )

        return {
            "video_path": str(output_path)
        }

    def build_graph(self):
        builder = StateGraph(WorkflowState)

        builder.add_node(
            "discover",
            self.discover,
            retry_policy=RetryPolicy(
                max_attempts=2
            )
        )

        builder.add_node(
            "plan",
            self.plan,
            retry_policy=RetryPolicy(
                max_attempts=2
            )
        )

        builder.add_node(
            "render",
            self.render
        )

        builder.add_edge(START, "discover")
        builder.add_edge("discover", "plan")
        builder.add_edge("plan", "render")
        builder.add_edge("render", END)

        return builder.compile()

    def run(self, job_id):
        job = self.jobs.get_job(job_id)

        if job is None:
            raise KeyError(
                f"Job not found: {job_id}"
            )

        if job["status"] != "queued":
            raise ValueError(
                "Only queued jobs can be started"
            )

        initial_state = {
            "job_id": job_id,
            "category": job["category"],
            "region": job["region"],
            "limit": job["video_limit"],
            "image_mode": job["image_mode"]
        }

        try:
            return self.graph.invoke(
                initial_state
            )
        except Exception as exc:
            self.jobs.update_job(
                job_id,
                "failed",
                error=str(exc)
            )
            raise
