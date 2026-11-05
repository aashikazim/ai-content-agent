"""Supervised generation consumer; never approves or publishes."""
import os
import signal
import threading
from dotenv import load_dotenv


def _consume():
    load_dotenv();os.umask(0o077)
    from app.services.structured_logging import configure
    configure()
    from app.api.routes import _execute_job_inline,_execute_script_inline
    from app.services.work_queue import WorkQueue
    from app.services.production_tools import ProductionTools
    import sys
    queue=WorkQueue();stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    import time
    from uuid import uuid4
    identifier=str(uuid4())
    with queue.connect() as conn:conn.execute('INSERT INTO app_worker_heartbeats VALUES (?,?)',(identifier,int(time.time())))
    def beat():
        while not stop.wait(5):
            with queue.connect() as conn:conn.execute('UPDATE app_worker_heartbeats SET heartbeat=? WHERE worker_id=?',(int(time.time()),identifier))
    threading.Thread(target=beat,daemon=True).start()
    try:
        while not stop.is_set():
            if not queue.run_next({'job':_execute_job_inline,'script':_execute_script_inline,'tool':ProductionTools(queue.db_path).run}):
                if '--drain' in sys.argv:break
                stop.wait(2)
    finally:
        stop.set()
        with queue.connect() as conn:conn.execute('DELETE FROM app_worker_heartbeats WHERE worker_id=?',(identifier,))


def main():
    # One consumer across supervised and detached queue drainers. Inference imports
    # happen after the lock so queued CPU/GPU jobs cannot exhaust shared DGX RAM.
    load_dotenv();os.umask(0o077)
    from pathlib import Path
    import fcntl
    from app.services.work_queue import WorkQueue
    queue=WorkQueue()
    path=Path(queue.db_path).with_suffix('.worker.lock')
    with path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        _consume()


if __name__=='__main__':main()
