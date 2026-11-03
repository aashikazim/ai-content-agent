"""Local workflow integration with real media tools and isolated data."""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from PIL import Image

from app.agents.content_agent import ContentAgent
from app.models.content_plan import ContentPlan
from app.models.generation_config import GenerationConfig
from app.models.video import Video
from app.services.content_storage import ContentStorage
from app.services.image_service import ImageService
from app.services.job_service import JobService
from app.workflows.content_workflow import ContentWorkflow
from tests.test_content_plan import valid_data


def candidate():
    return Video("fixture", "Fixture topic", "28", "Fixture channel",
                 datetime.now(timezone.utc), 30, 100, 10, 1,
                 "https://example.com/fixture")


def test_configured_local_graph_renders_real_square_mp4(tmp_path, monkeypatch):
    for command in ("ffmpeg", "ffprobe", "espeak-ng"):
        if not shutil.which(command):
            pytest.skip(f"{command} is required for the local media integration check")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    jobs = JobService(tmp_path / "test.db")
    config = GenerationConfig(duration_seconds=30, aspect_ratio="1:1",
                              resolution="720p", fps=24, max_shots=2, image_mode="text")
    job_id = jobs.create_job(generation_config=config)
    data = valid_data()
    data["scenes"][1]["duration_seconds"] = 15
    llm = Mock()
    llm.generate_plan.return_value = ContentPlan.model_validate(data)
    trend = Mock()
    trend.discover.return_value = [candidate()]
    workflow = ContentWorkflow(
        job_service=jobs, trend_agent=trend, content_agent=ContentAgent(llm=llm),
        content_storage=ContentStorage(jobs.db_path, tmp_path / "plans"),
    )
    workflow.run(job_id)
    job = jobs.get_job(job_id)
    assert job["status"] == "pending_review"
    assert job["generation_config"]["aspect_ratio"] == "1:1"
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
        job["video_path"],
    ], check=True, capture_output=True, text=True)
    media = json.loads(result.stdout)
    video = next(stream for stream in media["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"], video["avg_frame_rate"]) == (720, 720, "24/1")
    assert abs(float(media["format"]["duration"]) - 30) < 0.2
    assert any(stream["codec_type"] == "audio" for stream in media["streams"])
    subprocess.run(["ffmpeg", "-v", "error", "-i", job["video_path"], "-f", "null", "-"],
                   check=True, capture_output=True)
    assert "exactly 30 seconds" in llm.generate_plan.call_args.args[0]
    assert "1:1" in llm.generate_plan.call_args.args[0]


def test_legacy_renderer_environment_and_factory_signature_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_WIDTH", "360")
    monkeypatch.setenv("VIDEO_HEIGHT", "640")
    monkeypatch.setenv("VIDEO_FPS", "15")
    workflow = ContentWorkflow(job_service=JobService(tmp_path / "test.db"),
                               content_agent=Mock(),
                               content_storage=ContentStorage(tmp_path / "test.db", tmp_path / "plans"))
    agent = workflow.default_video_agent("text")
    assert (agent.renderer.width, agent.renderer.height, agent.renderer.fps) == (360, 640, 15)
    assert workflow.trend_agent is None


def test_unsupported_persisted_cloud_config_fails_before_discovery(tmp_path):
    jobs = JobService(tmp_path / "test.db")
    job_id = jobs.create_job()
    with jobs.connect() as conn:
        saved = jobs.get_job(job_id)["generation_config"]
        saved.update(video_provider="kling", video_model="kling-4.0")
        conn.execute("UPDATE job_generation_configs SET config_json = ? WHERE job_id = ?",
                     (json.dumps(saved), job_id))
    trend = Mock()
    workflow = ContentWorkflow(job_service=jobs, trend_agent=trend, content_agent=Mock(),
                               content_storage=ContentStorage(jobs.db_path, tmp_path / "plans"))
    with pytest.raises(ValueError, match="retired"):
        workflow.run(job_id)
    trend.discover.assert_not_called()
    assert jobs.get_job(job_id)["status"] == "failed"


def test_text_cards_do_not_import_torch(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    path = ImageService(mode="text", width=768, height=512).generate(
        "Fixture local landscape", tmp_path / "scene.png")
    with Image.open(path) as image:
        assert image.size == (768, 512)


def test_selected_duration_repaired_without_accepting_wrong_plan():
    first = ContentPlan.model_validate(valid_data())
    second = valid_data()
    second["scenes"][1]["duration_seconds"] = 15
    llm = Mock()
    llm.generate_plan.side_effect = [first, ContentPlan.model_validate(second)]
    plan = ContentAgent(llm=llm).create_plan(
        [candidate()], target_duration_seconds=30, max_scenes=2, visual_style="sketch")
    assert plan.total_duration == 30
    assert llm.generate_plan.call_count == 2
    assert "must be exactly 30s" in llm.generate_plan.call_args.args[0]
