from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.generation_config import GenerationConfig


def test_defaults_and_legacy_use_configured_local_models(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.setenv("IMAGE_MODEL_ID", "authorized/local-image")
    config = GenerationConfig.from_legacy("diffusion")
    assert config.script_model == "local-test-model"
    assert config.image_model == "authorized/local-image"
    assert config.image_mode == "diffusion"
    assert config.video_provider == "local"
    assert config.max_cost_usd == Decimal("10")
    assert config.paid_generation_confirmed is False


@pytest.mark.parametrize("aspect,resolution,dimensions", [
    ("9:16", "1080p", (1080, 1920)), ("16:9", "1080p", (1920, 1080)),
    ("1:1", "1080p", (1080, 1080)), ("9:16", "720p", (720, 1280)),
    ("16:9", "720p", (1280, 720)), ("1:1", "720p", (720, 720)),
])
def test_dimensions(aspect, resolution, dimensions):
    assert GenerationConfig(aspect_ratio=aspect, resolution=resolution).output_dimensions == dimensions


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"content_type": "unknown"}, {"video_provider": "unknown"},
    {"duration_seconds": 29}, {"duration_seconds": 1201}, {"duration_seconds": True},
    {"max_shots": 0}, {"max_shots": 13}, {"max_cost_usd": "-0.01"},
    {"max_cost_usd": "1000.01"}, {"max_cost_usd": "NaN"}, {"fps": 60},
    {"topic": "x" * 1001}, {"supplied_script": "x" * 20001},
    {"character_reference_ids": [str(uuid4()) for _ in range(4)]},
    {"character_reference_ids": ["../../credentials/admin.json"]},
    {"character_reference_ids": ["https://example.com/image.png"]},
    {"OPENAI_API_KEY": "never-store-me"}, {"require_motion": "false"},
    {"script_model": " model"}, {"script_model": "model "},
    {"video_model": "local\npipeline"}, {"image_model": "image\x00model"},
])
def test_invalid_settings_and_secrets_are_rejected(changes):
    with pytest.raises(ValidationError):
        GenerationConfig(**changes)


def test_json_round_trip_preserves_decimal_and_reference_ids():
    reference_id = uuid4()
    config = GenerationConfig(
        character_reference_ids=[str(reference_id)], max_cost_usd="0.125"
    )
    encoded = config.model_dump(mode="json")
    assert encoded["max_cost_usd"] == "0.125"
    assert encoded["character_reference_ids"] == [str(reference_id)]
    restored = GenerationConfig.model_validate_json(config.model_dump_json())
    assert restored == config
    assert isinstance(restored.character_reference_ids[0], UUID)


def test_duplicate_references_are_rejected():
    reference = uuid4()
    with pytest.raises(ValidationError, match="unique"):
        GenerationConfig(character_reference_ids=[reference, reference])


def test_validation_error_text_hides_secret_value():
    with pytest.raises(ValidationError) as error:
        GenerationConfig(OPENAI_API_KEY="do-not-echo-this-token")
    assert "do-not-echo-this-token" not in str(error.value)


def test_model_identifier_defaults_are_validated(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "invalid model")
    with pytest.raises(ValidationError, match="whitespace"):
        GenerationConfig()
