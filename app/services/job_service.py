
import sqlite3
import uuid

from datetime import datetime, timezone
from pathlib import Path


class JobService:
    def __init__(self, db_path="data/trends.db"):
        self.db_path = str(db_path)

        Path(self.db_path).parent.mkdir(
            parents=True, exist_ok=True
        )

        self.initialize()

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
        image_mode="text"
    ):
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
                image_mode,
                "queued",
                now,
                now
            ))

        return job_id

    def get_job(self, job_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,)
            ).fetchone()

        return dict(row) if row else None

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
