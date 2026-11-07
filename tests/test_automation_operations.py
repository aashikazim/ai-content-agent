from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock
import pytest
from app.automation.clock import FrozenClock
from app.automation.operations import snapshot
from app.automation.storage import AutomationStorage
from tests.test_generation_api import generation_api,sign_in
from tests.automation_soak_harness import SoakHarness

ROOT=Path(__file__).parents[1]


def test_operations_missing_database_is_read_only(tmp_path):
    path=tmp_path/'missing.db'
    assert snapshot(path)['initialized'] is False
    assert not path.exists()


def test_operations_does_not_migrate_existing_database(tmp_path):
    path=tmp_path/'existing.db'
    with sqlite3.connect(path) as c:c.execute('CREATE TABLE legacy(value TEXT)');c.execute("INSERT INTO legacy VALUES ('keep')")
    before=path.read_bytes();assert snapshot(path)['initialized'] is False
    assert path.read_bytes()==before


def test_operations_distinguishes_seed_and_actual_measurements_and_stale_work(tmp_path):
    from app.services.work_queue import WorkQueue
    clock=FrozenClock(datetime(2026,11,8,6,tzinfo=timezone.utc));path=tmp_path/'existing.db';s=AutomationStorage(path,clock=clock);q=WorkQueue(path)
    work=q.enqueue('automation','fixture')
    with s.connect() as c:
        c.execute("UPDATE app_work_queue SET status='running',heartbeat=1 WHERE work_id=?",(work['work_id'],))
        c.execute("INSERT INTO automation_gpu_measurements VALUES ('seed',NULL,'ltx-2.5','text',1,29,0,NULL,1,?,?)",(s.timestamp(),s.timestamp()))
    result=snapshot(path,clock=clock)
    assert result['attention'][0]['status']=='running'
    assert result['measurements'][0]['is_seed']==1 and result['measurements'][0]['seconds_per_video_second']==29
    assert result['workers']==[]


def test_operations_route_admin_only_and_read_only(generation_api,monkeypatch):
    api=generation_api
    assert api.client.get('/api/v1/automation/operations').status_code==401
    sign_in(api)
    assert api.client.get('/api/v1/automation/operations').status_code==200
    with sqlite3.connect(api.db_path) as c:assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='automation_events'").fetchone()


def test_soak_refuses_unmarked_existing_directory(tmp_path):
    root=tmp_path/'user-data';root.mkdir();(root/'keep.txt').write_text('keep')
    with pytest.raises(ValueError,match='unmarked'):SoakHarness(root,FrozenClock(datetime(2026,11,8,tzinfo=timezone.utc)))
    assert (root/'keep.txt').read_text()=='keep'
