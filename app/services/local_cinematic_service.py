"""Process boundary to an isolated local GPU environment; no GPU imports here."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from uuid import UUID, uuid4

from app.services.gemini_video_service import VideoProviderError
from app.services import local_cinematic_models as models


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as file:
        os.chmod(temporary, 0o600)
        json.dump(value, file)
    temporary.replace(path)


class LocalCinematicService:
    def __init__(self, config):
        self.config = config
        self.root = models.runtime_root()

    def check_model(self):
        models.validate(self.config)
        try:
            models.runtime_policy()
        except ValueError as error:
            raise VideoProviderError(str(error), uncertain=False) from None
        missing = []
        if not models.worker_python().is_file():
            missing.append('isolated worker Python')
        if self.config.video_model != 'wan2.2-animate-2':
            missing.extend('LTX ' + key for key, name in models.LTX_FILES.items() if not (self.root / 'models/ltx' / name).is_file())
        if self.config.video_model != 'ltx-2.5' and not (self.root / 'models/wan/model_index.json').is_file():
            missing.append('Wan-Animate-2 distilled checkpoint')
        if not (self.root / 'runtime-verified.json').is_file():
            missing.append('successful runtime diagnostic')
        for name in (['ltx'] if self.config.video_model == 'ltx-2.5' else ['wan'] if self.config.video_model == 'wan2.2-animate-2' else ['ltx','wan']):
            try:
                manifest = json.loads((self.root / f'checkpoint-{name}.json').read_text())
                expected = models.LTX_REVISION if name == 'ltx' else models.WAN_REVISION
                base = self.root / 'models' / name
                if manifest['revision'] != expected or not manifest['files'] or any(
                    not (base / path).is_file() or (base / path).stat().st_size != size for path,size in manifest['files'].items()):
                    missing.append(name + ' complete pinned weights')
            except (OSError, ValueError, KeyError):
                missing.append(name + ' checkpoint download manifest')
        if missing:
            raise VideoProviderError('Local cinematic setup incomplete: ' + ', '.join(missing) + '. Run scripts/setup_local_cinematic.py; no generation or fallback was started.', uncertain=False)
        return {'provider': 'local_cinematic', 'status': 'configured', 'available_credits': None}

    def submit(self, prompt, *, aspect_ratio, references=(), previous_id=None, operation='generate', local_inputs=None, **kwargs):
        if operation != 'generate' or previous_id:
            raise VideoProviderError('Local models currently support individual shot regeneration; cloud edit/extension remains available separately.', uncertain=False)
        self.check_model()
        identifier = str(uuid4())
        directory = self.root / 'tasks' / identifier
        directory.mkdir(parents=True, mode=0o700)
        request = {'model': self.config.video_model, 'prompt': prompt, 'aspect_ratio': aspect_ratio,
                   'seed': self.config.local_seed + local_inputs['shot_number'], 'root': str(self.root),
                   'created_at': time.time(), **local_inputs, 'runtime_policy':models.runtime_policy()}
        wan_width,wan_height=models.dimensions(aspect_ratio,'wan2.2-animate-2')
        request['wan_profile']={'width':wan_width,'height':wan_height,'segment_frame_length':49}
        if request.get('route')=='wan':
            request.update(width=wan_width,height=wan_height)
        private_json(directory / 'request.json', request)
        private_json(directory / 'state.json', {'status': 'in_progress', 'stage': 'queued', 'progress': 0, 'heartbeat': time.time()})
        # Never inherit cloud/platform credentials, tokens, or Python search paths.
        environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LD_LIBRARY_PATH', 'CUDA_VISIBLE_DEVICES', 'CUDA_HOME', 'TMPDIR') if key in os.environ}
        environment.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1', TOKENIZERS_PARALLELISM='false')
        includes = self.root / 'sysroot/usr/include'
        environment['CPATH'] = os.pathsep.join(map(str, [includes/'python3.12', includes, includes/'aarch64-linux-gnu/python3.12']))
        environment.update(TRITON_CACHE_DIR=str(self.root/'cache/triton'),
                           TORCHINDUCTOR_CACHE_DIR=str(self.root/'cache/torchinductor'),
                           CUDA_CACHE_PATH=str(self.root/'cache/cuda'), HF_HOME=str(self.root/'cache/huggingface'))
        environment.update(TORCHINDUCTOR_COMPILE_THREADS='1', MAX_JOBS='1', TORCHDYNAMO_SUPPRESS_ERRORS='0')
        worker = Path(__file__).resolve().parents[1] / 'workers/local_cinematic_worker.py'
        process = None
        try:
            process = subprocess.Popen([str(models.worker_python()), str(worker), str(directory)],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True)
            threading.Thread(target=process.wait, daemon=True).start()
            private_json(directory / 'process.json', {'pid': process.pid, 'created_at': time.time()})
        except OSError:
            if process is None:
                raise VideoProviderError('Could not start isolated local GPU worker; inspect runtime setup. No automatic restart.', uncertain=False) from None
            # PID metadata is supplementary. Always return the known task UUID
            # after launch so the caller can persist it even if this write fails.
        return {'provider':'local_cinematic','interaction_id':identifier,'status':'in_progress','usage':{'stage':'queued','progress':0}}

    def retrieve(self, identifier):
        try:
            UUID(identifier)
            state = json.loads((self.root / 'tasks' / identifier / 'state.json').read_text())
        except (ValueError, OSError):
            raise VideoProviderError('Local task state is unavailable; inspect its saved generation ID before starting a new candidate.', uncertain=True) from None
        if state['status'] == 'in_progress' and time.time() - state.get('heartbeat', 0) > 120:
            raise VideoProviderError('Local GPU worker stopped reporting progress; inspect it before manually regenerating. No worker was restarted.', uncertain=True)
        usage = {key: state[key] for key in ('stage', 'progress', 'elapsed_seconds', 'peak_gpu_memory_mb', 'peak_rss_mb', 'queue_wait_seconds', 'ltx_seconds', 'wan_seconds') if key in state}
        return {'provider': 'local_cinematic', 'interaction_id': identifier, 'status': state['status'],
                'usage': usage, 'provider_error': state.get('error')}

    def download(self, snapshot, destination):
        identifier = str(UUID(snapshot['interaction_id']))
        source = self.root / 'tasks' / identifier / 'output.mp4'
        if not source.is_file() or source.stat().st_size > 100 * 1024 * 1024:
            raise VideoProviderError('Local worker output is missing or exceeds the 100MB shot limit; inspect the task without regenerating.', uncertain=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.part')
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
