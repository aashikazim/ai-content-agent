"""Read models for the browser dashboard, backed by the existing local services."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from app.models.long_content_plan import parse_content_plan
from app.services.analytics_storage import AnalyticsStorage
from app.services.feedback_service import FeedbackService
from app.services.publishing_storage import PublishingStorage


class WebDashboardService:
    def __init__(self, jobs, content_storage):
        self.jobs = jobs
        self.content_storage = content_storage
        self.publications = PublishingStorage(jobs.db_path)
        self.analytics = AnalyticsStorage(
            os.getenv("ANALYTICS_DATABASE_PATH", jobs.db_path)
        )

    def publication_records(self, job_id=None):
        records = self.publications.list_publications()
        if job_id is not None:
            records = [record for record in records if record["job_id"] == job_id]
        by_id = {record["publication_id"]: record for record in records}
        for record in records:
            record["events"] = []
        with self.publications.connect() as conn:
            for row in conn.execute(
                "SELECT * FROM publication_events ORDER BY event_id"
            ).fetchall():
                record = by_id.get(row["publication_id"])
                if record is not None:
                    record["events"].append(dict(row))
        return records

    def plan_for(self, job):
        if not job.get("plan_id"):
            return None
        try:
            return parse_content_plan(
                self.content_storage.get_plan(job["plan_id"])
            ).model_dump(mode="json")
        except (KeyError, OSError, ValueError):
            return None

    def job_card(self, job, plan=None):
        plan = plan if plan is not None else self.plan_for(job)
        scenes = plan.get("scenes", []) if plan else []
        return {
            **job,
            "title": plan.get("title") if plan else None,
            "topic": plan.get("topic") if plan else None,
            "duration_seconds": sum(s.get("duration_seconds", 0) for s in scenes),
            "scene_count": len(scenes),
            "video_exists": bool(
                job.get("video_path") and Path(job["video_path"]).is_file()
            ),
        }

    def overview(self):
        jobs = [self.job_card(job) for job in self.jobs.list_jobs(limit=1000)]
        publications = self.publication_records()
        snapshots = self.analytics.latest_snapshots()
        with self.jobs.connect() as conn:
            counts = dict(conn.execute(
                "SELECT status, COUNT(*) FROM jobs GROUP BY status"
            ).fetchall())
        categories = {job["job_id"]: job["category"] for job in jobs}
        publication_jobs = {p["publication_id"]: p["job_id"] for p in publications}
        performance = [
            {
                **snapshot,
                "category": categories.get(
                    publication_jobs.get(snapshot["publication_id"]), "unknown"
                ),
            }
            for snapshot in snapshots
        ]
        return {
            "summary": {
                "total_jobs": sum(counts.values()),
                "pending_review": counts.get("pending_review", 0),
                "approved": counts.get("approved", 0),
                "failed_jobs": counts.get("failed", 0),
                "active_jobs": sum(counts.get(s, 0) for s in (
                    "queued", "discovering", "planning", "rendering", "processing"
                )),
                "publication_attempts": len(publications),
                "reported_views": sum(s["views"] or 0 for s in snapshots),
            },
            "jobs": jobs,
            "publications": publications,
            "analytics": performance,
            "category_performance": FeedbackService().rank_categories(performance),
            "platform_settings": {
                "youtube_privacy": os.getenv("YOUTUBE_PRIVACY_STATUS", "private")
            },
        }

    @staticmethod
    def external_url(value):
        try:
            parsed = urlsplit(value) if isinstance(value, str) else None
            return value if parsed and parsed.scheme in ("https", "http") and parsed.netloc else None
        except ValueError:
            return None

    def manifest_for(self, job, plan):
        base = Path(job["video_path"]).parent if job.get("video_path") else None
        saved = {}
        warning = None
        if base:
            path = base / "manifest.json"
            if path.is_file():
                try:
                    if path.stat().st_size > 1024 * 1024:
                        raise ValueError("Manifest too large")
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(saved, dict):
                        raise ValueError("Manifest must be an object")
                except (OSError, ValueError):
                    saved = {}
                    warning = "The scene manifest could not be read."
            else:
                warning = "Scene manifest unavailable for this existing video."
        entries = saved.get("scenes", [])
        if not isinstance(entries, list):
            entries = []
            warning = "The scene manifest could not be read."
        saved_scenes = {
            scene.get("scene_number"): scene
            for scene in entries
            if isinstance(scene, dict) and isinstance(scene.get("scene_number"), int)
        }
        scenes = []
        for scene in (plan or {}).get("scenes", []):
            number = scene["scene_number"]
            entry = saved_scenes.get(number, {})
            prefix = f"/api/v1/jobs/{job['job_id']}/scenes/{number}"
            image = base / f"scene_{number}.png" if base else None
            audio = base / f"scene_{number}.wav" if base else None
            scenes.append({
                "scene_number": number,
                "duration": scene.get("duration_seconds"),
                "image_url": prefix + "/image" if image and image.is_file() else None,
                "audio_url": prefix + "/audio" if audio and audio.is_file() else None,
                "status": entry.get("status", "available" if image and image.is_file() else "missing"),
                "source_url": self.external_url(entry.get("source_url")),
                "license": entry.get("license"),
                "attribution": entry.get("attribution"),
            })
        return {"status": saved.get("status"), "warning": warning, "scenes": scenes, "factual_status":saved.get("factual_status"), "technical_quality":saved.get("technical_quality"), "warnings":saved.get("warnings", [])}

    def details(self, job_id):
        job = self.jobs.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        plan = self.plan_for(job)
        publications = self.publication_records(job_id)
        return {
            "job": self.job_card(job, plan),
            "plan": plan,
            "video_exists": bool(job.get("video_path") and Path(job["video_path"]).is_file()),
            "manifest": self.manifest_for(job, plan),
            "publications": publications,
        }
