"""Durable authorization for a bounded set of local, never-submitted shots."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4


class LocalProductionRunner:
    def __init__(self, service):
        self.service = service

    def launch(self, production_id, attempt_ids=None):
        value = self.service.editable(production_id)
        if value['configuration']['video_provider'] != 'local_cinematic':
            raise ValueError('The local coordinator never submits cloud generation')
        with self.service.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            active = conn.execute("SELECT run_id,heartbeat FROM local_production_runs WHERE production_id=? AND status='running'", (production_id,)).fetchone()
            if active:
                if active['heartbeat'] < time.time() - 120:
                    raise ValueError('Coordinator heartbeat is stale; inspect it before explicitly resuming')
                return active['run_id']
            allowed = [item['attempt_id'] for item in value['shots'] if item['status'] == 'queued'
                       and (attempt_ids is None or item['attempt_id'] in attempt_ids)]
            if not allowed:
                raise ValueError('No authorized, never-submitted local shots remain')
            identifier = str(uuid4())
            now = int(time.time())
            conn.execute('INSERT INTO local_production_runs VALUES (?,?,?,\'running\',?,NULL,?)',
                         (identifier, production_id, json.dumps(allowed), now, now))
        environment = {key: value for key, value in os.environ.items() if key in {
            'PATH','HOME','LANG','LD_LIBRARY_PATH','CUDA_VISIBLE_DEVICES','CUDA_HOME','TMPDIR',
            'LOCAL_CINEMATIC_ROOT','LOCAL_CINEMATIC_PYTHON','LOCAL_CINEMATIC_GPU_MEMORY_FRACTION',
            'LOCAL_CINEMATIC_MIN_AVAILABLE_GB','LOCAL_CINEMATIC_STAGE_TIMEOUT_SECONDS',
            'CLOUD_VIDEOS_DIR','CHARACTER_REFERENCES_DIR','DRIVING_REFERENCES_DIR','CONTENT_PLANS_DIR','TTS_VOICE'}}
        try:
            child = subprocess.Popen([sys.executable, '-m', 'app.workers.local_production_coordinator',
                self.service.storage.db_path, identifier], cwd=Path(__file__).resolve().parents[2],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            threading.Thread(target=child.wait, daemon=True).start()
        except OSError:
            with self.service.storage.connect() as conn:
                conn.execute("UPDATE local_production_runs SET status='stopped',error='Coordinator could not start; no automatic retry' WHERE run_id=?", (identifier,))
            raise ValueError('Coordinator could not start; inspect local setup and resume manually') from None
        return identifier

    def pause(self, production_id):
        self.service.editable(production_id)
        with self.service.storage.connect() as conn:
            conn.execute("UPDATE local_production_runs SET status='paused' WHERE production_id=? AND status='running'", (production_id,))
        return {'production_id': production_id, 'status': 'paused', 'notice': 'No further shots will start. Existing neural tasks are preserved.'}

    def tick(self, run_id):
        with self.service.storage.connect() as conn:
            row = conn.execute('SELECT * FROM local_production_runs WHERE run_id=?', (run_id,)).fetchone()
            if not row or row['status'] != 'running':
                return False
            conn.execute('UPDATE local_production_runs SET heartbeat=? WHERE run_id=?', (int(time.time()), run_id))
        allowed = json.loads(row['attempt_ids_json'])
        value = self.service.process(row['production_id'], submit_queued=True, attempt_ids=allowed)
        stop = value['status'] in {'technical_ready','pending_review','approved','failed','needs_reconciliation','assembly_failed','continuity_stale','candidate_ready'}
        authorized = [shot for shot in value.get('shots',[]) if shot['attempt_id'] in allowed]
        if len(authorized) == len(allowed) and all(shot['status'] in {'completed','cancelled','failed','validation_failed','needs_reconciliation','download_failed'} for shot in authorized):
            stop = True  # A bounded run never expands itself to other queued shots.
        if stop:
            with self.service.storage.connect() as conn:
                conn.execute("UPDATE local_production_runs SET status='stopped',error=? WHERE run_id=? AND status='running'", (value.get('error'),run_id))
        return not stop
