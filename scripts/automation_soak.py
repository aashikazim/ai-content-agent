"""Bounded foreground validation; no service, scheduler, GPU or upload starts.

Requires the repository's test environment. Uses recorded source adapters, mocked
media/planners and the real queue/Director/Governor paths in a marked private DB.
"""
import argparse
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
import signal
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))


def atomic_json(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.chmod(0o600);temporary.replace(path)


def source_fingerprints():
    repository=Path(__file__).absolute().parents[1]
    files=list((repository/'app/automation').rglob('*.py'))
    files += [repository/p for p in ('scripts/automation_soak.py','tests/automation_soak_harness.py',
        'tests/test_automation_governor.py','tests/test_automation_series.py','tests/test_long_drama.py',
        'app/services/work_queue.py','app/services/cloud_video_service.py','app/services/publishing_service.py')]
    return {str(p.relative_to(repository)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}


def run(root,*,smoke_seconds=None,interval=30):
    from app.automation.clock import SystemClock,utc_now
    from tests.automation_soak_harness import SoakHarness
    root=Path(root).resolve()
    # The harness verifies allowed roots and marker before opening its fixed DB.
    clock=SystemClock();h=SoakHarness(root,clock)
    mode='smoke' if smoke_seconds is not None else 'real_24h_dry_run'
    duration=float(smoke_seconds) if smoke_seconds is not None else 86400.
    progress_path=root/'progress.json';old=json.loads(progress_path.read_text()) if progress_path.exists() else None
    if old and (old['mode']!=mode or old['required_active_seconds']!=duration):
        h.close();raise ValueError('Cannot change a verification run duration or mode on resume')
    fingerprints=source_fingerprints()
    if old and old.get('source_fingerprints')!=fingerprints:
        h.close();raise ValueError('Verification source changed; preserve this run and use a new root')
    progress=old or dict(source_fingerprints=fingerprints,mode=mode,started_at=utc_now(clock).isoformat(),required_active_seconds=duration,active_elapsed_seconds=0.,sessions=0,ticks=0,status='running')
    if progress['status']=='complete':h.close();return progress
    with (root/'foreground.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:h.close();raise ValueError('This verification root already has an active foreground process') from None
        progress['sessions']+=1;progress['status']='running';progress['continuous']=progress['sessions']==1
        progress['publishing']='dry_run';progress['providers']='recorded sources + mocked generation/media/planning'
        previous=time.monotonic()
        atomic_json(progress_path,progress)
        try:
            while True:
                result=h.tick(bootstrap=progress['ticks']==0)
                if result['publication_rows']!=0:raise AssertionError('24h dry-run unexpectedly created a publication row')
                now=time.monotonic();progress['active_elapsed_seconds']+=now-previous;previous=now
                progress['ticks']+=1;progress['last_heartbeat_at']=utc_now(clock).isoformat();progress['last_result']=result
                real_elapsed=(utc_now(clock)-datetime.fromisoformat(progress['started_at'])).total_seconds()
                progress['wall_elapsed_seconds']=real_elapsed
                done=progress['active_elapsed_seconds']>=duration and real_elapsed>=duration
                progress['status']='complete' if done else 'running'
                atomic_json(progress_path,progress)
                with (root/'heartbeats.jsonl').open('a') as log:log.write(json.dumps({'at':progress['last_heartbeat_at'],'active_seconds':round(progress['active_elapsed_seconds'],3),'ticks':progress['ticks'],'forbidden_calls':result['forbidden_calls'],'duplicate_calls':result['duplicate_calls'],'publication_rows':result['publication_rows']})+'\n')
                if progress['ticks']==1 or done or progress['ticks']%120==0:
                    print(json.dumps({'status':progress['status'],'mode':mode,'active_hours':round(progress['active_elapsed_seconds']/3600,4),'ticks':progress['ticks'],'publication_rows':0,'forbidden_calls':0}),flush=True)
                if done:break
                time.sleep(min(interval,max(.1,duration-progress['active_elapsed_seconds'])))
        except BaseException as exc:
            progress['status']='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'failed'
            progress['error_type']=type(exc).__name__;atomic_json(progress_path,progress);raise
        finally:h.close()
    return progress


def main():
    os.umask(0o077)
    def stopped(number,frame):raise SystemExit(128+number)
    signal.signal(signal.SIGTERM,stopped)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--smoke-seconds',type=float,help='Short development check, never counts as24h acceptance')
    args=parser.parse_args()
    if args.smoke_seconds is not None and not .1<=args.smoke_seconds<=60:parser.error('Smoke duration must be0.1–60seconds')
    run(args.root,smoke_seconds=args.smoke_seconds)


if __name__=='__main__':main()
