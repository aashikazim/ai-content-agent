import sqlite3
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.generation_config import GenerationConfig
from app.services.generation_storage import GenerationStorage
from app.services.provider_catalog import GOOGLE_MODEL


def create_legacy_jobs(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        conn.execute("INSERT INTO jobs VALUES ('legacy-job', 'approved')")


def test_fresh_database_can_initialize_and_save_planned_drafts(tmp_path):
    storage = GenerationStorage(tmp_path / "nested" / "fresh.db")
    config = GenerationConfig(video_provider="google", video_model=GOOGLE_MODEL,
                              fps=24, duration_seconds=1200, max_cost_usd="2.50")
    draft = storage.save_draft(config)
    UUID(draft["config_id"])
    assert draft["config"]["max_cost_usd"] == "2.50"
    assert storage.list_drafts() == [draft]
    assert storage.get_for_job("missing") is None


def test_existing_database_migration_is_additive_and_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    create_legacy_jobs(path)
    storage = GenerationStorage(path)
    storage.initialize()
    GenerationStorage(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM jobs").fetchall() == [("legacy-job", "approved")]
        foreign_keys = conn.execute("PRAGMA foreign_key_list(job_generation_configs)").fetchall()
        assert foreign_keys[0][2:5] == ("jobs", "job_id", "job_id")
        storage.save_for_job(conn, "legacy-job", GenerationConfig())
    assert storage.get_for_job("legacy-job")["schema_version"] == 1


def test_job_snapshot_is_immutable_and_not_affected_by_mutating_config(tmp_path):
    path = tmp_path / "legacy.db"
    create_legacy_jobs(path)
    storage = GenerationStorage(path)
    config = GenerationConfig(duration_seconds=45)
    with sqlite3.connect(path) as conn:
        storage.save_for_job(conn, "legacy-job", config)
    config.duration_seconds = 60
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            storage.save_for_job(conn, "legacy-job", config)
    assert storage.get_for_job("legacy-job")["duration_seconds"] == 45


def test_save_for_job_does_not_commit_caller_transaction(tmp_path):
    path = tmp_path / "legacy.db"
    create_legacy_jobs(path)
    storage = GenerationStorage(path)
    with pytest.raises(RuntimeError):
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO jobs VALUES ('new-job', 'queued')")
            storage.save_for_job(conn, "new-job", GenerationConfig())
            raise RuntimeError("Simulated job-creation failure")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT 1 FROM jobs WHERE job_id = 'new-job'").fetchone() is None
    assert storage.get_for_job("new-job") is None


def test_missing_job_rejected_even_on_legacy_connection_without_foreign_keys(tmp_path):
    path = tmp_path / "legacy.db"
    create_legacy_jobs(path)
    storage = GenerationStorage(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="missing job"):
            storage.save_for_job(conn, "does-not-exist", GenerationConfig())


def test_only_validated_configuration_is_saved(tmp_path):
    storage = GenerationStorage(tmp_path / "fresh.db")
    with pytest.raises(ValidationError):
        storage.save_draft({"OPENAI_API_KEY": "never-store-this"})
    with pytest.raises(ValueError, match="Unknown video model"):
        storage.save_draft(GenerationConfig(video_model="unknown"))
    # Revalidation also catches objects created without normal Pydantic validation.
    with pytest.raises(ValidationError):
        storage.save_draft(GenerationConfig.model_construct(duration_seconds=1))
    assert storage.list_drafts() == []


def test_reference_ids_and_decimal_round_trip(tmp_path):
    storage = GenerationStorage(tmp_path / "fresh.db")
    reference_id = str(uuid4())
    config = GenerationConfig(character_reference_ids=[reference_id], max_cost_usd="0.125")
    saved = storage.save_draft(config)
    restored = GenerationConfig.model_validate(storage.list_drafts()[0]["config"])
    assert restored == config
    assert saved["config"]["character_reference_ids"] == [reference_id]


@pytest.mark.parametrize("limit", [0, 101, -1, True, 1.5, "20"])
def test_list_draft_limits_are_bounded(tmp_path, limit):
    storage = GenerationStorage(tmp_path / "fresh.db")
    with pytest.raises(ValueError, match="limit"):
        storage.list_drafts(limit)


def test_drafts_are_recent_first_and_limited(tmp_path):
    storage = GenerationStorage(tmp_path / "fresh.db")
    first = storage.save_draft(GenerationConfig(duration_seconds=30))
    second = storage.save_draft(GenerationConfig(duration_seconds=45))
    assert storage.list_drafts(1) == [second]
    assert storage.list_drafts(2) == [second, first]


def test_existing_config_table_migrates_legacy_flag_without_changing_snapshot(tmp_path):
    path = tmp_path / "existing-configs.db"
    create_legacy_jobs(path)
    config_json = GenerationConfig(duration_seconds=30).model_dump_json()
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE job_generation_configs (
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                config_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO job_generation_configs VALUES (?, ?, 1, ?)",
                     ("legacy-job", config_json, "original-timestamp"))
    storage = GenerationStorage(path)
    storage.initialize()
    assert storage.get_for_job("legacy-job")["duration_seconds"] == 30
    with sqlite3.connect(path) as conn:
        assert conn.execute("""
            SELECT config_json, legacy_request, created_at FROM job_generation_configs
            WHERE job_id = 'legacy-job'
        """).fetchone() == (config_json, 0, "original-timestamp")


@pytest.mark.parametrize("legacy_request", [False, True])
def test_snapshot_records_request_source_without_changing_public_config(tmp_path, legacy_request):
    path = tmp_path / "legacy.db"
    create_legacy_jobs(path)
    storage = GenerationStorage(path)
    config = GenerationConfig()
    with sqlite3.connect(path) as conn:
        storage.save_for_job(conn, "legacy-job", config, legacy_request=legacy_request)
        assert conn.execute("SELECT legacy_request FROM job_generation_configs").fetchone()[0] == int(legacy_request)
    assert storage.get_for_job("legacy-job") == config.model_dump(mode="json")
