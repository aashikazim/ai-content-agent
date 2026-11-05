"""Single-host durable dispatch in the existing SQLite database.

Only never-claimed work is dispatched. A lost running lease is interrupted, never
requeued: paid requests and uploads must be reconciled by a person.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4


class WorkQueue:
    def __init__(self, db_path=None):
        self.db_path=str(Path(db_path or os.getenv('DATABASE_PATH','data/trends.db')).absolute())
        Path(self.db_path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS app_work_queue (
                work_id TEXT PRIMARY KEY,kind TEXT NOT NULL,target_id TEXT NOT NULL,
                status TEXT NOT NULL,lease TEXT,heartbeat INTEGER,error TEXT,
                created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(kind,target_id))''')
            conn.execute('CREATE TABLE IF NOT EXISTS app_worker_heartbeats (worker_id TEXT PRIMARY KEY,heartbeat INTEGER NOT NULL)')

    @contextmanager
    def connect(self):
        conn=sqlite3.connect(self.db_path,timeout=30);conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()

    def enqueue(self,kind,target_id,*,dispatch_key=None):
        if kind not in ('job','script','tool'):raise ValueError('Only generation jobs and script drafts can be queued')
        if dispatch_key:target_id=str(target_id)+'@'+str(dispatch_key)
        identifier=str(uuid4());now=int(time.time())
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO app_work_queue VALUES (?,?,?,'queued',NULL,NULL,NULL,?,?)",(identifier,kind,str(target_id),now,now))
            return dict(conn.execute('SELECT * FROM app_work_queue WHERE kind=? AND target_id=?',(kind,str(target_id))).fetchone())

    def recover(self):
        with self.connect() as conn:
            conn.execute("UPDATE app_work_queue SET status='interrupted',error='Worker lease expired. Inspect saved generation IDs and billing before any manual recovery.',updated_at=? WHERE status='running' AND heartbeat<?",(int(time.time()),int(time.time())-120))

    def run_next(self,handlers):
        self.recover();lease=str(uuid4());now=int(time.time())
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM app_work_queue WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:return False
            conn.execute("UPDATE app_work_queue SET status='running',lease=?,heartbeat=?,updated_at=? WHERE work_id=?",(lease,now,now,row['work_id']))
        stop=threading.Event()
        def beat():
            while not stop.wait(10):
                with self.connect() as conn:
                    conn.execute("UPDATE app_work_queue SET heartbeat=? WHERE work_id=? AND lease=? AND status='running'",(int(time.time()),row['work_id'],lease))
        threading.Thread(target=beat,daemon=True).start()
        try:
            handlers[row['kind']](row['target_id'].split('@',1)[0])
            status,error='finished',None
        except Exception:
            status,error='interrupted','Execution stopped; inspect the saved job/provider state before manual recovery. No automatic retry.'
        finally:stop.set()
        with self.connect() as conn:
            conn.execute("UPDATE app_work_queue SET status=?,error=?,updated_at=? WHERE work_id=? AND lease=? AND status='running'",(status,error,int(time.time()),row['work_id'],lease))
        return True
