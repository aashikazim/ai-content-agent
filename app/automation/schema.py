"""Additive automation tables only; no changes to legacy tables or statuses."""

# Each statement is executed in one explicit transaction (not executescript,
# which can implicitly commit). Do not reserve an existing PRAGMA user_version.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS trend_signals (
        signal_id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
        title TEXT NOT NULL, category_hint TEXT, score REAL NOT NULL DEFAULT 0,
        velocity REAL NOT NULL DEFAULT 0, region TEXT NOT NULL, url TEXT,
        collected_at TEXT NOT NULL, raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(source, external_id, collected_at))""",
    """CREATE TABLE IF NOT EXISTS topic_candidates (
        topic_id TEXT PRIMARY KEY, normalized_topic TEXT NOT NULL, category TEXT NOT NULL,
        rank_score REAL NOT NULL DEFAULT 0,
        signal_ids_json TEXT NOT NULL CHECK(json_valid(signal_ids_json)),
        status TEXT NOT NULL DEFAULT 'new'
            CHECK(status IN ('new','selected','used','rejected','cooldown')),
        last_used_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS daily_plans (
        daily_plan_id TEXT PRIMARY KEY, plan_date TEXT NOT NULL, lane TEXT NOT NULL,
        slot INTEGER NOT NULL CHECK(slot >= 1),
        topic_id TEXT REFERENCES topic_candidates(topic_id), job_id TEXT,
        recipe TEXT NOT NULL CHECK(json_valid(recipe)), status TEXT NOT NULL DEFAULT 'planned'
            CHECK(status IN ('planned','queued','rendering','in_review','approved',
                'scheduled','published','skipped','failed','needs_reconciliation')),
        planned_publish_at TEXT, notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(plan_date, lane, slot))""",
    """CREATE TABLE IF NOT EXISTS series (
        series_id TEXT PRIMARY KEY, lane TEXT NOT NULL, title TEXT NOT NULL,
        genre TEXT NOT NULL, premise TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planning'
            CHECK(status IN ('planning','awaiting_cast','active','completed')),
        start_date TEXT, total_episodes INTEGER NOT NULL DEFAULT 30 CHECK(total_episodes = 30),
        cast_package_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(cast_package_ids_json)),
        bible_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(bible_json)), bible_sha256 TEXT,
        story_state_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(story_state_json)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS series_episodes (
        episode_id TEXT PRIMARY KEY, series_id TEXT NOT NULL REFERENCES series(series_id),
        ep_no INTEGER NOT NULL CHECK(ep_no BETWEEN 1 AND 30),
        outline_json TEXT NOT NULL CHECK(json_valid(outline_json)), script_plan_id TEXT, job_id TEXT,
        status TEXT NOT NULL DEFAULT 'outlined'
            CHECK(status IN ('outlined','scripted','rendering','rendered','in_review',
                'approved','scheduled','published','skipped','failed','needs_reconciliation')),
        planned_publish_at TEXT, actual_publish_at TEXT, summary_text TEXT NOT NULL DEFAULT '',
        state_after_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(state_after_json)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(series_id, ep_no))""",
    """CREATE TABLE IF NOT EXISTS lane_settings (
        lane TEXT PRIMARY KEY, enabled INTEGER CHECK(enabled IN (0,1)),
        approval_mode TEXT CHECK(approval_mode IN ('manual','auto_with_gates')),
        updated_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS automation_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL
            CHECK(actor IN ('director','governor','user')),
        action TEXT NOT NULL, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
        detail_json TEXT NOT NULL CHECK(json_valid(detail_json)), timestamp TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS standing_authorizations (
        authorization_id TEXT PRIMARY KEY,
        providers_json TEXT NOT NULL CHECK(json_valid(providers_json)),
        max_daily_usd TEXT NOT NULL, max_total_usd TEXT NOT NULL, expires_at TEXT NOT NULL,
        created_by TEXT NOT NULL, consumed_total_usd TEXT NOT NULL DEFAULT '0',
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked','expired')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS governor_state (
        scope TEXT PRIMARY KEY CHECK(scope IN ('global','youtube','facebook')),
        kill_switch INTEGER NOT NULL DEFAULT 0 CHECK(kill_switch IN (0,1)),
        paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
        consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
        reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    # Separate from the existing queue: intent/ack tracking, never a retry queue.
    """CREATE TABLE IF NOT EXISTS automation_idempotency (
        scope TEXT NOT NULL, request_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
        claim_token TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'reserved'
            CHECK(status IN ('reserved','completed','needs_reconciliation')),
        result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(scope, request_key))""",
    "CREATE INDEX IF NOT EXISTS automation_daily_status ON daily_plans(lane, plan_date, status)",
    "CREATE INDEX IF NOT EXISTS automation_episode_status ON series_episodes(series_id, status, ep_no)",
    "CREATE INDEX IF NOT EXISTS automation_signal_time ON trend_signals(region, collected_at)",
    "CREATE INDEX IF NOT EXISTS automation_events_subject ON automation_events(subject_type, subject_id, event_id)",
    """CREATE TRIGGER IF NOT EXISTS automation_events_no_update
        BEFORE UPDATE ON automation_events BEGIN
        SELECT RAISE(ABORT, 'Automation events are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS automation_events_no_delete
        BEFORE DELETE ON automation_events BEGIN
        SELECT RAISE(ABORT, 'Automation events are append-only'); END""",
)

AUTOMATION_TABLES: frozenset[str] = frozenset({
    "trend_signals", "topic_candidates", "daily_plans", "series", "series_episodes",
    "lane_settings", "automation_events", "standing_authorizations", "governor_state",
    "automation_idempotency",
})


# Phase 3: durable day intent, immutable slot attempts, measured capacity.
SCHEMA_STATEMENTS += (
    """CREATE TABLE IF NOT EXISTS automation_day_runs (
        run_id TEXT PRIMARY KEY, plan_date TEXT NOT NULL, lane TEXT NOT NULL,
        request_json TEXT NOT NULL, backup_topics_json TEXT NOT NULL, used_backups_json TEXT NOT NULL DEFAULT '[]',
        forecast_json TEXT NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(plan_date,lane))""",
    """CREATE TABLE IF NOT EXISTS automation_slot_attempts (
        daily_plan_id TEXT NOT NULL REFERENCES daily_plans(daily_plan_id), attempt_no INTEGER NOT NULL CHECK(attempt_no IN (0,1)),
        topic_json TEXT NOT NULL, script_attempt_id TEXT, production_id TEXT, job_id TEXT,
        status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(daily_plan_id,attempt_no))""",
    """CREATE TABLE IF NOT EXISTS automation_gpu_measurements (
        measurement_id TEXT PRIMARY KEY, job_id TEXT UNIQUE, model TEXT NOT NULL, mode TEXT NOT NULL,
        video_seconds REAL NOT NULL CHECK(video_seconds>0), neural_seconds REAL NOT NULL CHECK(neural_seconds>0),
        queue_seconds REAL NOT NULL DEFAULT 0 CHECK(queue_seconds>=0), wall_seconds REAL,
        is_seed INTEGER NOT NULL DEFAULT 0 CHECK(is_seed IN (0,1)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
)
AUTOMATION_TABLES |= frozenset({'automation_day_runs','automation_slot_attempts','automation_gpu_measurements'})

SCHEMA_STATEMENTS += (
    """CREATE TABLE IF NOT EXISTS automation_short_receipts (
        daily_plan_id TEXT NOT NULL, attempt_no INTEGER NOT NULL, receipt_no INTEGER NOT NULL,
        body_json TEXT NOT NULL, errors_json TEXT NOT NULL, created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        PRIMARY KEY(daily_plan_id,attempt_no,receipt_no))""",
)
AUTOMATION_TABLES |= frozenset({'automation_short_receipts'})

# Phase 4: one durable planning intent and received local-model responses.
SCHEMA_STATEMENTS += (
    """CREATE TABLE IF NOT EXISTS automation_series_requests (
        series_id TEXT PRIMARY KEY REFERENCES series(series_id), request_id TEXT NOT NULL UNIQUE,
        request_json TEXT NOT NULL CHECK(json_valid(request_json)), actor TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'premises',
        error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS automation_series_receipts (
        series_id TEXT NOT NULL REFERENCES series(series_id), stage TEXT NOT NULL,
        attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 3), status TEXT NOT NULL,
        body_json TEXT CHECK(body_json IS NULL OR json_valid(body_json)),
        errors_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(errors_json)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(series_id,stage,attempt_no))""",
)
AUTOMATION_TABLES |= frozenset({'automation_series_requests','automation_series_receipts'})
