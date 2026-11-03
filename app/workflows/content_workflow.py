
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
from app.services.llm_service import LLMService
from app.models.generation_config import GenerationConfig
from app.services.provider_catalog import validate_for_execution
from app.services.openai_script_service import PaidScriptError
from app.services.script_generation_service import ScriptGenerationService


class WorkflowState(TypedDict, total=False):
    job_id: str
    category: str
    region: str
    limit: int
    image_mode: str
    videos: list
    plan_id: str
    video_path: str
    generation_config: dict
    generation_config_source: str


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

        self.trend_agent = trend_agent

        self.content_agent = (
            content_agent
            if content_agent is not None
            else ContentAgent()
        )
        self._custom_content_agent = content_agent is not None

        self.content_storage = (
            content_storage
            if content_storage is not None
            else ContentStorage(
                db_path, output_dir=os.getenv("CONTENT_PLANS_DIR", "data/plans")
            )
        )

        self.video_agent_factory = (
            video_agent_factory
            if video_agent_factory is not None
            else self.default_video_agent
        )
        self._custom_video_factory = video_agent_factory is not None

        self.graph = self.build_graph()

    def default_video_agent(self, image_mode, configuration=None):
        config = GenerationConfig.model_validate(configuration) if configuration else None
        width, height = config.output_dimensions if config else (
            int(os.getenv("VIDEO_WIDTH", "1080")), int(os.getenv("VIDEO_HEIGHT", "1920"))
        )
        image_width, image_height = (
            (768, 512) if width > height else (512, 512) if width == height else (512, 768)
        )
        return VideoGenerationAgent(
            image_service=ImageService(
                mode=image_mode,
                model_id=config.image_model if config else None,
                width=image_width,
                height=image_height,
                visual_style=config.visual_style if config else "cinematic",
            ),
            tts_service=TTSService(
                voice=os.getenv(
                    "TTS_VOICE", "en-us"
                )
            ),
            renderer=VideoRenderer(
                width=width,
                height=height,
                fps=config.fps if config else int(os.getenv("VIDEO_FPS", "30"))
            )
        )

    def discover(self, state):
        self.jobs.update_job(
            state["job_id"],
            "discovering"
        )

        if (state.get("generation_config") or {}).get("video_provider") == "google":
            return {"videos": []}

        category = state["category"]

        if (state.get("generation_config") or {}).get("source_mode", "trends") != "trends":
            return {"videos": []}

        if self.trend_agent is None:
            self.trend_agent = TrendAgent(storage=StorageService(self.jobs.db_path))

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

        if (state.get("generation_config") or {}).get("video_provider") == "google":
            return {"plan_id": self.jobs.get_job(state["job_id"])["plan_id"]}

        configuration = state.get("generation_config")
        config = GenerationConfig.model_validate(configuration) if configuration else None
        if config and config.script_provider == "openai":
            plan = ScriptGenerationService(self.jobs.db_path).run_for_job(
                state["job_id"], state["videos"]
            )
            plan_id, _ = self.content_storage.save_plan(plan)
            self.jobs.update_job(state["job_id"], "planning", plan_id=plan_id)
            return {"plan_id": plan_id}
        agent = self.content_agent
        if config and not self._custom_content_agent:
            agent = ContentAgent(llm=LLMService(model=config.script_model))
        options = {}
        if config and state.get("generation_config_source") == "configured":
            options = {
                "target_duration_seconds": config.duration_seconds,
                "aspect_ratio": config.aspect_ratio,
                "visual_style": config.visual_style,
                "max_scenes": config.max_shots,
                "topic": config.topic if config.source_mode == "topic" else "",
                "supplied_script": config.supplied_script if config.source_mode == "script" else "",
            }
        plan = agent.create_plan(
            state["videos"],
            category=state["category"],
            language=os.getenv(
                "CONTENT_LANGUAGE",
                "English"
            ),
            **options,
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

        if (state.get("generation_config") or {}).get("video_provider") == "google":
            from app.services.cloud_video_service import CloudVideoService
            service = CloudVideoService(self.jobs.db_path)
            production_id = service.production_id_for_job(state["job_id"])
            if not production_id:
                raise ValueError("Missing cloud production authorization")
            service.process(production_id, submit_queued=True)
            return {"video_path": self.jobs.get_job(state["job_id"]).get("video_path")}

        plan_data = (
            self.content_storage.get_plan(
                state["plan_id"]
            )
        )

        if self._custom_video_factory:
            agent = self.video_agent_factory(state["image_mode"])
        else:
            agent = self.default_video_agent(
                state["image_mode"],
                state.get("generation_config")
                if state.get("generation_config_source") == "configured" else None,
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
                max_attempts=2,
                retry_on=lambda exc: not isinstance(exc, PaidScriptError) and RetryPolicy().retry_on(exc),
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
            "image_mode": job["image_mode"],
            "generation_config": job.get("generation_config"),
            "generation_config_source": job.get("generation_config_source", "legacy"),
        }

        try:
            if job.get("generation_config"):
                config = GenerationConfig.model_validate(job["generation_config"])
                validate_for_execution(config)
                if config.image_mode != job["image_mode"]:
                    raise ValueError("Saved image mode conflicts with the job configuration")
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
