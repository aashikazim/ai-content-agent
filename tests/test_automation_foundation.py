"""Phase 1 foundations: temporary DBs and fakes only; no inference or network."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
import yaml

from app.automation.clock import FrozenClock, SystemClock, local_day, utc_now
from app.automation.config import AutomationConfig, load_config, load_settings
from app.automation.director import Director
from app.automation.events import EventLog
from app.automation.idempotency import canonical_json, daily_slot_id, episode_id, fingerprint, stable_id
from app.automation.lanes import Lane, PlannedItem
from app.automation.schema import AUTOMATION_TABLES
from app.automation.storage import AutomationStorage, IdempotencyConflict

ROOT = Path(__file__).parents[1]
LANES_FILE = ROOT / "config/lanes.yaml"
NOW = datetime(2026, 11, 6, 2, 5, tzinfo=timezone.utc)


@pytest.fixture
def raw_config():
    return yaml.safe_load(LANES_FILE.read_text())


@pytest.fixture
def storage(tmp_path):
    return AutomationStorage(tmp_path / "existing.db", clock=FrozenClock(NOW))


def test_shipped_config_safe_defaults_and_owner_runtime_override():
    settings = load_settings(LANES_FILE, environ={})
    assert not settings.enabled and not settings.governor_enabled and settings.governor_dry_run
    assert settings.daily_budget_usd == 0
    shorts, story, reserved = settings.config.lanes.values()
    assert shorts.daily_count == sum(shorts.quotas.values()) == 5
    assert all(recipe.provider == "local_cinematic" and recipe.script_provider == "ollama"
               for recipe in shorts.recipes.values())
    assert all(lane.approval_mode == "manual" for lane in settings.config.lanes.values())
    assert story.episode_duration_s.min == 300 and story.episode_duration_s.max == 600
    assert story.series_start_mode == "manual_cast_approval" and story.platforms == ["youtube"]
    assert not reserved.enabled


@pytest.mark.parametrize("path,value", [
    (("timezone",), "Definitely/Not_A_Timezone"),
    (("gpu", "nightly_window", "start"), "24:30"),
    (("gpu", "nightly_window", "end"), "00:30"),
    (("gpu", "seed_seconds_per_video_second", "ltx_i2v"), float("nan")),
    (("lanes", "shorts_daily", "daily_count"), 6),
    (("lanes", "shorts_daily", "enabled"), "false"),
    (("lanes", "shorts_daily", "approval_mode"), "auto"),
    (("lanes", "shorts_daily", "platforms"), ["youtube", "youtube"]),
    (("lanes", "shorts_daily", "publish_slots"), ["09:00"] * 5),
    (("lanes", "shorts_daily", "quotas", "comedy"), -1),
    (("lanes", "shorts_daily", "recipes", "comedy", "duration_s"), 29),
    (("lanes", "shorts_daily", "recipes", "comedy", "duration_s"), True),
    (("lanes", "shorts_daily", "recipes", "comedy", "model"), "unsupported"),
    (("lanes", "shorts_daily", "recipes", "comedy", "script_provider"), "openai"),
    (("lanes", "shorts_daily", "recipes", "comedy", "provider"), "runway"),
    (("lanes", "shorts_daily", "recipes", "comedy", "model"), "ltx-wan-hybrid"),
    (("lanes", "shorts_daily", "recipes", "politics", "source_bundle"), None),
    (("lanes", "story_series", "episode_duration_s", "min"), 601),
    (("lanes", "story_series", "episode_duration_s"), {"min": 500, "max": 400}),
    (("lanes", "story_series", "platforms"), ["facebook"]),
    (("lanes", "story_series", "planner", "provider"), "anthropic"),
    (("lanes", "story_series", "episodes_per_series"), 31),
    (("lanes", "story_series", "episode_render", "model"), "wan2.2-animate-2"),
    (("governor", "wer_max"), 1.1),
    (("governor", "hold_rules"), [{"kind": "regex", "expression": "["}]),
    (("governor", "daily_publish_cap", "youtube"), 0),
    (("unknown_option",), True),
])
def test_invalid_configuration_fails_loudly(raw_config, path, value):
    node = raw_config
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        AutomationConfig.model_validate(raw_config)


@pytest.mark.parametrize("content", [
    "timezone: UTC\ntimezone: America/New_York\n",
    "a: &shared [1]\nb: *shared\n",
    "!!python/object/apply:os.system ['false']",
    "[", "null", "- a", "x" * (128 * 1024 + 1),
])
def test_yaml_invalid_duplicate_unsafe_or_oversize(tmp_path, content):
    path = tmp_path / "lanes.yaml"
    path.write_text(content)
    with pytest.raises((ValueError, ValidationError)):
        load_config(path)


def test_missing_config_does_not_fallback(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "missing.yaml", environ={})


def test_explicit_env_and_timezone_override_no_dotenv_reads(monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")
    settings = load_settings(environ={"AUTOMATION_LANES_FILE": str(LANES_FILE),
        "AUTOMATION_TIMEZONE": "UTC", "AUTOMATION_DAILY_BUDGET_USD": "2.50"})
    assert settings.config.timezone == "UTC" and not settings.enabled
    assert str(settings.daily_budget_usd) == "2.50"
    with pytest.raises(ValueError):
        load_settings(LANES_FILE, environ={"AUTOMATION_ENABLED": "yes"})
    for key, value in {"AUTOMATION_PLAN_HOUR": "24", "AUTOMATION_PLAN_MINUTE": "60",
                       "AUTOMATION_TREND_INTERVAL_HOURS": "0", "AUTOMATION_DAILY_BUDGET_USD": "-1"}.items():
        with pytest.raises(ValueError):
            load_settings(LANES_FILE, environ={key: value})
    for value in ("not-money", "NaN", "Infinity"):
        with pytest.raises(ValidationError):
            load_settings(LANES_FILE, environ={"AUTOMATION_DAILY_BUDGET_USD": value})


def test_clock_midnight_dst_and_naive_rejection():
    assert local_day(FrozenClock(NOW), "America/New_York") == date(2026, 11, 5)
    assert local_day(FrozenClock(NOW), "UTC") == date(2026, 11, 6)
    # Fall-back repeats an hour but never repeats a Director plan date.
    early = FrozenClock(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
    late = FrozenClock(early.now() + timedelta(hours=1))
    assert local_day(early, "America/New_York") == local_day(late, "America/New_York")
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 11, 1))
    assert utc_now(SystemClock()).utcoffset() == timedelta(0)


def test_canonical_request_ids_are_unambiguous_and_not_numeric_coercions():
    assert fingerprint({"a": 1, "b": [2]}) == fingerprint({"b": [2], "a": 1})
    assert fingerprint({"a": "1"}) != fingerprint({"a": 1})
    assert stable_id("s", "a:b", "c") != stable_id("s", "a", "b:c")
    assert episode_id("s", 1) != episode_id("s", 2)
    with pytest.raises(ValueError):
        episode_id("s", 31)
    with pytest.raises(ValueError):
        daily_slot_id(date.today(), "shorts", True)
    with pytest.raises(ValueError):
        canonical_json({1: "coercion"})
    with pytest.raises(ValueError):
        fingerprint({"bad": float("nan")})


def test_additive_atomic_migration_preserves_legacy_state_and_user_version(tmp_path):
    path = tmp_path / "copy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=7")
        conn.execute("CREATE TABLE jobs(job_id TEXT PRIMARY KEY,status TEXT NOT NULL)")
        conn.execute("INSERT INTO jobs VALUES ('old-job','approved')")
        conn.execute("CREATE TABLE publications(job_id TEXT,platform TEXT,status TEXT, UNIQUE(job_id,platform))")
        conn.execute("INSERT INTO publications VALUES ('old-job','facebook','needs_reconciliation')")
    storage = AutomationStorage(path)
    storage.initialize()
    AutomationStorage(path)
    with storage.connect() as conn:
        assert tuple(conn.execute("SELECT * FROM jobs").fetchone()) == ("old-job", "approved")
        assert tuple(conn.execute("SELECT * FROM publications").fetchone()) == ("old-job", "facebook", "needs_reconciliation")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert AUTOMATION_TABLES <= {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in AUTOMATION_TABLES:
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            assert {"created_at", "updated_at"} <= columns


def test_failed_migration_rolls_back_every_new_table(tmp_path, monkeypatch):
    import app.automation.storage as module
    path = tmp_path / "copy.db"
    monkeypatch.setattr(module, "SCHEMA_STATEMENTS", (
        "CREATE TABLE must_rollback(id TEXT)", "not valid SQL",
    ))
    with pytest.raises(sqlite3.OperationalError):
        AutomationStorage(path)
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchall()


def test_atomic_daily_slot_and_conflicting_recipe(storage):
    def reserve(_):
        return storage.reserve_slot(date(2026, 11, 6), "shorts_daily", 1, {"model": "ltx-2.5"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = list(pool.map(reserve, range(8)))
    assert sum(created for _, created in attempts) == 1
    assert len({row["daily_plan_id"] for row, _ in attempts}) == 1
    assert all(row["job_id"] is None for row, _ in attempts)
    with pytest.raises(IdempotencyConflict):
        storage.reserve_slot(date(2026, 11, 6), "shorts_daily", 1, {"model": "different"})


def test_episode_and_signal_uniqueness(storage):
    now = storage.timestamp()
    with storage.connect() as conn:
        conn.execute("""INSERT INTO series(series_id,lane,title,genre,premise,created_at,updated_at)
            VALUES ('s','story_series','A title','drama','A premise',?,?)""", (now, now))
        values = ("ep-1", "s", 1, "{}", now, now)
        conn.execute("""INSERT INTO series_episodes(episode_id,series_id,ep_no,outline_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?)""", values)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""INSERT INTO series_episodes(episode_id,series_id,ep_no,outline_json,created_at,updated_at)
                VALUES ('ep-2','s',1,'{}',?,?)""", (now, now))
        conn.execute("""INSERT INTO trend_signals(signal_id,source,external_id,title,region,collected_at,raw_json,created_at,updated_at)
            VALUES ('sig-1','youtube','ext','Signal','US',?,'{}',?,?)""", (now, now, now))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""INSERT INTO trend_signals(signal_id,source,external_id,title,region,collected_at,raw_json,created_at,updated_at)
                VALUES ('sig-2','youtube','ext','Signal','US',?,'{}',?,?)""", (now, now, now))


def test_claim_is_single_winner_and_duplicate_has_no_owner_token(storage):
    def claim(_):
        return storage.claim("render", "slot-1", {"model": "local"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(8)))
    assert sum(created for _, created in claims) == 1
    owner = next(row for row, created in claims if created)
    assert all("claim_token" not in row for row, created in claims if not created)
    storage.complete_claim(owner["claim_token"], {"job_id": "job-1"})
    storage.complete_claim(owner["claim_token"], {"job_id": "job-1"})
    row, created = claim(0)
    assert not created and json.loads(row["result_json"]) == {"job_id": "job-1"}
    with pytest.raises(IdempotencyConflict):
        storage.complete_claim(owner["claim_token"], {"job_id": "another-job"})
    with pytest.raises(IdempotencyConflict):
        storage.claim("render", "slot-1", {"model": "other"})


def test_uncertain_or_crashed_claim_is_never_reclaimed(storage):
    row, created = storage.claim("generation", "episode-1", {})
    assert created
    storage.mark_uncertain(row["claim_token"])
    reopened = AutomationStorage(storage.db_path, clock=FrozenClock(NOW + timedelta(days=35)))
    same, created = reopened.claim("generation", "episode-1", {})
    assert not created and same["status"] == "needs_reconciliation"
    with pytest.raises(IdempotencyConflict):
        reopened.complete_claim(row["claim_token"], {"assumed_success": True})
    reserved, _ = reopened.claim("generation", "episode-2", {})
    assert reopened.claim("generation", "episode-2", {})[1] is False
    assert reserved["status"] == "reserved"  # No heartbeat-based retry.


def test_event_log_ordering_clock_pagination_and_append_only(storage):
    events = EventLog(storage)
    first = events.append("director", "plan.reserved", "daily_plan", "slot-1", {"slots": 5})
    second = events.append("user", "phase.checked", "phase", "1")
    items = events.list()
    assert [item.event_id for item in items] == [first, second]
    assert items[0].detail == {"slots": 5} and items[0].timestamp == storage.timestamp()
    assert events.list(after_id=first, limit=1) == [items[1]]
    for query in ("DELETE FROM automation_events", "UPDATE automation_events SET action='changed'"):
        with storage.connect() as conn:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(query)
    with pytest.raises(ValueError):
        events.append("user", "unsafe action", "phase", "1")
    with pytest.raises(ValueError):
        events.append("user", "oversized", "phase", "1", {"text": "x" * 17000})


def test_director_has_no_dispatch_and_disabled_lanes_never_call_implementations():
    settings = load_settings(LANES_FILE, environ={})
    lane = Mock(spec=Lane)
    director = Director(settings, clock=FrozenClock(NOW), lanes={"topic_shorts": lane})
    preview = director.plan_day()
    assert preview.plan_date == date(2026, 11, 5)
    assert all(item.status == "automation_disabled" for item in preview.lanes)
    lane.plan_day.assert_not_called()
    with pytest.raises(NotImplementedError, match="does not dispatch"):
        director.run_day()
    lane.produce.assert_not_called()
    lane.publishable.assert_not_called()


def test_director_enabled_unimplemented_and_extension_registry():
    settings = load_settings(LANES_FILE, environ={"AUTOMATION_ENABLED": "true"})
    director = Director(settings, clock=FrozenClock(NOW))
    assert [lane.status for lane in director.plan_day().lanes] == ["not_implemented", "not_implemented", "lane_disabled"]
    custom = Mock(spec=Lane)
    custom.describe.return_value = "Future owner lane"
    custom.plan_day.return_value = (PlannedItem(1, "A future plan"),)
    settings.config.lanes["lane_3"].enabled = True
    extended = Director(settings, clock=FrozenClock(NOW), lanes={"custom": custom})
    preview = extended.plan_day(date(2026, 11, 7))
    assert preview.lanes[2].status == "preview"
    assert preview.lanes[2].items[0].title == "A future plan"
    assert custom.plan_day.call_args.args[0].lane_id == "lane_3"
    custom.produce.assert_not_called()
    custom.publishable.assert_not_called()


def test_migration_copy_checker_does_not_modify_source(tmp_path):
    spec = importlib.util.spec_from_file_location("migration_check", ROOT / "scripts/check_automation_migration.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE videos(id TEXT PRIMARY KEY,title TEXT)")
        conn.execute("INSERT INTO videos VALUES ('v','Preserve me')")
    before = source.read_bytes()
    report = module.verify_copy(source, tmp_path / "validation")
    assert source.read_bytes() == before
    assert report["existing_schema_and_rows_preserved"]
    assert report["existing_rows_checked"] == 1 and report["initializations"] == 3
    assert Path(report["backup_path"]).stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        module.verify_copy(source, tmp_path / "validation")


def test_offline_inspection_entrypoint_checks_config_without_opening_db(tmp_path, monkeypatch, caplog):
    from app.automation.__main__ import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOMATION_ENABLED", "false")
    monkeypatch.setenv("AUTOMATION_LANES_FILE", str(LANES_FILE))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "must-not-exist.db"))
    with caplog.at_level("INFO"):
        assert main(["--describe"]) == 0
    assert "automation_disabled" in caplog.text
    assert not (tmp_path / "must-not-exist.db").exists()
    assert list(tmp_path.iterdir()) == []


def test_offline_inspection_errors_are_nonzero_without_echoing_inputs(tmp_path, caplog):
    from app.automation.__main__ import main
    bad = tmp_path / "bad.yaml"
    bad.write_text("bad: do-not-echo-this-config-value\n")
    with caplog.at_level("ERROR"):
        assert main(["--config", str(bad)]) == 2
    assert "configuration invalid" in caplog.text and "do-not-echo" not in caplog.text


def test_package_import_has_no_files_or_service_side_effects(tmp_path):
    code = "import sys;sys.path.insert(0,sys.argv[1]);import app.automation;import app.automation.director;import app.automation.storage"
    result = subprocess.run([sys.executable, "-c", code, str(ROOT)], cwd=tmp_path,
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
