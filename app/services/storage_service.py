
import sqlite3
from pathlib import Path


class StorageService:
    def __init__(self, db_path="data/trends.db"):
        self.db_path = str(db_path)

        Path(self.db_path).parent.mkdir(
            parents=True, exist_ok=True
        )

        self.initialize()

    def connect(self):
        return sqlite3.connect(self.db_path)

    def initialize(self):
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category_id TEXT,
                    channel TEXT,
                    published_at TEXT,
                    duration_seconds INTEGER,
                    views INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    url TEXT,
                    score REAL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def save_videos(self, videos):
        with self.connect() as conn:
            for video in videos:
                conn.execute("""
                    INSERT INTO videos (
                        video_id, title, category_id,
                        channel, published_at,
                        duration_seconds, views,
                        likes, comments, url, score
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_id) DO UPDATE SET
                        title=excluded.title,
                        views=excluded.views,
                        likes=excluded.likes,
                        comments=excluded.comments,
                        score=excluded.score,
                        updated_at=CURRENT_TIMESTAMP
                """, (
                    video.video_id,
                    video.title,
                    video.category_id,
                    video.channel,
                    video.published_at.isoformat(),
                    video.duration_seconds,
                    video.views,
                    video.likes,
                    video.comments,
                    video.url,
                    video.score,
                ))

    def count_videos(self):
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM videos"
            ).fetchone()[0]
