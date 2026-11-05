"""Verified local model identities; importing this module never imports CUDA.

The distilled checkpoints are explicit variants of the requested versions.
There is no automatic model substitution or network access during generation.
"""
from pathlib import Path
import json
import math
import os

LTX_REPO = 'Lightricks/LTX-2.5'
LTX_REVISION = '49b093a39c19169a8a81721dcc541e07e945232f'
LTX_CODE_REVISION = 'e98f6d927d83c92ef2be6aec76c8440b19306e39'
DIFFUSERS_REVISION = '35087a644038eb531f48581708d4763044cb4852'
WAN_REPO = 'Wan-AI/Wan2.2-Animate-2-14B-Distilled-Diffusers'
WAN_REVISION = '59e4141466bcb1bf9733eca1bc78be6891c9fbdf'
LTX_FILES = {
    'transformer-path': 'diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors',
    'text-encoder-path': 'text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors',
    'video-vae-path': 'vae/ltx-2.5-video-vae-conv-bf16.safetensors',
    'audio-vae-path': 'vae/ltx-2.5-audio-vae-bf16.safetensors',
    'spatial-upsampler-path': 'latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors',
}
MODELS = {
    'ltx-2.5': {'label': 'LTX-2.5 distilled · cinematic video + audio', 'input_modes': ['text', 'image'], 'audio': True,
                'source_url': 'https://github.com/Lightricks/LTX-2', 'license': 'LTX-2.x Community License (gated)'},
    'wan2.2-animate-2': {'label': 'Wan2.2-Animate-2 14B distilled · character + driving video', 'input_modes': ['image'], 'audio': False,
                'source_url': 'https://github.com/Wan-Video/Wan-Animate-2', 'license': 'Apache-2.0'},
    'ltx-wan-hybrid': {'label': 'Hybrid LTX-2.5 + Wan-Animate-2', 'input_modes': ['text', 'image'], 'audio': True,
                'source_url': 'https://github.com/Wan-Video/Wan-Animate-2', 'license': 'Both model licenses apply'},
}


def runtime_root():
    return Path(os.getenv('LOCAL_CINEMATIC_ROOT', 'data/local_cinematic')).resolve()


def worker_python():
    return Path(os.getenv('LOCAL_CINEMATIC_PYTHON', str(runtime_root() / 'venv/bin/python'))).absolute()


def runtime_policy():
    """Server-owned limits for the DGX's shared CPU/GPU memory."""
    try:
        fraction = float(os.getenv('LOCAL_CINEMATIC_GPU_MEMORY_FRACTION', '0.5'))
        reserve = float(os.getenv('LOCAL_CINEMATIC_MIN_AVAILABLE_GB', '16'))
        timeout = int(os.getenv('LOCAL_CINEMATIC_STAGE_TIMEOUT_SECONDS', '1800'))
    except ValueError:
        raise ValueError('Local cinematic memory/runtime limits must be numeric') from None
    if not math.isfinite(fraction) or not .2 <= fraction <= .7:
        raise ValueError('Local cinematic GPU memory fraction must be between 0.2 and 0.7')
    if not math.isfinite(reserve) or not 8 <= reserve <= 64 or not 60 <= timeout <= 7200:
        raise ValueError('Local cinematic limits require 8–64GiB host reserve and 60–7200 seconds per stage')
    return {'gpu_memory_fraction':fraction,'min_available_memory_mb':reserve*1024,'stage_timeout_seconds':timeout}


def catalog_models():
    def ready(key):
        needed = ['ltx'] if key == 'ltx-2.5' else ['wan'] if key == 'wan2.2-animate-2' else ['ltx','wan']
        return worker_python().is_file() and (runtime_root() / 'runtime-verified.json').is_file() and all(checkpoint_ready(name) for name in needed)
    return [{'id': key, **value, 'implemented': True, 'status': 'ready_to_generate' if ready(key) else 'setup_required',
             'executable': ready(key), 'verified_on': '2026-11-03',
             'motion': True, 'clip_duration_seconds': {'min': 3, 'max': 10}, 'fps': [24, 30],
             'native_fps': 24, 'normalizes_fps': True,
             'aspect_ratios': ['16:9', '9:16', '1:1'], 'resolutions': ['720p', '1080p'],
             'native_resolution': ('672x384 / 384x672 / 384x384, 49-frame windows; final output upscaled locally' if key == 'wan2.2-animate-2' else
                 'LTX 896x512 / 512x896; Wan hybrid pass 672x384 / 384x672, 49-frame windows; final output upscaled locally' if key == 'ltx-wan-hybrid' else
                 '896x512 / 512x896 / 512x512; final output upscaled locally'),
             'operations': ['generate'], 'model_audio': value['audio']} for key, value in MODELS.items()]


def checkpoint_ready(name):
    try:
        manifest = json.loads((runtime_root() / f'checkpoint-{name}.json').read_text())
        if manifest['revision'] != (LTX_REVISION if name == 'ltx' else WAN_REVISION) or not manifest['files']:
            return False
        root = runtime_root() / 'models' / name
        return all((root / path).is_file() and (root / path).stat().st_size == size for path,size in manifest['files'].items())
    except (OSError, ValueError, KeyError):
        return False


def validate(config, duration=None):
    if config.video_model not in MODELS:
        raise ValueError('Choose an exact local cinematic model from the catalogue')
    if config.continuity_mode != 'independent' and not config.cast_package_id:
        raise ValueError('Confirm a cast package before using anchored or connected generation')
    if config.video_model == 'wan2.2-animate-2' and config.continuity_mode == 'connected':
        raise ValueError('Wan-only uses approved identity images and driving motion; choose anchored continuity or the LTX + Wan hybrid for connected shots')
    if config.video_input_mode not in MODELS[config.video_model]['input_modes']:
        raise ValueError('Wan-Animate-2 requires a character reference image and a driving motion video')
    if config.video_model == 'wan2.2-animate-2' and config.narration == 'model_audio':
        raise ValueError('Wan-Animate-2 generates silent animation; choose local narration or the hybrid model for LTX audio')
    if config.video_model == 'wan2.2-animate-2' and (config.start_image_reference_id or config.shot_image_reference_ids or config.scene_image_reference_ids):
        raise ValueError('Wan uses its selected character reference and driving video, not LTX scene-start frames')
    if duration is not None and not 3 <= duration <= 10:
        raise ValueError('Local cinematic shots must be 3–10 seconds')


def dimensions(aspect_ratio, model='ltx-2.5'):
    if model == 'wan2.2-animate-2':
        return {'16:9': (672, 384), '9:16': (384, 672), '1:1': (384, 384)}[aspect_ratio]
    return {'16:9': (896, 512), '9:16': (512, 896), '1:1': (512, 512)}[aspect_ratio]


def frame_count(seconds, fps=24):
    # LTX's causal grid is 8k+1. Generate enough frames, then trim locally.
    return ((seconds * fps - 1 + 7) // 8) * 8 + 1


def route(model, shot, continuity_mode='independent'):
    if model == 'wan2.2-animate-2':
        if len(shot['character_ids']) != 1:
            raise ValueError('Wan animation requires exactly one character per shot; use hybrid for establishing or multi-character shots')
        return 'wan'
    if model == 'ltx-wan-hybrid' and continuity_mode == 'connected' and shot.get('transition') == 'continue':
        # Wan reanchors to its identity image and can reset the composition.
        # Continue motion in LTX from the selected Wan/LTX output instead.
        return 'ltx'
    return 'hybrid' if model == 'ltx-wan-hybrid' and len(shot['character_ids']) == 1 else 'ltx'
