"""Read-only readiness; checks never spend credits, contact accounts or load CUDA."""
import os
from pathlib import Path
import shutil
import sqlite3
import psutil


def readiness(db_path):
    from app.services import local_cinematic_models as local
    from app.services.speech_profiles import catalog as voices
    from app.services.still_images import ready as still_ready
    from app.services.clip_studio.media import analysis_python
    parent=Path(db_path).absolute().parent;parent.mkdir(parents=True,exist_ok=True)
    checks=[]
    for binary in ('ffmpeg','ffprobe','espeak-ng','node'):
        checks.append({'name':binary,'ready':bool(shutil.which(binary)),'required':binary in ('ffmpeg','ffprobe')})
    try:
        with sqlite3.connect(str(db_path),timeout=5) as conn:database=conn.execute('PRAGMA quick_check').fetchone()==('ok',)
    except sqlite3.Error:database=False
    workers=0
    try:
        import time
        with sqlite3.connect(str(db_path),timeout=5) as conn:
            workers=conn.execute('SELECT COUNT(*) FROM app_worker_heartbeats WHERE heartbeat>?',(int(time.time())-120,)).fetchone()[0]
    except sqlite3.Error:pass
    checks.append({'name':'durable worker heartbeat','ready':bool(workers),'required':os.getenv('DURABLE_WORKERS','false').lower()=='true'})
    checks += [{'name':'database','ready':database,'required':True},
        {'name':'CPU analysis runtime','ready':analysis_python().is_file(),'required':False},
        {'name':'standalone SDXL checkpoint','ready':still_ready(),'required':False},
        {'name':'neural speech profiles','ready':bool(voices()),'required':False}]
    return {'status':'ready' if all(c['ready'] for c in checks if c['required']) else 'setup_required',
        'checks':checks,'disk_free_gb':round(shutil.disk_usage(parent).free/1024**3,1),
        'memory_available_gb':round(psutil.virtual_memory().available/1024**3,1),
        'local_models':local.catalog_models(),'speech_profiles':voices(),
        'durable_worker_enabled':os.getenv('DURABLE_WORKERS','false').lower()=='true',
        'configured':{key:bool(os.getenv(variable)) for key,variable in {'pexels':'PEXELS_API_KEY','openai':'OPENAI_API_KEY','claude':'ANTHROPIC_API_KEY','runway':'RUNWAYML_API_SECRET','google':'GEMINI_API_KEY','facebook':'FACEBOOK_PAGE_ACCESS_TOKEN'}.items()},
        'manual_gates':{'approval':True,'publishing':True,'uncertain_external_retry':True},
        'account_access_verified':False}
