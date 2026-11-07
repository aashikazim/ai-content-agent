"""Long-running operational contracts use fake time/providers, never a real GPU."""
from datetime import datetime,timedelta,timezone
import json
from app.automation.clock import FrozenClock
from tests.automation_soak_harness import SoakHarness

class Clock:
    def __init__(self):self.instant=datetime(2026,11,8,6,tzinfo=timezone.utc)
    def now(self):return self.instant


def test_mock_whole_system_tick_reuses_queue_and_never_uploads(tmp_path):
    clock=Clock();h=SoakHarness(tmp_path/'soak',clock)
    try:
        first=h.tick();second=h.tick()
        assert first['drained']>=1 and second['drained']==0
        assert first['publication_rows']==second['publication_rows']==0
        assert not first['interrupted'] and not second['interrupted']
        assert first['forbidden_calls']==second['forbidden_calls']==0
    finally:h.close()


def test_35_days_combined_director_governor_and_series_lifecycle(tmp_path):
    clock=Clock();h=SoakHarness(tmp_path/'soak',clock);history=[]
    try:
        for day in range(35):
            clock.instant=datetime(2026,11,8,6,tzinfo=timezone.utc)+timedelta(days=day)
            result=h.tick();h.tick()
            assert not result['interrupted'] and result['forbidden_calls']==0
            # Tick actual configured slots and repeat them; no upload object is used.
            for hour,minute in [(14,0),(17,30),(20,0),(23,0),(0,0),(1,30)]:
                clock.instant=datetime(2026,11,8,tzinfo=timezone.utc)+timedelta(days=day,hours=hour,minutes=minute)
                if hour<6:clock.instant+=timedelta(days=1)
                h.gov.tick();h.gov.tick()
            if day<30:h.simulate_publish_episode(day+1)
            history.append({'day':day+1,'drained':result['drained'],'forbidden_calls':h.forbidden_calls})
        with h.gov.storage.connect() as c:
            assert c.execute("SELECT count(*) FROM series_episodes WHERE series_id=? AND status='published'",(h.series_id,)).fetchone()[0]==29
            assert c.execute("SELECT count(*) FROM series_episodes WHERE series_id=? AND status='skipped'",(h.series_id,)).fetchone()[0]==1
            assert c.execute("SELECT status FROM series WHERE series_id=?",(h.series_id,)).fetchone()[0]=='completed'
            assert c.execute('SELECT count(*) FROM automation_series_rollovers').fetchone()[0]==1
            assert c.execute("SELECT count(*) FROM series WHERE status='awaiting_cast'").fetchone()[0]==1
            assert c.execute("SELECT count(*) FROM automation_governor_actions WHERE mode='live'").fetchone()[0]==0
        assert len(h.calls)==len(set(h.calls)) and h.forbidden_calls==0
        (tmp_path/'35-day-report.json').write_text(json.dumps({'simulated_days':35,'history':history,'mock_publications':29,'actual_uploads':0,'duplicate_validation_calls':0,'rollover_awaits_cast':True},indent=2))
    finally:h.close()


def test_short_smoke_is_not_relabelled_as_real_24h(tmp_path):
    from scripts.automation_soak import run
    import pytest
    root=tmp_path/'smoke'
    result=run(root,smoke_seconds=.1,interval=.1)
    assert result['status']=='complete' and result['mode']=='smoke'
    assert result['last_result']['publication_rows']==0 and result['last_result']['forbidden_calls']==0
    with pytest.raises(ValueError,match='duration or mode'):run(root)


def test_interrupted_smoke_resumes_without_counting_downtime(tmp_path,monkeypatch):
    from scripts.automation_soak import run
    import pytest
    import scripts.automation_soak as command
    root=tmp_path/'resume'
    original=command.time.sleep
    monkeypatch.setattr(command.time,'sleep',lambda _:(_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):run(root,smoke_seconds=3,interval=.1)
    before=json.loads((root/'progress.json').read_text())
    assert before['status']=='interrupted' and before['active_elapsed_seconds']<3
    monkeypatch.setattr(command.time,'sleep',original)
    result=run(root,smoke_seconds=3,interval=.1)
    assert result['status']=='complete' and result['sessions']==2 and result['continuous'] is False
    assert result['last_result']['publication_rows']==0
