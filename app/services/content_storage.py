
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ContentStorage:
    def __init__(
        self,
        db_path="data/trends.db",
        output_dir="data/plans"
    ):
        self.db_path = str(db_path)
        self.output_dir = Path(output_dir)

        Path(self.db_path).parent.mkdir(
            parents=True, exist_ok=True
        )
        self.output_dir.mkdir(
            parents=True, exist_ok=True
        )

        self.initialize()

    def initialize(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS content_plans (
                    plan_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    json_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def save_plan(self, plan):
        plan_id = str(uuid.uuid4())
        path = self.output_dir / f"{plan_id}.json"

        data = plan.model_dump(mode="json")
        path.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8"
        )

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO content_plans (
                        plan_id, topic, title,
                        category, status,
                        json_path, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    plan_id,
                    plan.topic,
                    plan.title,
                    plan.category,
                    plan.review_status,
                    str(path),
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ))
        except Exception:
            path.unlink(missing_ok=True)
            raise

        return plan_id, path

    def get_plan(self, plan_id):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT json_path
                FROM content_plans
                WHERE plan_id = ?
                """,
                (plan_id,)
            ).fetchone()

        if row is None:
            raise KeyError(
                f"Plan not found: {plan_id}"
            )

        return json.loads(
            Path(row[0]).read_text(
                encoding="utf-8"
            )
        )
