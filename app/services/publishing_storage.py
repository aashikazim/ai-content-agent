
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

            # Day 6: Migrate existing publications table
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(publications)"
                )
            }

            new_columns = {
                "attempt_started_at": "TEXT",
                "external_status": "TEXT",
                "last_checked_at": "TEXT",
            }

            for column, column_type in new_columns.items():
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE publications "
                        f"ADD COLUMN {column} {column_type}"
                    )

            # Day 6: Publication event history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS publication_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
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




    def record_event(
        self,
        publication_id,
        event_type,
        details=None
    ):
        from datetime import datetime, timezone

        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                INSERT INTO publication_events (
                    publication_id,
                    event_type,
                    details,
                    created_at
                )
                VALUES (?, ?, ?, ?)
            """, (
                publication_id,
                event_type,
                details,
                now
            ))

    def list_publications(self, statuses=None):
        with self.connect() as conn:
            if statuses:
                placeholders = ",".join(
                    "?" for _ in statuses
                )

                rows = conn.execute(
                    f"""
                    SELECT * FROM publications
                    WHERE status IN ({placeholders})
                    ORDER BY created_at DESC
                    """,
                    tuple(statuses)
                ).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM publications
                    ORDER BY created_at DESC
                """).fetchall()

        return [dict(row) for row in rows]

    def reserve_publication(
        self,
        job_id,
        platform
    ):
        """
        Atomically reserve one publication attempt.

        A duplicate reservation raises an SQLite
        integrity error. No second upload is started.
        """
        from datetime import datetime, timezone
        import uuid

        publication_id = str(uuid.uuid4())
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                INSERT INTO publications (
                    publication_id,
                    job_id,
                    platform,
                    status,
                    created_at,
                    updated_at,
                    attempt_started_at
                )
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """, (
                publication_id,
                job_id,
                platform,
                now,
                now,
                now
            ))

        return publication_id
