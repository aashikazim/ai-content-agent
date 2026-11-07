"""Aggregate operational report with honest measured-only timing and hold reasons."""
from datetime import datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from app.automation.reports import data_root
from app.automation.storage import AutomationStorage


def cell(value):return str(value or '—').replace('|','/').replace('\n',' ')


def write_daily_report(db_path,day,timezone_name="America/New_York"):
    from app.automation.shorts_service import backup_before_upgrade
    backup_before_upgrade(db_path)
    storage=AutomationStorage(db_path)
    zone=ZoneInfo(timezone_name)
    start=datetime.combine(day,time.min,zone).astimezone(timezone.utc).isoformat()
    end=datetime.combine(day+timedelta(days=1),time.min,zone).astimezone(timezone.utc).isoformat()
    with storage.connect() as c:
        plans=[dict(r) for r in c.execute('SELECT * FROM daily_plans WHERE plan_date=? ORDER BY lane,slot',(day.isoformat(),))]
        episodes=[dict(r) for r in c.execute('''SELECT r.ep_no,r.status,r.job_id,r.error,s.title FROM automation_episode_runs r JOIN series s ON s.series_id=r.series_id WHERE r.plan_date=?''',(day.isoformat(),))]
        actions=[dict(r) for r in c.execute('SELECT * FROM automation_governor_actions WHERE publish_day=?',(day.isoformat(),))]
        states=[dict(r) for r in c.execute('SELECT * FROM governor_state')]
        events=[dict(r) for r in c.execute("SELECT action,subject_id,detail_json FROM automation_events WHERE timestamp>=? AND timestamp<? ORDER BY event_id",(start,end))]
        metrics=[dict(r) for r in c.execute('SELECT * FROM automation_gpu_measurements WHERE is_seed=0')]
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        publications=[dict(r) for r in c.execute('SELECT * FROM publications WHERE created_at>=? AND created_at<?',(start,end))] if 'publications' in tables else []
    lines=[f'# Automation report — {day}','','Director generates only. Governor decisions are labeled dry-run/live; only verified published records establish public delivery.','',
        '| Lane / item | Status | Job | Reason |','|---|---|---|---|']
    lines += [f"| {cell(r['lane'])} / {r['slot']} | {cell(r['status'])} | {cell(r['job_id'])} | {cell(r['notes'])} |" for r in plans]
    lines += [f"| {cell(r['title'])} / Ep {r['ep_no']} | {cell(r['status'])} | {cell(r['job_id'])} | {cell(r['error'])} |" for r in episodes]
    lines+=['','## Governor','','| Job | Platform | Mode | Status | Slot |','|---|---|---|---|---|']
    lines += [f"| {cell(r['job_id'])} | {r['platform']} | {r['mode']} | {r['status']} | {r['slot_at']} |" for r in actions]
    lines+=['','## Actual platform records','','| Job | Platform | Recorded status |','|---|---|---|']
    lines += [f"| {cell(r['job_id'])} | {r['platform']} | {r['status']} |" for r in publications]
    lines+=['','Only status published proves verified delivery; uploaded/submitted/private are not public publication.']
    lines+=['','## Holds and owner attention','']+[f"- {r['scope']}: paused={bool(r['paused'])}, killed={bool(r['kill_switch'])}; {cell(r['reason'])}" for r in states]
    lines += [f"- {cell(r['action'])}: {cell(r['subject_id'])} {cell(r['detail_json'])}" for r in events if r['action'] in {'governor.held','trend.failed'}]
    jobs={r['job_id'] for r in plans+episodes}
    lines+=['','## Measured timing','','| Job | Video seconds | Neural seconds | Queue seconds |','|---|---|---|---|']
    lines += [f"| {r['job_id']} | {r['video_seconds']:.3f} | {r['neural_seconds']:.2f} | {r['queue_seconds']:.2f} |" for r in metrics if r['job_id'] in jobs]
    lines+=['','Unmeasured jobs have no invented GPU time. Dry-run decisions do not certify platform visibility or human quality.']
    folder=data_root()/'reports';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=folder/(day.isoformat()+'.'+str(uuid4())+'.tmp');text='\n'.join(lines)+'\n';temp.write_text(text);temp.chmod(0o600);temp.replace(folder/(day.isoformat()+'.md'))
    return text
