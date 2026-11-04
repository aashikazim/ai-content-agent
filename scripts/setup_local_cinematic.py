"""Explicit isolated runtime installation, checkpoint download and CUDA probe.

Run with the APP Python. No paid APIs, publishing, global installs or application
environment upgrades. Downloading LTX requires the owner's prior HF acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import local_cinematic_models as models


def run(command, *, cwd=None):
    subprocess.run(list(map(str, command)), check=True, cwd=cwd)


def install_headers(root, python):
    result = subprocess.run([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("include"))'],
                            capture_output=True, text=True, check=True)
    if (Path(result.stdout.strip()) / 'Python.h').is_file() or (root / 'sysroot/usr/include/python3.12/Python.h').is_file():
        return
    # DGX OS is Ubuntu ARM64. Extract the matching development package locally;
    # no sudo, apt install, or changes to /usr or the app environment.
    packages = root / 'sysroot/packages'
    packages.mkdir(parents=True, exist_ok=True)
    run(['apt-get', 'download', 'libpython3.12-dev'], cwd=packages)
    for package in packages.glob('libpython3.12-dev_*.deb'):
        run(['dpkg-deb', '--extract', package, root / 'sysroot'])
    if not (root / 'sysroot/usr/include/python3.12/Python.h').is_file():
        raise ValueError('Python development headers are missing; provision matching Python3.12 headers before inference')


def install(root, python):
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('Install uv in your user environment first; the application environment will not be changed')
    if python.parent.parent.resolve() == Path(sys.prefix).resolve():
        raise ValueError('Worker Python must be a separate environment')
    if not python.is_file():
        run([uv, 'venv', '--python', '3.12', python.parent.parent])
    install_headers(root, python)
    overrides = Path(__file__).with_name('local_cinematic_constraints.txt')
    run([uv, 'pip', 'install', '--python', python, '--index-url', 'https://download.pytorch.org/whl/cu132',
         'torch==2.13.0', 'torchvision==0.28.0', '--overrides', overrides])
    run([uv, 'pip', 'install', '--python', python, '--index-url', 'https://download.pytorch.org/whl/test/cu132/',
         'torchaudio==2.11.1', '--no-deps'])
    # Official LTX workaround for CUDA13/cuDNN engine mixing, isolated here.
    run([uv, 'pip', 'install', '--python', python, 'nvidia-cudnn-cu13==9.24.0.43',
         'transformers==5.14.1', 'accelerate==1.15.0', 'huggingface-hub>=1.5,<2',
         'safetensors==0.8.0', 'sentencepiece', 'av', 'imageio', 'imageio-ffmpeg', 'einops', 'peft', 'matplotlib', '--overrides', overrides])
    # Exact audited source revisions; never run a moving branch on startup.
    source = root / 'sources/LTX-2'
    if not (source / '.git').is_dir():
        source.parent.mkdir(parents=True, exist_ok=True)
        run(['git', 'clone', '--filter=blob:none', '--no-checkout', 'https://github.com/Lightricks/LTX-2.git', source])
    run(['git', '-C', source, 'checkout', '--detach', models.LTX_CODE_REVISION])
    run([uv, 'pip', 'install', '--python', python,
         source / 'packages/ltx-core', source / 'packages/ltx-pipelines',
         f'git+https://github.com/huggingface/diffusers.git@{models.DIFFUSERS_REVISION}',
         '--overrides', overrides])


def download(root, model):
    from huggingface_hub import snapshot_download
    repo, revision = (models.LTX_REPO, models.LTX_REVISION) if model == 'ltx' else (models.WAN_REPO, models.WAN_REVISION)
    if shutil.disk_usage(root).free < 110 * 1024 ** 3:
        raise ValueError('Keep at least 110GiB free before checkpoint downloads')
    options = {'allow_patterns': list(models.LTX_FILES.values()) + ['*.json', 'LICENSE*']} if model == 'ltx' else {}
    # The Hub CLI's private credential store handles access; no token argument,
    # raw HTTP response or credential is printed or copied into worker state.
    destination = root / 'models' / model
    snapshot_download(repo, revision=revision, local_dir=destination, **options)
    files = {str(path.relative_to(destination)): path.stat().st_size for path in destination.rglob('*')
             if path.is_file() and '.cache' not in path.parts}
    (root / f'checkpoint-{model}.json').write_text(json.dumps({'repo':repo,'revision':revision,'files':files}))


def diagnose(root, python):
    (root / 'runtime-verified.json').unlink(missing_ok=True)
    code = '''import json,sys,importlib.util,torch,transformers,importlib.metadata as m
from ltx_pipelines.distilled import DistilledPipeline
from diffusers import WanAnimate2Transformer3DModel
from diffusers.modular_pipelines import WanAnimate2DistilledBlocks
assert torch.cuda.is_available(), 'CUDA unavailable'
torch.cuda.set_per_process_memory_fraction(0.5)
x=torch.ones(8,device='cuda'); assert x.sum().item()==8
q=torch.ones((1,1,16,64),device='cuda',dtype=torch.bfloat16)
assert torch.nn.functional.scaled_dot_product_attention(q,q,q).isfinite().all()
assert (q @ q.transpose(-1,-2)).isfinite().all()
assert torch.fft.rfft(torch.ones(1024,device='cuda')).isfinite().all()
assert torch.bmm(torch.ones((1,32,1),device='cuda'),torch.ones((1,1,128),device='cuda')).isfinite().all()
import torchaudio
assert torchaudio.functional.resample(torch.ones(1024,device='cuda'),24000,16000).isfinite().all()
from torch.nn.attention.flex_attention import create_block_mask
spec=importlib.util.spec_from_file_location('cinematic_worker_probe',sys.argv[1])
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
worker.compile_wan_attention()
from diffusers.models.attention_dispatch import dispatch_attention_fn,AttentionBackendName
mask=create_block_mask(lambda b,h,q,k:q>=k,B=1,H=1,Q_LEN=128,KV_LEN=128,device='cuda')
query=torch.randn((1,2,128,64),device='cuda',dtype=torch.bfloat16)
packed=query.permute(0,2,1,3)
actual=dispatch_attention_fn(packed,packed,packed,attn_mask=mask,backend=AttentionBackendName.FLEX)
expected=torch.nn.functional.scaled_dot_product_attention(query,query,query,is_causal=True)
assert actual.isfinite().all() and torch.allclose(actual.permute(0,2,1,3),expected,atol=.02,rtol=.02)
WanAnimate2DistilledBlocks().init_pipeline()
print(json.dumps({'torch':torch.__version__,'cuda':torch.version.cuda,
 'transformers':transformers.__version__,'diffusers':m.version('diffusers'),
 'gpu':torch.cuda.get_device_name(0),'capability':torch.cuda.get_device_capability(0),
 'compiled_flex_attention':True}))
'''
    environment = os.environ.copy()
    includes = root / 'sysroot/usr/include'
    environment['CPATH'] = os.pathsep.join(map(str, [includes/'python3.12', includes, includes/'aarch64-linux-gnu/python3.12']))
    environment['TRITON_CACHE_DIR'] = str(root / 'cache/triton')
    environment['TORCHINDUCTOR_CACHE_DIR'] = str(root / 'cache/torchinductor')
    environment['CUDA_CACHE_PATH'] = str(root / 'cache/cuda')
    environment.update(TORCHINDUCTOR_COMPILE_THREADS='1', MAX_JOBS='1', TORCHDYNAMO_SUPPRESS_ERRORS='0')
    worker = Path(__file__).resolve().parents[1] / 'app/workers/local_cinematic_worker.py'
    result = subprocess.run([str(python), '-c', code, str(worker)], capture_output=True, text=True, timeout=180, env=environment)
    if result.returncode:
        diagnostic = root / 'setup-diagnostic.txt'
        diagnostic.write_text(result.stderr)
        diagnostic.chmod(0o600)
        raise ValueError('Runtime diagnostic failed; inspect private data/local_cinematic/setup-diagnostic.txt. The app environment is unchanged.')
    metadata = json.loads(result.stdout.strip().splitlines()[-1])
    metadata.update(ltx_code_revision=models.LTX_CODE_REVISION, diffusers_revision=models.DIFFUSERS_REVISION)
    (root / 'runtime-verified.json').write_text(json.dumps(metadata))
    (root / 'setup-diagnostic.txt').unlink(missing_ok=True)
    print(json.dumps(metadata, indent=2))


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--install-headers', action='store_true', help='Extract matching Ubuntu Python development headers locally for Triton JIT')
    parser.add_argument('--download-ltx', action='store_true')
    parser.add_argument('--accept-ltx-license', action='store_true', help='Owner has reviewed/accepted the LTX license and HF access terms')
    parser.add_argument('--download-wan', action='store_true')
    parser.add_argument('--diagnose', action='store_true')
    args = parser.parse_args()
    root, python = models.runtime_root(), models.worker_python()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if args.download_ltx and not args.accept_ltx_license:
            raise ValueError('Review/accept the LTX model terms on Hugging Face first, then explicitly pass --accept-ltx-license')
        if args.install:
            install(root, python)
        if args.install_headers:
            install_headers(root, python)
        if args.download_ltx:
            download(root, 'ltx')
        if args.download_wan:
            download(root, 'wan')
        if args.diagnose:
            diagnose(root, python)
        if not any((args.install, args.install_headers, args.download_ltx, args.download_wan, args.diagnose)):
            parser.print_help()
    except Exception as error:
        # Hub exceptions sometimes contain signed URLs. Keep their bodies out of
        # terminal output; credential failures have an explicit local remedy.
        print(str(error) if isinstance(error, ValueError) else
              'Setup step failed. Check network, official wheel availability and HF model access; no automatic model substitution.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
