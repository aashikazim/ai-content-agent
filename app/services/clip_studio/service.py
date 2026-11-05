"""Persisted Clip Studio orchestration; existing production features stay independent."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.services.cloud_video_renderer import media_sha256, run
from app.services.provider_catalog import get_catalog
from .generators import MockGenerator
from .planner import MockPlanner
from .schemas import PlannedClip, ProjectRequest, Settings
from .storage import ClipStudioStorage, encoded


class ClipStudioService:
    def __init__(self, db_path=None):
        self.storage = ClipStudioStorage(db_path or os.getenv('DATABASE_PATH', 'data/trends.db'))
        self.root = Path(os.getenv('CLIP_STUDIO_DATA_DIR', 'data/clip_studio')).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def catalog():
        providers = [{key: item[key] for key in ('id', 'label', 'models', 'configured')}
                     for item in get_catalog()['script_providers'] if item['id'] in {'anthropic', 'openai'}]
        return {'mock': os.getenv('CLIP_STUDIO_MOCK', 'false').lower() == 'true',
                'phase': 7, 'real_generation_available': True, 'planning_estimate_usd': 0.40, 'planning_max_calls': 2, 'planner_providers': providers,
                'video_models': [{'id': 'ltx', 'label': 'LTX-2'}, {'id': 'wan', 'label': 'Wan'}, {'id': 'hybrid', 'label': 'Connected LTX + Wan'}],
                'notice': 'Mock mode uses CPU test patterns and no APIs. Real mode analyzes sources, plans with the selected hosted service and renders with native LTX/Wan or an explicitly configured ComfyUI workflow. Initial real exports enter manual Review; publishing remains a separate human action.'}

    @staticmethod
    def upload_limit():
        return max(1, min(int(os.getenv('CLIP_STUDIO_UPLOAD_MB', '256')), 1024)) * 1024**2

    def preflight(self, request):
        from .media import StudioError, analysis_python
        from .native import native_config
        from app.services.cinematic_cast_service import CinematicCastService
        settings = request.settings
        if not settings.paid_planning_confirmed:
            raise StudioError('Confirm the displayed planning estimate and at most two paid calls before real generation')
        key = 'ANTHROPIC_API_KEY' if settings.planner_provider == 'anthropic' else 'OPENAI_API_KEY'
        if not os.getenv(key):
            raise StudioError('Configure the API key for the selected planning provider')
        if settings.cast_package_id:
            CinematicCastService(self.storage.db_path).get(settings.cast_package_id)
        if settings.continuity == 'connected' and not settings.cast_package_id:
            raise StudioError('Connected shots require an approved cast package')
        if settings.video_model in ('wan','hybrid') and not settings.cast_package_id:
            raise StudioError('Wan/hybrid requires an approved cast package from Local Video Studio')
        if settings.video_model == 'wan' and (request.source_kind == 'images' or settings.continuity == 'connected' or settings.narration == 'model'):
            raise StudioError('Wan needs a video driving source, anchored continuity and source or character speech; choose hybrid for connected action')
        if settings.narration == 'character_tts' and not settings.cast_package_id:
            raise StudioError('Fixed character voices require a cast package')
        if (settings.reattach_audio or settings.narration == 'source') and request.source_kind == 'images':
            raise StudioError('Image sources do not contain source audio')
        if settings.backend == 'native':
            from app.services.local_cinematic_service import LocalCinematicService
            from app.services.gemini_video_service import VideoProviderError
            try:
                LocalCinematicService(native_config(settings)).check_model()
            except VideoProviderError as error:
                raise StudioError(str(error)) from None
        else:
            from .comfy import ComfyAdapter, inject_workflow
            if settings.video_model == 'hybrid':
                raise StudioError('Connected hybrid is available through the native backend; ComfyUI accepts explicit LTX or Wan workflows')
            values={'prompt':'','negative_prompt':'','reference':'','duration':3,'width':896,'height':512,'seed':0}
            if settings.cast_package_id:values['character_reference']='actor-reference.png'
            if settings.continuity=='connected':values['continuation_reference']='carry-frame.png'
            if settings.video_model=='wan':values['source_video']='driving.mp4'
            inject_workflow(ComfyAdapter.template(settings.video_model), values)
        if request.source_kind != 'images' and not analysis_python().is_file():
            raise StudioError('Install the isolated analysis runtime with scripts/setup_clip_studio.py')

    def existing(self, request, actor):
        with self.storage.connect() as conn:
            row = conn.execute('SELECT project_id,request_json FROM clip_studio_projects WHERE actor=? AND request_id=?',
                               (actor, str(request.request_id))).fetchone()
        if row:
            if ProjectRequest.model_validate_json(row['request_json']).model_dump(mode='json') != request.model_dump(mode='json'):
                raise ValueError('This request ID already belongs to different settings; inspect the saved project')
            return self.get(row['project_id'], actor)

    def create(self, request, uploads, actor):
        mock = self.catalog()['mock']
        saved = self.existing(request, actor)
        if saved:
            return saved
        if not mock:
            self.preflight(request)
        elif request.settings.reattach_audio:
            raise ValueError('Source audio reattachment requires real mode')
        allowed = {item['id']: {model['id'] for model in item['models']} for item in self.catalog()['planner_providers']}
        if request.settings.planner_model not in allowed[request.settings.planner_provider]:
            raise ValueError('Choose a supported model for the selected planning provider')
        expected = len(uploads)
        if (request.source_kind == 'youtube' and expected) or (request.source_kind == 'video' and expected != 1) or (request.source_kind == 'images' and not 1 <= expected <= 8):
            raise ValueError('Choose one video, one to eight images, or a YouTube URL without uploads')
        project_id, job_id = str(uuid4()), str(uuid4())
        folder = self.root/project_id
        folder.mkdir(mode=0o700)
        sources, total = [], 0
        committed = False
        try:
            if request.source_kind == 'youtube':
                sources.append((str(uuid4()), None, None, {'url': request.source_url, 'mock': mock,
                               **({'duration_seconds': max(30, request.settings.clip_count * request.settings.duration_seconds)} if mock else {})}))
            for upload in uploads:
                identifier = str(uuid4())
                suffix = Path(upload.filename or '').suffix.lower()
                allowed_suffixes = {'.mp4', '.mov', '.webm', '.mkv'} if request.source_kind == 'video' else {'.png', '.jpg', '.jpeg', '.webp'}
                if suffix not in allowed_suffixes:
                    raise ValueError('Unsupported source file type')
                path = folder/(identifier + suffix)
                with path.open('xb') as output:
                    while chunk := upload.file.read(1024**2):
                        total += len(chunk)
                        if total > self.upload_limit():
                            raise ValueError('Source uploads exceed the configured size limit')
                        output.write(chunk)
                path.chmod(0o600)
                if not path.stat().st_size:
                    raise ValueError('The uploaded file is empty')
                if request.source_kind == 'images':
                    try:
                        with Image.open(path) as image:
                            if image.format not in {'PNG', 'JPEG', 'WEBP'} or min(image.size) < 128 or max(image.size) > 8192 or image.width * image.height > 32000000:
                                raise ValueError('Use an image from 128px to 8192px and at most 32 megapixels')
                            metadata = {'width': image.width, 'height': image.height}
                            image.load()
                            normalized = folder/(identifier + '.normalized.png')
                            image.convert('RGB').save(normalized)
                        normalized.chmod(0o600)
                        path.unlink()
                        path = normalized
                    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                        raise ValueError('The uploaded image cannot be decoded') from None
                else:
                    # Probe only local files and safe container formats, never a playlist/URL.
                    result = run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_format',
                                  '-show_streams', '-of', 'json', str(path)], timeout=30)
                    info = json.loads(result.stdout)
                    video = next((s for s in info.get('streams', []) if s['codec_type'] == 'video'), None)
                    duration = float(info.get('format', {}).get('duration', 0))
                    formats = set(info.get('format', {}).get('format_name', '').split(','))
                    if not video or not formats.intersection({'mov', 'mp4', 'matroska', 'webm'}) or not 0 < duration <= 3600 or min(video['width'], video['height']) < 128 or max(video['width'], video['height']) > 8192:
                        raise ValueError('Use a decodable video up to one hour, with dimensions from 128px to 8192px')
                    metadata = {'duration_seconds': duration, 'width': video['width'], 'height': video['height']}
                sources.append((identifier, str(path), media_sha256(path), metadata))
            now = int(time.time())
            with self.storage.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if conn.execute("SELECT COUNT(*) FROM clip_studio_jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                    raise ValueError('The Clip Studio queue is full; wait for a job to finish')
                # Serialized duplicate check also covers simultaneous browser submissions.
                duplicate = conn.execute('SELECT project_id,request_json FROM clip_studio_projects WHERE actor=? AND request_id=?', (actor, str(request.request_id))).fetchone()
                if duplicate:
                    if ProjectRequest.model_validate_json(duplicate['request_json']).model_dump(mode='json') != request.model_dump(mode='json'):
                        raise ValueError('The request ID was already used with different settings')
                    shutil.rmtree(folder)
                    return self.get(duplicate['project_id'], actor, recover=False)
                conn.execute('INSERT INTO clip_studio_projects VALUES (?,?,?,?,?,?)',
                             (project_id, actor, str(request.request_id), request.model_dump_json(), now, now))
                for index, (identifier, path, digest, metadata) in enumerate(sources):
                    conn.execute('INSERT INTO clip_studio_sources VALUES (?,?,?,?,?,?,?)',
                                 (identifier, project_id, index, request.source_kind, path, digest, encoded(metadata)))
                conn.execute("INSERT INTO clip_studio_jobs VALUES (?,?,?,'generate','queued','queued',0,?,NULL,NULL,NULL,?)",
                             (job_id, project_id, str(request.request_id), encoded({'mode': 'mock' if mock else 'real', 'attempt_ids': None, 'planning_authorized': request.settings.paid_planning_confirmed}), now))
                self.storage.event(conn, job_id, 'queued', 'Mock workflow authorized; no paid planning or GPU requests' if mock else 'Human authorized up to two paid planning calls and local generation; approval/publishing remain manual')
            committed = True
        finally:
            for upload in uploads:
                upload.file.close()
            if not committed and folder.exists():
                shutil.rmtree(folder)
        self.launch(job_id)
        return self.get(project_id, actor)

    def recover(self):
        with self.storage.connect() as conn:
            rows = conn.execute("SELECT job_id FROM clip_studio_jobs WHERE status='running' AND heartbeat<?", (int(time.time())-300,)).fetchall()
            for row in rows:
                conn.execute("UPDATE clip_studio_jobs SET status='interrupted',error='Worker heartbeat stopped. Inspect saved outputs and explicitly retry; no request was resubmitted.' WHERE job_id=?", (row[0],))

    def get(self, project_id, actor, *, recover=True):
        if recover:
            self.recover()
        with self.storage.connect() as conn:
            row = conn.execute('SELECT * FROM clip_studio_projects WHERE project_id=? AND actor=?', (project_id, actor)).fetchone()
            if not row:
                raise ValueError('Clip Studio project not found')
            value = dict(row)
            value['request'] = json.loads(value.pop('request_json'))
            value['sources'] = [{'source_id': s['source_id'], 'kind': s['kind'], **json.loads(s['metadata_json'])}
                                for s in conn.execute('SELECT * FROM clip_studio_sources WHERE project_id=? ORDER BY position', (project_id,))]
            value['jobs'] = [self.storage.job(j) for j in conn.execute('SELECT * FROM clip_studio_jobs WHERE project_id=? ORDER BY created_at DESC,rowid DESC', (project_id,))]
            value['clips'] = []
            for clip in conn.execute('SELECT * FROM clip_studio_clips WHERE project_id=? ORDER BY position', (project_id,)):
                item = dict(clip)
                item['plan'] = json.loads(item.pop('plan_json'))
                item['attempts'] = []
                for attempt in conn.execute('SELECT * FROM clip_studio_render_attempts WHERE clip_id=? ORDER BY version', (item['clip_id'],)):
                    saved = dict(attempt)
                    saved.pop('output_path'); saved.pop('thumbnail_path')
                    saved['quality'] = json.loads(saved.pop('quality_json') or 'null')
                    base = '/api/v1/clip-studio/attempts/' + saved['attempt_id']
                    saved['video_url'] = base + '/video' if saved['status'] == 'completed' else None
                    saved['thumbnail_url'] = base + '/thumbnail' if saved['status'] == 'completed' else None
                    item['attempts'].append(saved)
                for saved in item['attempts']:
                    saved['chunks'] = [dict(c) for c in conn.execute('SELECT position,status,external_id FROM clip_studio_chunks WHERE attempt_id=? ORDER BY position', (saved['attempt_id'],))]
                value['clips'].append(item)
            value['events'] = [dict(e) for e in conn.execute('SELECT e.* FROM clip_studio_events e JOIN clip_studio_jobs j ON j.job_id=e.job_id WHERE j.project_id=? ORDER BY e.event_id DESC LIMIT 100', (project_id,))]
        with self.storage.connect() as conn:
            initial = conn.execute('SELECT request_json FROM clip_studio_jobs WHERE project_id=? ORDER BY created_at,rowid LIMIT 1', (project_id,)).fetchone()
            links = dict(conn.execute('SELECT r.attempt_id,r.job_id FROM clip_studio_review_links r JOIN clip_studio_render_attempts a ON a.attempt_id=r.attempt_id JOIN clip_studio_clips c ON c.clip_id=a.clip_id WHERE c.project_id=?', (project_id,)).fetchall())
        value['mode'] = json.loads(initial[0]).get('mode', 'mock') if initial else 'mock'
        with self.storage.connect() as conn:
            value['planning_calls'] = [dict(c) for c in conn.execute('SELECT call_id,provider,status,external_id FROM clip_studio_calls WHERE project_id=? ORDER BY created_at,rowid', (project_id,))]
        for item in value['clips']:
            for attempt in item['attempts']:
                attempt['review_job_id'] = links.get(attempt['attempt_id'])
                previous = (attempt['quality'] or {}).get('previous_attempt_id')
                attempt['stale_continuity'] = bool(previous and not any(c['selected_attempt_id'] == previous for c in value['clips']))
        return value

    def list_projects(self, actor):
        self.recover()
        with self.storage.connect() as conn:
            rows = conn.execute('''SELECT p.*,(SELECT j.status FROM clip_studio_jobs j WHERE j.project_id=p.project_id
                ORDER BY j.created_at DESC,j.rowid DESC LIMIT 1) AS status FROM clip_studio_projects p
                WHERE actor=? ORDER BY created_at DESC,rowid DESC LIMIT 100''', (actor,)).fetchall()
        return [{'project_id': p['project_id'], 'instruction': json.loads(p['request_json'])['instruction'],
                 'created_at': p['created_at'], 'status': p['status'] or 'draft'} for p in rows]

    def launch(self, job_id):
        environment = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'LANG', 'TMPDIR'}}
        environment['CLIP_STUDIO_DATA_DIR'] = str(self.root)
        with self.storage.connect() as conn:
            payload = conn.execute('SELECT request_json FROM clip_studio_jobs WHERE job_id=?', (job_id,)).fetchone()
        if payload and json.loads(payload[0]).get('mode') == 'real':
            allowed = {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'COMFYUI_BASE_URL', 'LOCAL_CINEMATIC_ROOT', 'LOCAL_CINEMATIC_PYTHON', 'CHARACTER_REFERENCES_DIR', 'CUDA_VISIBLE_DEVICES', 'CUDA_HOME', 'LD_LIBRARY_PATH'}
            environment.update({key:value for key,value in os.environ.items() if key in allowed or key.startswith('CLIP_STUDIO_') or key.startswith('LOCAL_CINEMATIC_')})
        try:
            child = subprocess.Popen([sys.executable, '-m', 'app.workers.clip_studio_worker', self.storage.db_path],
                                     cwd=Path(__file__).absolute().parents[3], env=environment,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
            def wait():
                child.wait()
                with self.storage.connect() as conn:
                    conn.execute("UPDATE clip_studio_jobs SET status='failed',error='Clip Studio worker exited before claiming this job; retry explicitly.' WHERE job_id=? AND status='queued'", (job_id,))
            threading.Thread(target=wait, daemon=True).start()
        except OSError:
            with self.storage.connect() as conn:
                conn.execute("UPDATE clip_studio_jobs SET status='failed',error='The Clip Studio worker could not start; retry explicitly' WHERE job_id=? AND status='queued'", (job_id,))

    def new_attempt(self, conn, clip_id, settings):
        version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM clip_studio_render_attempts WHERE clip_id=?', (clip_id,)).fetchone()[0]
        identifier = str(uuid4())
        position = conn.execute('SELECT position FROM clip_studio_clips WHERE clip_id=?', (clip_id,)).fetchone()[0]
        seed = ((settings.seed if settings.seed is not None else secrets.randbelow(2147483647)) + position*997 + (version-1)*1000) % 2147483648
        conn.execute("INSERT INTO clip_studio_render_attempts VALUES (?,?,?,?,?,'queued',NULL,NULL,NULL,NULL,NULL,NULL,?)",
                     (identifier, clip_id, version, seed, settings.video_model, int(time.time())))
        return identifier

    def enqueue(self, project_id, actor, request_id, *, clip_id=None, retry_job_id=None, paid_planning_confirmed=False):
        project = self.get(project_id, actor)
        settings = Settings.model_validate(project['request']['settings'])
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT job_id,request_json FROM clip_studio_jobs WHERE project_id=? AND request_id=?', (project_id, str(request_id))).fetchone()
            action = {'clip_id': clip_id, 'retry_job_id': retry_job_id}
            if existing:
                if json.loads(existing['request_json']).get('action') != action:
                    raise ValueError('This request ID was used for a different action')
                return self.storage.job(conn.execute('SELECT * FROM clip_studio_jobs WHERE job_id=?', (existing['job_id'],)).fetchone())
            if conn.execute("SELECT 1 FROM clip_studio_jobs WHERE project_id=? AND status IN ('queued','running')", (project_id,)).fetchone():
                raise ValueError('Wait for this project’s active job before requesting another')
            if conn.execute("SELECT COUNT(*) FROM clip_studio_jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                raise ValueError('The Clip Studio queue is full; wait for a job to finish')
            attempts = []
            if project['mode'] == 'real':
                if not project['clips'] and not paid_planning_confirmed:
                    raise ValueError('Retrying planning requires explicit confirmation of up to two new paid calls')
                if any(j['status'] == 'uncertain' for j in project['jobs']) or any(a['status'] in ('running','uncertain') for c in project['clips'] for a in c['attempts']):
                    raise ValueError('An uncertain generation exists. Poll saved IDs or explicitly reconcile before new generation')
                unresolved = conn.execute("SELECT 1 FROM clip_studio_calls WHERE project_id=? AND status IN ('submitting','uncertain')", (project_id,)).fetchone()
                if unresolved:
                    raise ValueError('Reconcile the uncertain paid planning request before retrying')
            if clip_id:
                if not any(c['clip_id'] == clip_id for c in project['clips']):
                    raise ValueError('Clip not found in this project')
                attempts = [self.new_attempt(conn, clip_id, settings)]
                kind = 'regenerate'
            else:
                previous = next((j for j in project['jobs'] if j['job_id'] == retry_job_id), None)
                if not previous or previous['status'] not in {'failed', 'partial', 'interrupted'}:
                    raise ValueError('Choose a failed, partial or interrupted job to retry')
                # Retry only failed items from that job; successful outputs remain unchanged.
                old = json.loads(conn.execute('SELECT request_json FROM clip_studio_jobs WHERE job_id=?', (retry_job_id,)).fetchone()[0])
                targets = old.get('attempt_ids')
                for item in project['clips']:
                    candidates = [a for a in item['attempts'] if targets is None or a['attempt_id'] in targets]
                    if candidates and not any(a['status'] == 'completed' for a in candidates):
                        for candidate in candidates:
                            conn.execute("UPDATE clip_studio_render_attempts SET status='cancelled' WHERE attempt_id=? AND status IN ('queued','running')", (candidate['attempt_id'],))
                        attempts.append(self.new_attempt(conn, item['clip_id'], settings))
                kind = 'retry'
            identifier = str(uuid4())
            payload = {'mode': project['mode'], 'attempt_ids': attempts or None, 'action': action, 'planning_authorized': paid_planning_confirmed}
            conn.execute("INSERT INTO clip_studio_jobs VALUES (?,?,?,?,'queued','queued',0,?,NULL,NULL,NULL,?)",
                         (identifier, project_id, str(request_id), kind, encoded(payload), int(time.time())))
            self.storage.event(conn, identifier, 'queued', 'Human requested regeneration/retry; earlier versions preserved')
        self.launch(identifier)
        return self.storage.job({'job_id': identifier, 'status': 'queued', 'request_json': encoded(payload)})

    def select(self, project_id, clip_id, attempt_id, actor):
        project = self.get(project_id, actor)
        item = next((c for c in project['clips'] if c['clip_id'] == clip_id), None)
        if not item or not any(a['attempt_id'] == attempt_id and a['status'] == 'completed' for a in item['attempts']):
            raise ValueError('Choose a completed version belonging to this clip')
        self.asset(attempt_id, actor, 'video')
        with self.storage.connect() as conn:
            conn.execute('UPDATE clip_studio_clips SET selected_attempt_id=? WHERE clip_id=?', (attempt_id, clip_id))
        return self.get(project_id, actor)

    def asset(self, attempt_id, actor, kind):
        with self.storage.connect() as conn:
            row = conn.execute('''SELECT a.* FROM clip_studio_render_attempts a JOIN clip_studio_clips c ON c.clip_id=a.clip_id
                JOIN clip_studio_projects p ON p.project_id=c.project_id WHERE a.attempt_id=? AND p.actor=? AND a.status='completed' ''', (attempt_id, actor)).fetchone()
        if not row:
            raise ValueError('Completed clip asset not found')
        path = Path(row['output_path'] if kind == 'video' else row['thumbnail_path']).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise ValueError('The clip asset is unavailable')
        if kind == 'video' and media_sha256(path) != json.loads(row['quality_json'])['sha256']:
            raise ValueError('The clip changed after validation; inspect or regenerate it')
        return path

    def run_next(self, generator=None):
        """Called only by the serialized worker (or tests), never a status GET."""
        token = str(uuid4())
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT * FROM clip_studio_jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:
                return False
            conn.execute("UPDATE clip_studio_jobs SET status='running',lease_token=?,heartbeat=? WHERE job_id=?", (token, int(time.time()), row['job_id']))
            project = conn.execute('SELECT request_json FROM clip_studio_projects WHERE project_id=?', (row['project_id'],)).fetchone()
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(5):
                with self.storage.connect() as conn:
                    conn.execute("UPDATE clip_studio_jobs SET heartbeat=? WHERE job_id=? AND lease_token=? AND status='running'", (int(time.time()), row['job_id'], token))
        threading.Thread(target=heartbeat, daemon=True).start()
        def stage(name, progress, message):
            with self.storage.connect() as conn:
                changed = conn.execute("UPDATE clip_studio_jobs SET stage=?,progress=MAX(progress,?),heartbeat=? WHERE job_id=? AND lease_token=? AND status='running'", (name, progress, int(time.time()), row['job_id'], token))
                if not changed.rowcount:
                    raise ValueError('Job lease is no longer active')
                self.storage.event(conn, row['job_id'], name, message)
        try:
            request = ProjectRequest.model_validate_json(project[0])
            payload = json.loads(row['request_json'])
            if payload['mode'] == 'real':
                from .pipeline import RealPipeline
                RealPipeline(self).run(row, request, payload, stage, generator)
                return True
            if payload['mode'] != 'mock':
                raise ValueError('Unknown job mode')
            stage('ingest', .05, 'Mock ingest; YouTube URLs are not downloaded')
            with self.storage.connect() as conn:
                sources = [dict(s) for s in conn.execute('SELECT * FROM clip_studio_sources WHERE project_id=? ORDER BY position', (row['project_id'],))]
            for source in sources:
                if source['path'] and media_sha256(source['path']) != source['sha256']:
                    raise ValueError('A source changed')
            stage('analyze', .12, 'Mock analysis; no scene detection or transcription performed')
            analysis = {'mock': True, 'transcript': [], 'scenes': [], 'notice': 'Analysis will be implemented in Phase 3'}
            with self.storage.connect() as conn:
                conn.execute('INSERT OR REPLACE INTO clip_studio_analyses VALUES (?,?,?)', (row['project_id'], encoded(analysis), int(time.time())))
                clips_exist = conn.execute('SELECT 1 FROM clip_studio_clips WHERE project_id=?', (row['project_id'],)).fetchone()
            stage('plan', .2, 'Mock planner; selected provider/model saved, no API request or charge')
            if not clips_exist:
                plan = MockPlanner().plan(request, sources)
                plan_id = str(uuid4())
                with self.storage.connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute('INSERT INTO clip_studio_plans VALUES (?,?,?,?,?,?,NULL,?,?)', (plan_id, row['project_id'], 1, request.settings.planner_provider, request.settings.planner_model, plan.model_dump_json(), encoded({'mock': True, 'paid_calls': 0, 'cost_usd': '0'}), int(time.time())))
                    for index, item in enumerate(plan.clips):
                        clip_id = str(uuid4())
                        conn.execute('INSERT INTO clip_studio_clips VALUES (?,?,?,?,?,?,?,?,NULL)', (clip_id, row['project_id'], plan_id, sources[index % len(sources)]['source_id'], index, 'video-1', index * request.settings.duration_seconds, item.model_dump_json()))
                        self.new_attempt(conn, clip_id, request.settings)
            with self.storage.connect() as conn:
                attempts = [dict(a) for a in conn.execute("SELECT a.*,c.plan_json,c.source_id FROM clip_studio_render_attempts a JOIN clip_studio_clips c ON c.clip_id=a.clip_id WHERE c.project_id=? AND a.status='queued' ORDER BY c.position,a.version", (row['project_id'],))
                            if payload['attempt_ids'] is None or a['attempt_id'] in payload['attempt_ids']]
                payload['attempt_ids'] = [a['attempt_id'] for a in attempts]
                conn.execute('UPDATE clip_studio_jobs SET request_json=? WHERE job_id=?', (encoded(payload), row['job_id']))
            generator = generator or MockGenerator()
            for index, attempt in enumerate(attempts):
                stage(f'generate:{index+1}', .25 + .65*index/max(1, len(attempts)), f'Generating mock clip {index+1} of {len(attempts)}')
                with self.storage.connect() as conn:
                    conn.execute("UPDATE clip_studio_render_attempts SET status='running' WHERE attempt_id=? AND status='queued'", (attempt['attempt_id'],))
                directory = self.root/row['project_id']/'renders'/attempt['attempt_id']
                try:
                    output, thumbnail, quality = generator.generate(PlannedClip.model_validate_json(attempt['plan_json']), request.settings, attempt['seed'], directory,
                        references={'primary_source_id': attempt['source_id'], 'sources': sources})
                    quality.update(generator='mock', requested_model=attempt['model'])
                    stage('assemble', .25+.65*(index+1)/max(1, len(attempts)), 'Normalized and validated an individual mock export with thumbnail')
                    with self.storage.connect() as conn:
                        conn.execute("UPDATE clip_studio_render_attempts SET status='completed',output_path=?,thumbnail_path=?,quality_json=?,error=NULL WHERE attempt_id=?", (str(output), str(thumbnail), encoded(quality), attempt['attempt_id']))
                        conn.execute('UPDATE clip_studio_clips SET selected_attempt_id=COALESCE(selected_attempt_id,?) WHERE clip_id=?', (attempt['attempt_id'], attempt['clip_id']))
                except Exception:
                    # No arbitrary exception/provider response can expose credentials.
                    with self.storage.connect() as conn:
                        conn.execute("UPDATE clip_studio_render_attempts SET status='failed',error='Mock rendering or media validation failed. Retry this clip explicitly.' WHERE attempt_id=?", (attempt['attempt_id'],))
                    if directory.exists():
                        shutil.rmtree(directory)
            with self.storage.connect() as conn:
                current = [conn.execute('SELECT status FROM clip_studio_render_attempts WHERE attempt_id=?', (a['attempt_id'],)).fetchone()[0] for a in attempts]
                status = 'completed' if current and all(s == 'completed' for s in current) else 'partial' if any(s == 'completed' for s in current) else 'failed'
                conn.execute("UPDATE clip_studio_jobs SET status=?,stage='finished',progress=1,heartbeat=? WHERE job_id=? AND lease_token=? AND status='running'", (status, int(time.time()), row['job_id'], token))
                conn.execute('UPDATE clip_studio_projects SET updated_at=? WHERE project_id=?', (int(time.time()), row['project_id']))
                self.storage.event(conn, row['job_id'], 'finished', 'Mock exports finished; no approval or publishing performed')
        except Exception as error:
            from .media import StudioError
            with self.storage.connect() as conn:
                uncertain = getattr(error, 'uncertain', False) or bool(conn.execute("SELECT 1 FROM clip_studio_calls WHERE project_id=? AND status IN ('submitting','uncertain')", (row['project_id'],)).fetchone())
                message = str(error) if isinstance(error, StudioError) else 'Workflow stopped; inspect saved sources, chunk IDs and runtime setup before explicitly retrying'
                conn.execute("UPDATE clip_studio_jobs SET status=?,error=? WHERE job_id=? AND lease_token=? AND status='running'", ('uncertain' if uncertain else 'failed', message, row['job_id'], token))
        finally:
            stop.set()
        return True

    def reconcile(self, project_id, actor, request_id, *, resume=False):
        """Human reconciliation: resume only saved IDs, or acknowledge checked outcomes."""
        from .media import StudioError
        project=self.get(project_id,actor)
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            action=encoded({'resume':resume})
            prior=conn.execute('SELECT action_json,response_json FROM clip_studio_actions WHERE project_id=? AND request_id=?',(project_id,str(request_id))).fetchone()
            if prior:
                if prior['action_json']!=action:raise StudioError('This request ID was already used for another reconciliation action')
                return json.loads(prior['response_json'])
            if project['mode']!='real':raise StudioError('Reconciliation applies only to real generation')
            if conn.execute("SELECT 1 FROM clip_studio_jobs WHERE project_id=? AND status IN ('queued','running')",(project_id,)).fetchone():
                raise StudioError('Wait for the active project job before reconciling')
            old=conn.execute('SELECT * FROM clip_studio_jobs WHERE project_id=? AND request_id=?',(project_id,str(request_id))).fetchone()
            if old:return self.storage.job(old)
            uncertain_calls=conn.execute("SELECT 1 FROM clip_studio_calls WHERE project_id=? AND status IN ('submitting','uncertain')",(project_id,)).fetchone()
            targets=[a['attempt_id'] for c in project['clips'] for a in c['attempts'] if a['status'] in ('running','uncertain')]
            if resume:
                if uncertain_calls:raise StudioError('Paid planning has no safe polling contract. Inspect provider usage and use explicit reconciliation first')
                if not targets:raise StudioError('No interrupted generation is available to resume')
                for identifier in targets:
                    chunks=conn.execute("SELECT * FROM clip_studio_chunks WHERE attempt_id=? AND status IN ('submitting','processing')",(identifier,)).fetchall()
                    if not chunks or any(not c['external_id'] for c in chunks):raise StudioError('A generation ID is missing. Check the runtime/provider queue before marking the outcome reconciled')
                    conn.execute("UPDATE clip_studio_render_attempts SET status='queued' WHERE attempt_id=?",(identifier,))
                if conn.execute("SELECT COUNT(*) FROM clip_studio_jobs WHERE status IN ('queued','running')").fetchone()[0]>=20:raise StudioError('The Clip Studio queue is full')
                conn.execute("UPDATE clip_studio_jobs SET status='resumed',error='Explicitly continued using saved generation IDs' WHERE project_id=? AND status IN ('uncertain','interrupted')",(project_id,))
                identifier=str(uuid4())
                payload={'mode':'real','attempt_ids':targets,'action':{'resume':True}}
                conn.execute("INSERT INTO clip_studio_jobs VALUES (?,?,?,'resume','queued','queued',0,?,NULL,NULL,NULL,?)",
                    (identifier,project_id,str(request_id),encoded(payload),int(time.time())))
            else:
                # This action asserts the operator checked billing/runtime and stopped any
                # abandoned generation. It does not submit, delete IDs, or start a retry.
                conn.execute("UPDATE clip_studio_calls SET status='reconciled_manually' WHERE project_id=? AND status IN ('submitting','uncertain')",(project_id,))
                for target in targets:conn.execute("UPDATE clip_studio_render_attempts SET status='cancelled',error='Operator reconciled and abandoned this attempt; saved IDs retained' WHERE attempt_id=?",(target,))
                conn.execute("UPDATE clip_studio_jobs SET status='failed',error='Operator reconciled the outcome. A new explicit generation/retry is required' WHERE project_id=? AND status IN ('uncertain','interrupted')",(project_id,))
                response={'status':'reconciled','project_id':project_id}
                conn.execute('INSERT INTO clip_studio_actions VALUES (?,?,?,?,?)',(project_id,str(request_id),action,encoded(response),int(time.time())))
                return response
            conn.execute('INSERT INTO clip_studio_actions VALUES (?,?,?,?,?)',(project_id,str(request_id),action,encoded({'status':'queued','job_id':identifier}),int(time.time())))
        self.launch(identifier)
        return {'status':'queued','job_id':identifier}
