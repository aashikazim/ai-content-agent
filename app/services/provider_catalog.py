"""An explicit catalogue, not a claim that credentials or models work.

This module performs no network requests, downloads, or GPU imports. Provider
documentation verifies model identifiers; account access remains unverified.
"""

import os

from app.models.generation_config import DEFAULT_IMAGE_MODEL, GenerationConfig
from app.services.runway_models import EDIT_MODELS, catalog_models, validate as validate_runway
from app.services import local_cinematic_models
from app.services.anthropic_script_service import ANTHROPIC_MODEL


VERIFIED_ON = "2026-11-03"
OPENAI_MODEL = "gpt-6.1-sol"
GOOGLE_MODEL = "gemini-omni-1.1-flash"
KLING_MODEL = "kling-4.0"

CONTENT_TYPES = [
    {"id": "realistic_short", "label": "Realistic short", "status": "local_preview",
     "phase": 1, "description": "Local text cards or still images with narration; no moving actors.",
     "executable": True},
    {"id": "short_drama", "label": "Short drama", "status": "gemini_video", "cloud_executable": True, "phase": 4,
     "description": "Screenplay, character references and Gemini moving shots; manual continuity checks required.",
     "executable": False},
    {"id": "animated_comedy", "label": "Animated comedy", "status": "gemini_video", "cloud_executable": True, "phase": 4,
     "description": "Fiction/satire character designs and Gemini animated shots with human continuity checks.", "executable": False},
    {"id": "long_drama", "label": "Long drama", "status": "runway_video", "phase": 7, "cloud_executable": True,
     "description": "Fictional screenplay: 2–30 minutes / 600 shots locally, 2–20 minutes / 240 shots with Runway; shared cast and human continuity inspection required.", "executable": False},
    {"id": "news", "label": "News", "status": "unverified_gemini_illustration", "cloud_executable": True, "phase": 6,
     "description": "Unverified news drafts and AI illustrative footage; source verification remains a human responsibility.",
     "executable": False},
]


def _local_script_model():
    return os.getenv("OLLAMA_MODEL", "qwen3:8b")


def _local_image_model():
    return os.getenv("IMAGE_MODEL_ID", DEFAULT_IMAGE_MODEL)


def _cloud_provider(provider_id, label, env_name, phase, model):
    configured = bool(os.getenv(env_name))
    credentials = "Credential configured; account access unverified." if configured else "Credential not configured."
    return {
        "id": provider_id, "label": label, "configured": configured,
        "implemented": False, "status": "planned", "phase": phase,
        "account_access": "unverified", "executable": False,
        "reason": f"{credentials} Adapter planned for Phase {phase}; execution is disabled.",
        "models": [model],
    }


def get_catalog() -> dict:
    """Return public metadata and credential-presence flags, never secrets."""
    openai = {
        "id": OPENAI_MODEL, "label": "GPT-6.1 Sol", "status": "account_access_unverified",
        "implemented": True, "verified_on": VERIFIED_ON,
        "source_url": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        "structured_output": True, "account_access": "unverified",
    }
    google = {
        "id": GOOGLE_MODEL, "label": "Gemini Omni 1.1 Flash", "status": "planned",
        "implemented": False, "verified_on": VERIFIED_ON,
        "source_url": "https://aistudio.google.com/docs/models/gemini-omni-flash",
        "source_urls": ["https://aistudio.google.com/docs/models/gemini-omni-flash",
                        "https://aistudio.google.com/docs/omni"],
        "clip_duration_seconds": {"min": 3, "max": 10},
        "aspect_ratios": ["9:16", "16:9"], "resolutions": ["720p", "1080p"],
        "fps": [24], "audio": True, "dialogue_languages": ["English"],
        "resolution_note": "Higher resolutions are upscaled; this application exposes 720p and 1080p.",
        "account_access": "unverified", "character_references": "planned",
    }
    kling = {
        "id": KLING_MODEL, "label": "Kling 4.0", "status": "planned",
        "implemented": False, "verified_on": VERIFIED_ON,
        "source_url": "https://kling.ai/document-api/api/video/4-0/text-to-video",
        "source_urls": ["https://kling.ai/document-api/api/video/4-0/text-to-video",
                        "https://kling.ai/document-api/updates/api?date=2026-02-11"],
        "clip_duration_seconds": {"min": 3, "max": 30},
        "aspect_ratios": ["9:16", "16:9", "1:1"],
        "resolutions": ["720p", "1080p"], "audio": True,
        "character_references": "schema_unverified", "account_access": "unverified",
    }
    return {
        "schema_version": 1, "phase": 6,
        "runway_edit_models": [{'id': key, **value} for key,value in EDIT_MODELS.items()],
        "content_types": [dict(item) for item in CONTENT_TYPES],
        "script_providers": [
            {"id": "ollama", "label": "Local Ollama", "configured": True,
             "implemented": True, "status": "model_access_unverified", "executable": True,
             "account_access": "unverified",
             "reason": "Local adapter implemented; Ollama service and model availability have not been checked.",
             "models": [{"id": _local_script_model(), "label": _local_script_model(),
                         "status": "model_access_unverified", "implemented": True,
                         "verified_on": None, "source_url": None}]},
            {**_cloud_provider("openai", "OpenAI", "OPENAI_API_KEY", 2, openai),
             "implemented": True, "executable": bool(os.getenv("OPENAI_API_KEY")),
             "status": "account_access_unverified" if os.getenv("OPENAI_API_KEY") else "not_configured",
             "reason": "Script adapter implemented. A configured API key, account model check and single-use cost confirmation are required. No automatic fallback or paid retries.",
             "models": [openai, {**openai, "id": "gpt-6-sol", "label": "GPT-6 Sol (explicit alternative)",
                                  "source_url": "https://developers.openai.com/api/docs/models/gpt-6-sol"}]},
            {"id": "anthropic", "label": "Claude (Anthropic)",
             "configured": bool(os.getenv('ANTHROPIC_API_KEY')), "implemented": True,
             "executable": bool(os.getenv('ANTHROPIC_API_KEY')), "account_access": "unverified",
             "status": "account_access_unverified" if os.getenv('ANTHROPIC_API_KEY') else "not_configured",
             "reason": "Claude screenplay and shot planning. Server-side ANTHROPIC_API_KEY and explicit script cost confirmation required. No automatic retries or fallback.",
             "models": [{"id": ANTHROPIC_MODEL, "label": "Claude Sonnet 5.5", "implemented": True,
                         "structured_output": True, "verified_on": "2026-11-04",
                         "source_url": "https://platform.claude.com/docs/en/models/sonnet-5-5/overview"}]},
        ],
        "video_providers": [
            {"id": "local", "label": "Local illustrated preview", "configured": True,
             "implemented": True, "status": "runtime_unverified", "executable": True,
             "account_access": "unverified",
             "reason": "Still-image/text-card adapter implemented; FFmpeg, TTS and GPU/model access have not been checked.",
             "models": [{"id": "local-pipeline", "label": "Local still-image pipeline",
                         "status": "runtime_unverified", "implemented": True,
                         "verified_on": None, "source_url": None,
                         "aspect_ratios": ["9:16", "16:9", "1:1"],
                         "resolutions": ["720p", "1080p"], "fps": [24, 30],
                         "duration_seconds": {"min": 30, "max": 60},
                         "motion": False, "model_audio": False}]},
            {**_cloud_provider("google", "Google Gemini", "GEMINI_API_KEY", 3, google),
             "implemented": True, "executable": bool(os.getenv("GEMINI_API_KEY")),
             "status": "account_access_unverified" if os.getenv("GEMINI_API_KEY") else "not_configured",
             "reason": "Gemini native 720p footage with audio, reference images and explicit paid authorization. 1080p is upscaled locally. Background/account access is unverified.",
             "models": [{**google, "implemented": True, "status": "account_access_unverified", "character_references": "supported", "native_resolution": "720p"}]},
            {"id": "runway", "label": "Runway Dev API", "configured": bool(os.getenv('RUNWAYML_API_SECRET')),
             "implemented": True, "executable": bool(os.getenv('RUNWAYML_API_SECRET')),
             "account_access": "unverified", "status": "account_access_unverified",
             "reason": "Official Runway SDK; model-specific text/image inputs, explicit credit confirmation and no paid retries. Native resolution varies; FFmpeg assembles locally.",
             "models": catalog_models()},
            {"id": "local_cinematic", "label": "Local cinematic · LTX / Wan / hybrid",
             "production_duration_seconds": {"min":30,"max":1800}, "max_shots":600,
             "continuity_modes": ["independent","anchored","connected"],
             "configured": any(item['executable'] for item in local_cinematic_models.catalog_models()), "implemented": True,
             "executable": any(item['executable'] for item in local_cinematic_models.catalog_models()), "status": "runtime_check_required",
             "reason": "Isolated GPU workers; exact LTX-2.5 and Wan-Animate-2 distilled checkpoints. Setup and model access required. LTX uses a512px short edge; Wan uses384px with49-frame windows, locally upscaled. No still-image fallback. Wan needs one reference actor and driving motion; hybrid retains LTX audio.",
             "models": local_cinematic_models.catalog_models()},
        ],
        "image_models": [{"id": _local_image_model(), "label": _local_image_model(),
                          "status": "model_access_unverified", "implemented": True}],
        "styles": [{"id": value, "label": label} for value, label in [
            ("realistic", "Realistic"), ("cinematic", "Cinematic"),
            ("2d_cartoon", "2D cartoon"), ("3d_animation", "3D animation"),
            ("comic_book", "Comic book"), ("caricature", "Caricature"), ("sketch", "Sketch"),
        ]],
        "aspect_ratios": ["9:16", "16:9", "1:1"], "resolutions": ["720p", "1080p"],
        "fps": [24, 30], "defaults": GenerationConfig().model_dump(mode="json"),
        "limits": {"duration_seconds": {"min": 30, "max": 1200},
                   "execution_duration_seconds": {"min": 30, "max": 60},
                   "max_shots": 12, "long_form_max_shots": 240, "long_form_duration_seconds": {"min":120,"max":1200}, "max_character_references": 3,
                   "local_long_form_max_shots":600, "local_long_form_duration_seconds":{"min":120,"max":1800},
                   "max_cost_usd": "1000", "topic_characters": 1000,
                   "script_characters": 20000},
    }


def validate_for_draft(config: GenerationConfig) -> None:
    """Validate known selections without enabling a planned adapter.

    Production duration is distinct from the provider's per-clip duration.
    Long drafts therefore remain valid for future multi-shot assembly.
    """
    config = GenerationConfig.model_validate(config)
    expected_scripts = {'ollama': [_local_script_model()], 'openai': [OPENAI_MODEL, 'gpt-6-sol'],
                        'anthropic': [ANTHROPIC_MODEL]}[config.script_provider]
    if config.script_model not in expected_scripts:
        raise ValueError("Unknown script model for the selected provider; choose a model from the catalogue")
    expected_video = {"local": "local-pipeline", "google": GOOGLE_MODEL, "kling": KLING_MODEL}
    if config.video_provider == 'local_cinematic':
        local_cinematic_models.validate(config)
    elif config.video_provider == 'runway':
        validate_runway(config)
    elif config.video_model != expected_video[config.video_provider]:
        raise ValueError("Unknown video model for the selected provider; choose a model from the catalogue")
    if config.video_provider not in {'runway', 'local_cinematic'} and (config.video_input_mode != 'text' or config.start_image_reference_id or config.shot_image_reference_ids):
        raise ValueError('Start-frame input controls are available for Runway only')
    if config.image_model != _local_image_model():
        raise ValueError("Unknown image model; choose the configured local model from the catalogue")
    if config.video_provider == "google":
        if config.aspect_ratio == "1:1":
            raise ValueError("Gemini Omni does not natively support 1:1; choose 9:16 or 16:9")
        if config.fps != 24:
            raise ValueError("Gemini Omni produces 24 FPS; choose 24 FPS")
    if config.source_mode == "topic" and not config.topic.strip():
        raise ValueError("Enter a topic for topic-based generation")
    if config.source_mode == "script" and not config.supplied_script.strip():
        raise ValueError("Enter a script for supplied-script generation")


def validate_for_script(config: GenerationConfig) -> None:
    """Validate a script-only draft; it does not enable cloud video adapters."""
    config = GenerationConfig.model_validate(config)
    validate_for_draft(config)
    if config.video_provider == "kling":
        raise ValueError("Kling was retired; explicitly choose Google or the local pipeline")
    if config.script_provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not configured; choose local Ollama or configure the key on the server")
    if config.script_provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("ANTHROPIC_API_KEY is not configured; choose local Ollama or configure the key on the server")
    if config.content_type == 'long_drama':
        maximum_duration = 1800 if config.video_provider == 'local_cinematic' else 1200
        if config.video_provider not in {'runway', 'local_cinematic'} or not 120 <= config.duration_seconds <= maximum_duration:
            raise ValueError(f'Long-form drama needs Runway or local cinematic and a 120–{maximum_duration}-second target')
        from math import ceil
        from app.services.runway_models import capabilities
        maximum = 10 if config.video_provider == 'local_cinematic' else capabilities(config)['clip_duration_seconds']['max']
        if config.max_shots < ceil(config.duration_seconds / maximum):
            raise ValueError('Increase Maximum shots so the selected model can cover the full drama duration')
    elif config.duration_seconds > 60:
        raise ValueError("Short-form script generation supports 30–60 seconds; select long drama with Runway for longer stories")
    if config.source_mode != "topic" and config.topic.strip():
        raise ValueError("Remove the unused topic or select topic source mode")
    if config.source_mode != "script" and config.supplied_script.strip():
        raise ValueError("Remove the unused script or select script source mode")


def validate_for_execution(config: GenerationConfig) -> None:
    """Validate a renderable job; paid authorization is also enforced in JobService."""
    config = GenerationConfig.model_validate(config)
    validate_for_script(config)
    if config.video_provider in {"google", "runway", "local_cinematic"}:
        validate_for_cloud_video(config)
        return
    if config.content_type != "realistic_short":
        raise ValueError("Video production for this content type is planned; use the script workshop or the realistic-short local preview")
    if config.require_motion:
        raise ValueError("The local pipeline produces still images or text cards, not moving actors; save a draft for cloud video")
    if config.duration_seconds > 60:
        raise ValueError("The local ContentPlan supports 30–60 seconds; save longer productions as drafts")
    if config.narration != "local_tts":
        raise ValueError("Model-generated audio is not implemented; choose local TTS or save a draft")
    if config.character_reference_ids:
        raise ValueError("Character references are not implemented; remove references or save a draft")
    if config.captions != "scene_text":
        raise ValueError("Timed captions are not implemented; choose scene text or save a draft")


def validate_for_cloud_video(config: GenerationConfig) -> None:
    validate_for_script(config)
    if config.video_provider == 'local_cinematic':
        # Kept behind the existing tracked-production entry point. Runtime
        # readiness is checked by its adapter at quote/submission time.
        local_cinematic_models.validate(config)
        return
    if config.video_provider not in {"google", "runway"}:
        raise ValueError("Select Google Gemini or Runway explicitly for cloud video")
    credential = 'RUNWAYML_API_SECRET' if config.video_provider == 'runway' else 'GEMINI_API_KEY'
    if not os.getenv(credential):
        raise ValueError(f"{credential} is not configured")
    if config.content_type in {"short_drama", "animated_comedy"} and config.narration != "model_audio":
        raise ValueError("Character dialogue requires model audio; local TTS is a single narrator")
