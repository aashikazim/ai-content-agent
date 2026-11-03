
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class AnalyticsStorage:
    def __init__(self, db_path="data/trends.db"):
        self.db_path = str(db_path)

        Path(self.db_path).parent.mkdir(
            parents=True,
            exist_ok=True
        )

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
                CREATE TABLE IF NOT EXISTS
                analytics_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    platform_video_id TEXT NOT NULL,
                    views INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    watch_time_minutes REAL,
                    average_view_duration REAL,
                    collected_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_analytics_publication_time
                ON analytics_snapshots (
                    publication_id,
                    collected_at
                )
            """)

    def save_snapshot(
        self,
        publication_id,
        platform,
        platform_video_id,
        metrics
    ):
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO analytics_snapshots (
                    publication_id,
                    platform,
                    platform_video_id,
                    views,
                    likes,
                    comments,
                    watch_time_minutes,
                    average_view_duration,
                    collected_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                publication_id,
                platform,
                platform_video_id,
                metrics.get("views"),
                metrics.get("likes"),
                metrics.get("comments"),
                metrics.get("watch_time_minutes"),
                metrics.get("average_view_duration"),
                now
            ))

            return cursor.lastrowid

    def latest_snapshots(self):
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT a.*
                FROM analytics_snapshots a
                WHERE a.snapshot_id = (
                    SELECT MAX(b.snapshot_id)
                    FROM analytics_snapshots b
                    WHERE b.publication_id =
                          a.publication_id
                )
                ORDER BY a.collected_at DESC
            """).fetchall()

        return [dict(row) for row in rows]

    def history(self, publication_id):
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT *
                FROM analytics_snapshots
                WHERE publication_id = ?
                ORDER BY collected_at
            """, (publication_id,)).fetchall()

        return [dict(row) for row in rows]
