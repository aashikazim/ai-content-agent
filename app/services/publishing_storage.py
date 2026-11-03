
import sqlite3
import uuid
from datetime import datetime, timezone


class PublishingStorage:
    def __init__(self, db_path="data/trends.db"):
        self.db_path = str(db_path)
        self.initialize()

    def connect(self):
        conn = sqlite3.connect(
            self.db_path,
            timeout=30
        )
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self):
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS publications (
                    publication_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    platform_video_id TEXT,
                    url TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, platform)
                )
            """)

    def get_by_job(self, job_id, platform):
        with self.connect() as conn:
            row = conn.execute("""
                SELECT * FROM publications
                WHERE job_id = ? AND platform = ?
            """, (job_id, platform)).fetchone()

        return dict(row) if row else None

    def create(self, job_id, platform):
        publication_id = str(uuid.uuid4())
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                INSERT INTO publications (
                    publication_id, job_id, platform,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'pending', ?, ?)
            """, (
                publication_id,
                job_id,
                platform,
                now,
                now
            ))

        return publication_id

    def update(
        self,
        publication_id,
        status,
        platform_video_id=None,
        url=None,
        error=None
    ):
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                UPDATE publications
                SET status = ?,
                    platform_video_id = COALESCE(
                        ?, platform_video_id
                    ),
                    url = COALESCE(?, url),
                    error = ?,
                    updated_at = ?
                WHERE publication_id = ?
            """, (
                status,
                platform_video_id,
                url,
                error,
                now,
                publication_id
            ))

    def get(self, publication_id):
        with self.connect() as conn:
            row = conn.execute("""
                SELECT * FROM publications
                WHERE publication_id = ?
            """, (publication_id,)).fetchone()

        return dict(row) if row else None
