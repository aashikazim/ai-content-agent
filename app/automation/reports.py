"""Private, atomic daily reports and read-only dashboard rows."""
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
from typing import Any


def data_root() -> Path:return Path(os.getenv('AUTOMATION_DATA_DIR','data/automation')).absolute()


def read_day(db_path: str | Path,day: date,lane: str='shorts_daily') -> dict[str,Any]:
    path=Path(db_path).absolute()
    if not path.exists():return {'plan_date':day.isoformat(),'slots':[],'run':None}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'automation_day_runs' not in tables:return {'plan_date':day.isoformat(),'slots':[],'run':None}
        run=conn.execute('SELECT * FROM automation_day_runs WHERE plan_date=? AND lane=?',(day.isoformat(),lane)).fetchone()
        rows=conn.execute('SELECT * FROM daily_plans WHERE plan_date=? AND lane=? ORDER BY slot',(day.isoformat(),lane)).fetchall()
        slots=[]
        for row in rows:
            value=dict(row);value['recipe']=json.loads(value['recipe'])
            value['attempts']=[dict(a) for a in conn.execute('''SELECT attempt_no,script_attempt_id,production_id,job_id,status,error
                FROM automation_slot_attempts WHERE daily_plan_id=? ORDER BY attempt_no''',(row['daily_plan_id'],))]
            job=conn.execute('SELECT status FROM jobs WHERE job_id=?',(row['job_id'],)).fetchone() if 'jobs' in tables and row['job_id'] else None
            value['job_status']=job[0] if job else None
            metric=conn.execute('SELECT video_seconds,neural_seconds,queue_seconds FROM automation_gpu_measurements WHERE job_id=? AND is_seed=0',(row['job_id'],)).fetchone() if row['job_id'] else None
            value['metrics']=dict(metric) if metric else None
            slots.append(value)
        run_value=dict(run) if run else None
        if run_value:
            run_value['forecast']=json.loads(run_value.pop('forecast_json'))
            for name in ('request_json','backup_topics_json','used_backups_json'):run_value.pop(name,None)
    return {'plan_date':day.isoformat(),'slots':slots,'run':run_value}


def write_report(db_path: str | Path,day: date,lane: str='shorts_daily') -> str:
    plan=read_day(db_path,day,lane)
    lines=[f'# Automation report — {day.isoformat()}', '',f'Lane: {lane}. No approval or publishing is performed by the Director.','',
        '| Slot | Topic | State | Job | Notes |','|---|---|---|---|---|']
    for row in plan['slots']:
        title=row['recipe']['topic']['title'].replace('|','/').replace('\n',' ')
        notes=row['notes'].replace('|','/').replace('\n',' ')
        lines.append(f"| {row['slot']} | {title} | {row['status']} | {row['job_id'] or '—'} | {notes} |")
    path=Path(db_path).absolute()
    if path.exists() and plan['run']:
        with sqlite3.connect(path) as conn:
            metrics=conn.execute('''SELECT m.job_id,m.video_seconds,m.neural_seconds,m.queue_seconds FROM automation_gpu_measurements m
                JOIN automation_slot_attempts a ON a.job_id=m.job_id JOIN daily_plans d ON d.daily_plan_id=a.daily_plan_id WHERE d.plan_date=? AND d.lane=? AND m.is_seed=0''',(day.isoformat(),lane)).fetchall()
        lines+=['','## Measured local runs','','| Job | Video seconds | Neural seconds | Queue seconds |','|---|---|---|---|']
        lines += [f'| {j} | {v:.3f} | {n:.2f} | {q:.2f} |' for j,v,n,q in metrics]
        lines+=['','Unmeasured jobs have no invented GPU time. Technical readiness does not certify speech, captions or continuity.']
    text='\n'.join(lines)+'\n';folder=data_root()/'reports';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=folder/(day.isoformat()+'.tmp');temporary.write_text(text);temporary.chmod(0o600)
    temporary.replace(folder/(day.isoformat()+'.md'))
    return text
