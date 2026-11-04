import json
from uuid import uuid4

import pytest

from app.models.generation_config import GenerationConfig
from app.services.provider_catalog import (
    GOOGLE_MODEL, KLING_MODEL, OPENAI_MODEL, get_catalog,
    validate_for_draft, validate_for_execution,
)


def test_catalog_does_not_claim_model_or_account_access(monkeypatch):
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "KLING_API_KEY", "RUNWAYML_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    catalog = get_catalog()
    assert catalog["phase"] == 6
    assert len(catalog["content_types"]) == 5
    assert [item["id"] for item in catalog["content_types"] if item["executable"]] == ["realistic_short"]
    local = catalog["video_providers"][0]
    assert local["implemented"] is True
    assert local["status"] == "runtime_unverified"
    assert local["models"][0]["motion"] is False
    assert all(not p["configured"] for p in catalog["video_providers"] if p['id'] in {'google','runway'})
    types = {item["id"]: item for item in catalog["content_types"]}
    assert types["short_drama"]["phase"] == 4
    assert types["news"]["phase"] == 6
    assert types["long_drama"]["phase"] == 7
    assert types['long_drama']['cloud_executable'] is True


def test_catalog_exposes_only_credential_presence(monkeypatch):
    secrets = ["dummy-openai-secret", "dummy-google-secret", "dummy-kling-secret", "dummy-runway-secret"]
    for name, secret in zip(("OPENAI_API_KEY", "GEMINI_API_KEY", "KLING_API_KEY", "RUNWAYML_API_SECRET"), secrets):
        monkeypatch.setenv(name, secret)
    catalog = get_catalog()
    providers = catalog["script_providers"][1:] + [p for p in catalog["video_providers"] if p['id'] in {'google','runway'}]
    assert all(provider["configured"] is True for provider in providers)
    assert providers[0]["implemented"] is True
    assert providers[0]["account_access"] == "unverified"
    assert all(provider["implemented"] is True for provider in providers[1:])
    assert all(provider["executable"] is True for provider in providers[1:])
    assert [item["id"] for item in catalog["video_providers"]] == ["local", "google", "runway", "local_cinematic"]
    serialized = json.dumps(catalog)
    assert all(secret not in serialized for secret in secrets)


@pytest.mark.parametrize("provider,model,changes", [
    ("google", GOOGLE_MODEL, {"fps": 24}), ("kling", KLING_MODEL, {}),
])
@pytest.mark.parametrize("confirmed", [False, True])
def test_cloud_cannot_execute_with_credentials_or_payment_confirmation(monkeypatch, provider, model, changes, confirmed):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    monkeypatch.setenv("KLING_API_KEY", "dummy-key")
    config = GenerationConfig(
        video_provider=provider, video_model=model,
        paid_generation_confirmed=confirmed, **changes,
    )
    validate_for_draft(config)
    if provider == "kling":
        with pytest.raises(ValueError, match="retired"):
            validate_for_execution(config)
    else:
        validate_for_execution(config)
        from app.services.job_service import JobService
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            with pytest.raises(ValueError, match="completed script"):
                JobService(directory + "/test.db").create_job(generation_config=config)


def test_openai_adapter_capability_does_not_replace_paid_authorization(monkeypatch, tmp_path):
    from app.services.job_service import JobService
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key")
    config = GenerationConfig(script_provider="openai", script_model=OPENAI_MODEL,
                              paid_generation_confirmed=True)
    validate_for_draft(config)
    validate_for_execution(config)
    jobs = JobService(tmp_path / "test.db")
    with pytest.raises(ValueError, match="confirmation"):
        jobs.create_job(generation_config=config, actor="admin")
    assert jobs.list_jobs() == []


def test_provider_clip_limit_does_not_limit_draft_production_length():
    config = GenerationConfig(video_provider="google", video_model=GOOGLE_MODEL,
                              fps=24, duration_seconds=1200)
    validate_for_draft(config)
    google = get_catalog()["video_providers"][1]["models"][0]
    assert google["clip_duration_seconds"] == {"min": 3, "max": 10}


@pytest.mark.parametrize("changes,message", [
    ({"script_model": "unverified-model"}, "Unknown script model"),
    ({"video_model": GOOGLE_MODEL}, "Unknown video model"),
    ({"image_model": "unverified-image"}, "Unknown image model"),
    ({"video_provider": "google", "video_model": GOOGLE_MODEL, "fps": 30}, "24 FPS"),
    ({"video_provider": "google", "video_model": GOOGLE_MODEL, "fps": 24, "aspect_ratio": "1:1"}, "1:1"),
    ({"source_mode": "topic", "topic": " "}, "Enter a topic"),
    ({"source_mode": "script", "supplied_script": " "}, "Enter a script"),
])
def test_invalid_model_pairings_and_native_capabilities(changes, message):
    with pytest.raises(ValueError, match=message):
        validate_for_draft(GenerationConfig(**changes))


@pytest.mark.parametrize("changes,message", [
    ({"content_type": "short_drama"}, "content type"),
    ({"content_type": "animated_comedy"}, "content type"),
    ({"content_type": "long_drama"}, "Long-form"),
    ({"content_type": "news"}, "content type"),
    ({"require_motion": True}, "moving actors"),
    ({"duration_seconds": 61}, "30–60"),
    ({"topic": "Do not silently ignore this"}, "unused topic"),
    ({"narration": "model_audio"}, "audio"),
    ({"character_reference_ids": [str(uuid4())]}, "references"),
    ({"captions": "timed"}, "Timed captions"),
])
def test_planned_features_are_drafts_only(changes, message):
    config = GenerationConfig(**changes)
    validate_for_draft(config)
    with pytest.raises(ValueError, match=message):
        validate_for_execution(config)


def test_existing_text_and_diffusion_requests_remain_executable_without_cloud_credentials(monkeypatch):
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "KLING_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for mode in ("text", "diffusion"):
        validate_for_execution(GenerationConfig.from_legacy(mode))


def test_local_models_are_curated_from_configuration(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "configured-script")
    monkeypatch.setenv("IMAGE_MODEL_ID", "configured-image")
    catalog = get_catalog()
    assert catalog["script_providers"][0]["models"][0]["id"] == "configured-script"
    assert catalog["image_models"][0]["id"] == "configured-image"
    validate_for_execution(GenerationConfig())
