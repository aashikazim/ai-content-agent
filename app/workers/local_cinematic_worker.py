"""Standalone GPU worker, executed ONLY by the separate cinematic Python.

No application imports: CUDA libraries never enter FastAPI/Streamlit. Tasks are
immutable; one system file lock serializes local GPU work across API restarts.
"""
import fcntl
import faulthandler
import gc
import hashlib
import json
import os
from pathlib import Path
import runpy
import resource
import signal
import subprocess
import sys
import threading
import time


def write_state(directory, state):
    temporary = directory / 'state.tmp'
    with temporary.open('w') as file:
        os.chmod(temporary, 0o600)
        json.dump({**state, 'heartbeat': time.time()}, file)
    temporary.replace(directory / 'state.json')


def ffmpeg(*arguments):
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', *map(str, arguments)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)


def verify_file(path, expected):
    with Path(path).open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != expected:
            raise ValueError('Reference changed')


def ltx_arguments(request, output):
    # The controller supplies only verified local model paths and registry paths.
    result = ['ltx_pipelines.distilled']
    for key, value in request['ltx_files'].items():
        result.extend(['--' + key, value])
    frames = ((request['duration'] * 24 - 1 + 7) // 8) * 8 + 1
    result.extend(['--num-frames', str(frames), '--height', str(request['height']), '--width', str(request['width']),
                   '--frame-rate', '24', '--offload', 'cpu', '--seed', str(request['seed']), '--output-path', str(output), '--prompt', request['prompt']])
    if request.get('start_image'):
        result.extend(['--image', request['start_image'], '0', '1.0'])
    return result


def ltx(request, output):
    previous = sys.argv
    try:
        sys.argv = ltx_arguments(request, output)
        runpy.run_module('ltx_pipelines.distilled', run_name='__main__')
    finally:
        sys.argv = previous
        gc.collect()
        import torch
        torch.cuda.empty_cache()


def wan(request, driving, output):
    import torch
    from PIL import Image
    from transformers import AutoTokenizer, CLIPVisionModel, UMT5EncoderModel
    from diffusers import AutoencoderKLWan, FlowMatchEulerDiscreteScheduler, WanAnimate2Transformer3DModel, ComponentsManager
    from diffusers.modular_pipelines import WanAnimate2DistilledBlocks
    from diffusers.utils import export_to_video, load_video, logging

    logging.set_verbosity_info()
    logging.enable_explicit_format()
    compile_wan_attention()

    root = Path(request['root']) / 'models/wan'
    options = {'local_files_only': True, 'torch_dtype': torch.bfloat16}
    # Explicit local subfolders avoid upstream index references to Hub PRs and
    # remote model code. All components belong to the pinned distilled model.
    manager = ComponentsManager()
    pipeline = WanAnimate2DistilledBlocks().init_pipeline(components_manager=manager)
    pipeline.update_components(
        text_encoder=UMT5EncoderModel.from_pretrained(root / 'text_encoder', **options),
        tokenizer=AutoTokenizer.from_pretrained(root / 'tokenizer', local_files_only=True),
        image_encoder=CLIPVisionModel.from_pretrained(root / 'image_encoder', **options),
        transformer=WanAnimate2Transformer3DModel.from_pretrained(root / 'transformer', **options),
        vae=AutoencoderKLWan.from_pretrained(root / 'vae', **(options | {'torch_dtype':torch.float32})),
        scheduler=FlowMatchEulerDiscreteScheduler.from_pretrained(root / 'scheduler', local_files_only=True),
    )
    # GB10 shares host/device RAM. Keep the two repeatedly alternating models
    # resident; moving the entire transformer for every VAE call is expensive.
    # Encoders are offloaded after conditioning. Allocator/host limits still apply.
    manager.enable_auto_cpu_offload(device='cuda',
        offload_strategy=offload_except(pipeline.transformer,pipeline.vae))
    pipeline.vae.enable_tiling()
    image = Image.open(request['character_image']).convert('RGB')
    # Decoding is bounded by the normalized 3–10-second input, never a URL.
    frames = load_video(str(driving))
    profile = request.get('wan_profile', {'width':384,'height':672,'segment_frame_length':49})
    videos = pipeline(prompt=request['character_prompt'], prompt_ref=request['prompt'], image=image,
                      driving_video=frames, driving_video_fps=24, fps=24,
                      width=profile['width'], height=profile['height'], segment_frame_length=profile['segment_frame_length'], num_inference_steps=10,
                      generator=torch.Generator(device='cuda').manual_seed(request['seed']), output='videos')
    export_to_video(videos[0], str(output), fps=24)
    del pipeline
    gc.collect()
    torch.cuda.empty_cache()


def compile_wan_attention(torch_module=None, dispatch=None):
    """Fuse only flex attention; keep dynamic rotary/cache preparation eager.

    Full-block compilation fails on this pinned model's tensor-dependent grid
    sizes. A process-local proxy changes only Diffusers' flex module reference;
    PyTorch's module and installed dependency files remain untouched.
    """
    if torch_module is None:
        import torch as torch_module
    if dispatch is None:
        from diffusers.models import attention_dispatch as dispatch
    from types import SimpleNamespace
    backend = dispatch.flex_attention
    if backend is None or not callable(getattr(backend, 'flex_attention', None)):
        raise RuntimeError('Pinned Diffusers flex backend is unavailable')
    if getattr(backend, '_cinematic_compiled', False):
        return backend.flex_attention
    compiled = torch_module.compile(backend.flex_attention, fullgraph=True)
    dispatch.flex_attention = SimpleNamespace(**(vars(backend) | {
        'flex_attention':compiled, '_cinematic_compiled':True}))
    return compiled


def offload_except(*resident_models):
    """Pin hot components by identity, independently of registry UUID names."""
    protected={id(model) for model in resident_models}
    def select(hooks, **unused):
        return [hook for hook in hooks if id(hook.model) not in protected]
    return select


class LocalMemoryError(RuntimeError):
    pass


class LocalTimeoutError(RuntimeError):
    pass


def policy_for(request):
    return request.get('runtime_policy', {'gpu_memory_fraction':.5,
        'min_available_memory_mb':16384, 'stage_timeout_seconds':1800})


def available_memory_mb():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) / 1024
    raise LocalMemoryError('Host available memory could not be measured')


def stop_child(child):
    """Terminate only this stage's own process group, retaining the GPU lock."""
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=5)
    except ProcessLookupError:
        child.wait(timeout=5)


def child_peak_rss_mb(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmHWM:'):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    return 0


def model_stage(stage, directory):
    """Each neural stage exits completely before another model loads."""
    request = json.loads((directory / 'request.json').read_text())
    started = time.monotonic()
    result = {'status': 'failed'}
    # Private, periodic stacks identify slow upstream blocks after a restart.
    # They neither change inference nor terminate the model process.
    faulthandler.dump_traceback_later(180, repeat=True)
    try:
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable')
        torch.cuda.set_per_process_memory_fraction(policy_for(request)['gpu_memory_fraction'])
        torch.cuda.reset_peak_memory_stats()
        if stage == 'ltx':
            ltx(request, directory / 'ltx.mp4')
        else:
            wan(request, directory / 'driving.mp4', directory / 'wan.mp4')
        result.update(status='completed', peak_gpu_memory_mb=round(torch.cuda.max_memory_allocated() / 1024 ** 2, 2))
    except (Exception, SystemExit) as error:
        import traceback
        traceback.print_exc()
        result['error'] = 'out_of_memory' if isinstance(error, MemoryError) or type(error).__name__ == 'OutOfMemoryError' else 'inference_failed'
    finally:
        faulthandler.cancel_dump_traceback_later()
        try:
            result['peak_gpu_memory_mb'] = round(torch.cuda.max_memory_allocated() / 1024 ** 2, 2)
        except (NameError, RuntimeError):
            pass
        result.update(elapsed_seconds=round(time.monotonic() - started, 2),
                      peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2))
        with (directory / (stage + '-metrics.json')).open('w') as file:
            os.chmod(file.name, 0o600)
            json.dump(result, file)
    return 0 if result['status'] == 'completed' else 1


def run_model(stage, directory, state, lock_fd):
    # Inheriting the flock descriptor keeps the GPU reservation even if the
    # controller crashes while its neural child is still executing.
    request_path = directory / 'request.json'
    policy = policy_for(json.loads(request_path.read_text()) if request_path.exists() else {})
    if available_memory_mb() < policy['min_available_memory_mb']:
        raise LocalMemoryError('Host memory reserve is unavailable; no neural stage was started')
    started = time.monotonic()
    with (directory / (stage + '-diagnostic.txt')).open('w') as log:
        os.chmod(log.name, 0o600)
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--model-stage', stage, str(directory)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, pass_fds=(lock_fd,), start_new_session=True)
        try:
            metadata = directory / (stage + '-process.json')
            temporary = metadata.with_suffix('.tmp')
            with temporary.open('w') as file:
                os.chmod(file.name, 0o600)
                json.dump({'pid':child.pid,'created_at':time.time(),'stage':stage},file)
            temporary.replace(metadata)
            while child.poll() is None:
                state['peak_rss_mb'] = max(state.get('peak_rss_mb',0), child_peak_rss_mb(child.pid))
                if available_memory_mb() < policy['min_available_memory_mb']:
                    raise LocalMemoryError('Host memory reserve reached; stopped the neural stage')
                if time.monotonic() - started > policy['stage_timeout_seconds']:
                    raise LocalTimeoutError('Neural stage exceeded its runtime limit')
                time.sleep(.5)
            returncode = child.returncode
        finally:
            stop_child(child)
    metrics_path = directory / (stage + '-metrics.json')
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
    for key in ('peak_gpu_memory_mb', 'peak_rss_mb'):
        if key in metrics:
            state[key] = max(state.get(key, 0), metrics[key])
    state[stage + '_seconds'] = metrics.get('elapsed_seconds', round(time.monotonic()-started,2))
    if returncode or metrics.get('status') != 'completed':
        if metrics.get('error') == 'out_of_memory':
            raise LocalMemoryError('GPU memory exhausted')
        raise RuntimeError('Local neural stage failed')


def generate(request, directory, state, lock_fd):
    for reference in request.get('reference_files', []):
        verify_file(reference['path'], reference['sha256'])
    route = request['route']
    source = directory / 'ltx.mp4'
    if route in {'ltx', 'hybrid'}:
        state.update(stage='ltx_generation', progress=.15)
        run_model('ltx', directory, state, lock_fd)
    if route in {'wan', 'hybrid'}:
        state.update(stage='wan_animation', progress=.55 if route == 'hybrid' else .15)
        driving = directory / 'driving.mp4'
        ffmpeg('-i', source if route == 'hybrid' else request['driving_video'], '-t', request['duration'],
               '-vf', f"fps=24,scale={request['width']}:{request['height']}:force_original_aspect_ratio=decrease,pad={request['width']}:{request['height']}:(ow-iw)/2:(oh-ih)/2",
               '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', driving)
        source = directory / 'wan.mp4'
        run_model('wan', directory, state, lock_fd)
    state.update(stage='normalizing', progress=.9)
    arguments = ['-i', source]
    if route == 'hybrid':
        arguments.extend(['-i', directory / 'ltx.mp4', '-map', '0:v:0', '-map', '1:a:0'])
    arguments.extend(['-t', request['duration'], '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', directory / 'output.mp4'])
    ffmpeg(*arguments)


def main(directory):
    directory = Path(directory).resolve()
    request = json.loads((directory / 'request.json').read_text())
    started = time.monotonic()
    state = {'status': 'in_progress', 'stage': 'waiting_for_gpu', 'progress': 0}
    stop = threading.Event()

    def heartbeat():
        while not stop.is_set():
            state['elapsed_seconds'] = round(time.monotonic() - started, 2)
            state['peak_rss_mb'] = max(state.get('peak_rss_mb', 0), round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2))
            write_state(directory, state.copy())
            stop.wait(3)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with (Path(request['root']) / 'gpu.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state['queue_wait_seconds'] = round(time.monotonic() - started, 2)
            state.update(stage='loading', progress=.05)
            generate(request, directory, state, lock.fileno())
        state.update(status='completed', stage='completed', progress=1)
    except Exception as error:
        # A private traceback helps local setup debugging; public state never
        # contains prompts, paths, arbitrary provider messages or credentials.
        import traceback
        with (directory / 'diagnostic.txt').open('w') as file:
            os.chmod(directory / 'diagnostic.txt', 0o600)
            traceback.print_exc(file=file)
        oom = isinstance(error, (LocalMemoryError, MemoryError))
        state.update(status='failed', stage='failed', error=(
            'Local CPU/GPU memory safety limit reached. Inspect this task and available memory before manually regenerating.' if oom else
            'Local model stage reached its runtime limit and was stopped. Inspect this task; no automatic retry was started.' if isinstance(error, LocalTimeoutError) else
            'Local model inference failed. Inspect the private task diagnostic and runtime setup; no fallback or retry was started.'))
    finally:
        stop.set()
        thread.join(timeout=5)
        state['elapsed_seconds'] = round(time.monotonic() - started, 2)
        write_state(directory, state)


if __name__ == '__main__':
    if sys.argv[1] == '--model-stage':
        raise SystemExit(model_stage(sys.argv[2], Path(sys.argv[3])))
    main(sys.argv[1])
