"""Namespaced additive tables in DATABASE_PATH; no second database."""
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path


def encoded(value):
    return json.dumps(value, allow_nan=False)


class ClipStudioStorage:
    def __init__(self, db_path):
        self.db_path = str(Path(db_path).absolute())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS clip_studio_projects (
                    project_id TEXT PRIMARY KEY, actor TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_json TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    UNIQUE(actor,request_id));
                CREATE TABLE IF NOT EXISTS clip_studio_sources (
                    source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, position INTEGER NOT NULL,
                    kind TEXT NOT NULL, path TEXT, sha256 TEXT, metadata_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS clip_studio_analyses (
                    project_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS clip_studio_plans (
                    plan_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version INTEGER NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, result_json TEXT NOT NULL,
                    external_id TEXT, usage_json TEXT, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS clip_studio_clips (
                    clip_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, position INTEGER NOT NULL, track_id TEXT NOT NULL,
                    timeline_start REAL NOT NULL, plan_json TEXT NOT NULL, selected_attempt_id TEXT,
                    UNIQUE(project_id,position));
                CREATE TABLE IF NOT EXISTS clip_studio_render_attempts (
                    attempt_id TEXT PRIMARY KEY, clip_id TEXT NOT NULL, version INTEGER NOT NULL,
                    seed INTEGER NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    output_path TEXT, thumbnail_path TEXT, quality_json TEXT, error TEXT,
                    external_id TEXT, workflow_sha256 TEXT, created_at INTEGER NOT NULL,
                    UNIQUE(clip_id,version));
                CREATE TABLE IF NOT EXISTS clip_studio_jobs (
                    job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    kind TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                    progress REAL NOT NULL, request_json TEXT NOT NULL, error TEXT,
                    lease_token TEXT, heartbeat INTEGER, created_at INTEGER NOT NULL,
                    UNIQUE(project_id,request_id));
                CREATE TABLE IF NOT EXISTS clip_studio_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    stage TEXT NOT NULL, message TEXT NOT NULL, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS clip_studio_chunks (
                    attempt_id TEXT NOT NULL, position INTEGER NOT NULL, request_json TEXT NOT NULL,
                    status TEXT NOT NULL, external_id TEXT, output_path TEXT, quality_json TEXT,
                    PRIMARY KEY(attempt_id,position));
                CREATE TABLE IF NOT EXISTS clip_studio_review_links (
                    attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS clip_studio_actions (
                    project_id TEXT NOT NULL, request_id TEXT NOT NULL, action_json TEXT NOT NULL,
                    response_json TEXT NOT NULL, created_at INTEGER NOT NULL,
                    PRIMARY KEY(project_id,request_id));
                CREATE TABLE IF NOT EXISTS clip_studio_plan_responses (
                    call_id TEXT PRIMARY KEY, response_json TEXT NOT NULL,
                    validation_json TEXT);
                CREATE TABLE IF NOT EXISTS clip_studio_calls (
                    call_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, provider TEXT NOT NULL,
                    status TEXT NOT NULL, external_id TEXT, usage_json TEXT, created_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS clip_studio_jobs_queue ON clip_studio_jobs(status,created_at);
            ''')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def event(conn, job_id, stage, message):
        conn.execute('INSERT INTO clip_studio_events(job_id,stage,message,created_at) VALUES (?,?,?,?)',
                     (job_id, stage, message, int(time.time())))

    @staticmethod
    def job(row):
        value = dict(row)
        value.pop('request_json', None)
        value.pop('lease_token', None)
        return value
