"""Additive SQLite storage for configuration drafts and immutable job settings."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.models.generation_config import GenerationConfig
from app.services.provider_catalog import validate_for_draft


class GenerationStorage:
    def __init__(self, db_path="data/trends.db"):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self):
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_generation_configs (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                    config_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    legacy_request INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_generation_configs)")}
            if "legacy_request" not in columns:
                conn.execute("""
                    ALTER TABLE job_generation_configs
                    ADD COLUMN legacy_request INTEGER NOT NULL DEFAULT 0
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS generation_config_drafts (
                    config_id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    @staticmethod
    def _validated(config):
        config = GenerationConfig.model_validate(config)
        validate_for_draft(config)
        return config

    @staticmethod
    def save_for_job(conn, job_id, config, *, legacy_request=False):
        """Insert once inside the caller's job-creation transaction; never commit."""
        config = GenerationStorage._validated(config)
        if not isinstance(legacy_request, bool):
            raise ValueError("legacy_request must be a boolean")
        # Legacy callers may not enable foreign keys on their connection.
        if conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone() is None:
            raise sqlite3.IntegrityError("Cannot save configuration for a missing job")
        conn.execute("""
            INSERT INTO job_generation_configs
                (job_id, config_json, schema_version, legacy_request, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (job_id, config.model_dump_json(), config.schema_version,
              int(legacy_request), datetime.now(timezone.utc).isoformat()))

    def get_for_job(self, job_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT config_json FROM job_generation_configs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return json.loads(row["config_json"]) if row else None

    def save_draft(self, config):
        config = self._validated(config)
        config_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO generation_config_drafts
                    (config_id, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
            """, (config_id, config.model_dump_json(), now, now))
        return {"config_id": config_id, "config": config.model_dump(mode="json"),
                "created_at": now, "updated_at": now}

    def list_drafts(self, limit=20):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT config_id, config_json, created_at, updated_at
                FROM generation_config_drafts
                ORDER BY created_at DESC, config_id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [{"config_id": row["config_id"], "config": json.loads(row["config_json"]),
                 "created_at": row["created_at"], "updated_at": row["updated_at"]}
                for row in rows]
