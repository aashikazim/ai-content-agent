
import json
import sqlite3
import uuid

from datetime import datetime, timezone
from pathlib import Path

from app.models.generation_config import GenerationConfig
from app.services.generation_storage import GenerationStorage
from app.services.provider_catalog import validate_for_execution


class JobService:
    def __init__(self, db_path="data/trends.db"):
        self.db_path = str(db_path)

        Path(self.db_path).parent.mkdir(
            parents=True, exist_ok=True
        )

        self.initialize()
        GenerationStorage(self.db_path)

    def connect(self):
        conn = sqlite3.connect(
            self.db_path,
            timeout=30
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def initialize(self):
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    region TEXT NOT NULL,
                    video_limit INTEGER NOT NULL,
                    image_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_id TEXT,
                    video_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def create_job(
        self,
        category="mixed",
        region="US",
        limit=20,
        image_mode="text",
        generation_config=None,
        script_quote_id=None,
        script_cost_confirmed=False,
        actor=None,
    ):
        legacy_request = generation_config is None
        config = (
            GenerationConfig.from_legacy(image_mode)
            if legacy_request else GenerationConfig.model_validate(generation_config)
        )
        validate_for_execution(config)
        script_service = None
        script_request = None
        if config.script_provider == "openai":
            from app.services.script_generation_service import ScriptGenerationService
            if not actor:
                raise ValueError("Paid script generation requires an authenticated administrator")
            script_service = ScriptGenerationService(self.db_path)
            script_request = script_service.request(
                config, category=category, region=region, limit=limit, purpose="video"
            )
        job_id = str(uuid.uuid4())
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                INSERT INTO jobs (
                    job_id, category, region,
                    video_limit, image_mode,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id,
                category,
                region,
                limit,
                config.image_mode,
                "queued",
                now,
                now
            ))
            GenerationStorage.save_for_job(
                conn, job_id, config, legacy_request=legacy_request
            )
            if script_service:
                script_service.reserve(
                    conn, script_request, actor, job_id=job_id,
                    quote_id=script_quote_id, confirmed=script_cost_confirmed,
                )

        return job_id

    def get_job(self, job_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT j.*, c.config_json AS saved_generation_config, "
                "c.legacy_request FROM jobs j LEFT JOIN job_generation_configs c "
                "ON c.job_id = j.job_id WHERE j.job_id = ?",
                (job_id,)
            ).fetchone()

        return self._decode_job(row) if row else None

    @staticmethod
    def _decode_job(row):
        job = dict(row)
        saved = job.pop("saved_generation_config", None)
        legacy = job.pop("legacy_request", None)
        job["generation_config_source"] = (
            "legacy" if legacy or saved is None else "configured"
        )
        job["generation_config"] = json.loads(saved) if saved else None
        return job

    def update_job(
        self,
        job_id,
        status,
        plan_id=None,
        video_path=None,
        error=None
    ):
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            cursor = conn.execute("""
                UPDATE jobs
                SET status = ?,
                    plan_id = COALESCE(?, plan_id),
                    video_path = COALESCE(?, video_path),
                    error = ?,
                    updated_at = ?
                WHERE job_id = ?
            """, (
                status,
                plan_id,
                video_path,
                error,
                now,
                job_id
            ))

            if cursor.rowcount != 1:
                raise KeyError(
                    f"Job not found: {job_id}"
                )

    def review_job(self, job_id, decision):
        if decision not in ("approved", "rejected"):
            raise ValueError("Invalid review decision")

        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            cursor = conn.execute("""
                UPDATE jobs
                SET status = ?, updated_at = ?
                WHERE job_id = ?
                  AND status = 'pending_review'
            """, (decision, now, job_id))

            if cursor.rowcount != 1:
                raise ValueError(
                    "Job is not pending review"
                )

        return self.get_job(job_id)
    

    def list_jobs(self, status=None, limit=100):
        """
        Return recent jobs, optionally filtered by status.
        """
        if limit < 1 or limit > 1000:
            raise ValueError(
                "limit must be between 1 and 1000"
            )

        with self.connect() as conn:
            if status:
                rows = conn.execute("""
                    SELECT j.*, c.config_json AS saved_generation_config,
                           c.legacy_request
                    FROM jobs j LEFT JOIN job_generation_configs c
                    ON c.job_id = j.job_id
                    WHERE j.status = ?
                    ORDER BY j.created_at DESC
                    LIMIT ?
                """, (status, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT j.*, c.config_json AS saved_generation_config,
                           c.legacy_request
                    FROM jobs j LEFT JOIN job_generation_configs c
                    ON c.job_id = j.job_id
                    ORDER BY j.created_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()

        return [self._decode_job(row) for row in rows]

