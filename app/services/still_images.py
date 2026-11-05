"""Optional isolated SDXL still illustrations; existing image defaults remain unchanged."""
import json
import os
from pathlib import Path
import subprocess
from PIL import Image

from app.services.local_cinematic_models import worker_python,runtime_root,runtime_policy
from app.services.cloud_video_renderer import media_sha256


def root():return Path(os.getenv('STILL_IMAGE_ROOT','data/still_image_runtime')).absolute()


def ready():return (root()/'checkpoint.json').is_file() and worker_python().is_file()


def generate(prompt,output,*,aspect_ratio='1:1',seed=42):
    if not prompt.strip() or len(prompt)>5000:raise ValueError('Enter an illustration prompt up to5000characters')
    if aspect_ratio not in ('1:1','9:16','16:9') or type(seed) is not int or not 0<=seed<=2147483647:raise ValueError('Invalid illustration settings')
    if not ready():raise ValueError('SDXL setup is missing. Run scripts/setup_still_images.py --download-model')
    manifest=json.loads((root()/'checkpoint.json').read_text())
    if manifest.get('repo')!='stabilityai/stable-diffusion-xl-base-1.0' or manifest.get('variant')!='fp16':raise ValueError('Unsupported still-image checkpoint')
    for name,item in manifest['files'].items():
        path=(root()/'model'/name).resolve()
        if not path.is_relative_to((root()/'model').resolve()) or not path.is_file() or path.stat().st_size!=item['size'] or media_sha256(path)!=item['sha256']:raise ValueError('Pinned SDXL checkpoint is missing or changed')
    width,height={'1:1':(1024,1024),'9:16':(768,1344),'16:9':(1344,768)}[aspect_ratio]
    output=Path(output).absolute();output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    task=output.parent/'illustration-request.json';task.write_text(json.dumps({'prompt':prompt,'output':str(output),
        'model':str(root()/'model'),'width':width,'height':height,'seed':seed,'gpu_lock':str(runtime_root()/'gpu.lock'),'policy':runtime_policy()}));task.chmod(0o600)
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','LD_LIBRARY_PATH','CUDA_VISIBLE_DEVICES','CUDA_HOME','TMPDIR') if k in os.environ}
    env.update(HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',TOKENIZERS_PARALLELISM='false')
    try:
        log=output.parent/'illustration-worker.log'
        with log.open('w') as diagnostics:
            log.chmod(0o600)
            subprocess.run([str(worker_python()),str(Path(__file__).absolute().parents[1]/'workers/still_image_worker.py'),str(task)],
                env=env,stdin=subprocess.DEVNULL,stdout=diagnostics,stderr=diagnostics,check=True,timeout=1800)
        with Image.open(output) as image:
            if image.size!=(width,height):raise ValueError('Unexpected illustration dimensions')
            image.verify()
    except (OSError,subprocess.SubprocessError):raise ValueError('Isolated SDXL generation failed or timed out; no legacy image fallback was selected') from None
    finally:task.unlink(missing_ok=True)
    output.chmod(0o600)
    return {'model':manifest['repo'],'revision':manifest['revision'],'license':manifest['license'],
            'license_url':manifest['license_url'],'width':width,'height':height,'seed':seed,'sha256':media_sha256(output)}
