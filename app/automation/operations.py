"""Read-only operations snapshots; never initialize schema, providers or workers."""
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sqlite3
from app.automation.clock import SystemClock,utc_now


def snapshot(db_path,*,clock=None):
    now=utc_now(clock or SystemClock());path=Path(db_path).absolute()
    result={'at':now.isoformat(),'initialized':False,'queue':[],'workers':[],'measurements':[],'attention':[],'events':[],'series':[]}
    if not path.exists():return result
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row;names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result['initialized']='automation_events' in names
        if 'app_work_queue' in names:
            result['queue']=[dict(r) for r in c.execute('SELECT kind,status,count(*) AS count FROM app_work_queue GROUP BY kind,status ORDER BY kind,status')]
            result['attention']+=[dict(r) for r in c.execute("SELECT kind,target_id,status,error,heartbeat FROM app_work_queue WHERE status='interrupted' OR (status='running' AND heartbeat<?) ORDER BY updated_at DESC LIMIT 30",(int(now.timestamp())-120,))]
        if 'app_worker_heartbeats' in names:
            result['workers']=[{'worker_id':r['worker_id'],'age_seconds':max(0,int(now.timestamp())-r['heartbeat']),'fresh':r['heartbeat']>=now.timestamp()-120} for r in c.execute('SELECT * FROM app_worker_heartbeats ORDER BY heartbeat DESC LIMIT 20')]
        if 'automation_gpu_measurements' in names:
            result['measurements']=[dict(r) for r in c.execute('''SELECT model,mode,is_seed,count(*) AS samples,sum(video_seconds) AS video_seconds,
                sum(neural_seconds) AS neural_seconds,sum(queue_seconds) AS queue_seconds,
                sum(neural_seconds)/sum(video_seconds) AS seconds_per_video_second FROM automation_gpu_measurements GROUP BY model,mode,is_seed ORDER BY is_seed,model,mode''')]
        if 'automation_events' in names:
            result['events']=[dict(r) for r in c.execute('SELECT event_id,actor,action,subject_id,timestamp,detail_json FROM automation_events ORDER BY event_id DESC LIMIT 30')]
        if 'series' in names:
            result['series']=[dict(r) for r in c.execute('SELECT series_id,title,status,start_date FROM series ORDER BY created_at DESC LIMIT 20')]
        for table,identifier in [('automation_day_runs','run_id'),('automation_episode_runs','run_id'),('automation_series_requests','series_id')]:
            if table in names:
                result['attention']+=[dict(r) for r in c.execute(f"SELECT {identifier} AS target_id,status FROM {table} WHERE status IN ('needs_reconciliation','launch_failed','failed') ORDER BY updated_at DESC LIMIT 20")]
    try:
        free=shutil.disk_usage(path.parent).free;result['disk_free_gib']=round(free/1024**3,2)
        values={line.split(':',1)[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemAvailable:','MemTotal:'))}
        result['host_available_ram_gib']=round(values['MemAvailable']/1024**2,2)
        result['host_total_ram_gib']=round(values['MemTotal']/1024**2,2)
    except (OSError,KeyError,ValueError):pass
    return result
