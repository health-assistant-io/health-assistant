"""Tests for model capabilities + the STT resolution/transcription wiring.

Covers:
  * ``app.ai.providers.capabilities`` — task→capability mapping + eligibility.
  * ``app.ai.assistance.stt._resolve_stt_target`` — capability enforcement +
    api_key/base/model resolution incl. env fallback shape.
  * ``AIModel.get_capabilities`` always includes the ``text`` baseline.
"""
from unittest.mock import MagicMock

import pytest

from app.ai.assistance.stt import STTTarget, TranscriptionError, _resolve_stt_target
from app.ai.providers.capabilities import (
    model_supports,
    normalize_capabilities,
    required_capabilities_for_task,
)
from app.ai.providers.enums import TaskType
from app.models.enums import AIModelCapability


# ---------------------------------------------------------------------------
# capabilities mapping
# ---------------------------------------------------------------------------


def test_chat_requires_text():
    assert required_capabilities_for_task(TaskType.CHAT.value) == {AIModelCapability.TEXT}


def test_ocr_requires_vision():
    assert required_capabilities_for_task(TaskType.OCR.value) == {AIModelCapability.VISION}


def test_transcription_requires_audio_input():
    assert required_capabilities_for_task(TaskType.TRANSCRIPTION.value) == {
        AIModelCapability.AUDIO_INPUT
    }


def test_unknown_task_defaults_to_text():
    assert required_capabilities_for_task("define_biomarker") == {AIModelCapability.TEXT}
    assert required_capabilities_for_task("nonexistent_task") == {AIModelCapability.TEXT}


def test_model_supports_all_required():
    assert model_supports(["text", "vision"], required_capabilities_for_task("ocr"))
    assert model_supports(["audio_input", "text"], required_capabilities_for_task("transcription"))


def test_model_supports_rejects_missing_capability():
    # text-only model cannot do OCR (vision) or transcription (audio_input)
    assert not model_supports(["text"], required_capabilities_for_task("ocr"))
    assert not model_supports(["text"], required_capabilities_for_task("transcription"))


def test_audio_only_model_rejected_for_text_tasks():
    # An STT-only model (e.g. whisper-1) is NOT eligible for chat (text) or
    # OCR (vision) — only for transcription (audio_input).
    assert not model_supports(["audio_input"], required_capabilities_for_task("chat"))
    assert not model_supports(["audio_input"], required_capabilities_for_task("ocr"))
    assert model_supports(
        ["audio_input"], required_capabilities_for_task("transcription")
    )


def test_normalize_capabilities_defaults_empty_to_text():
    # Empty/null falls back to the text baseline; non-empty is kept as-is
    # (text is NOT forced — an STT-only model can be audio_input-only).
    assert normalize_capabilities(None) == {"text"}
    assert normalize_capabilities([]) == {"text"}
    assert normalize_capabilities(["vision"]) == {"vision"}
    assert normalize_capabilities(["audio_input"]) == {"audio_input"}
    assert normalize_capabilities(["text", "vision"]) == {"text", "vision"}
    # unknown values are dropped
    assert normalize_capabilities(["vision", "bogus"]) == {"vision"}


# ---------------------------------------------------------------------------
# AIModel.get_capabilities baseline
# ---------------------------------------------------------------------------


def test_model_get_capabilities_defaults_empty_to_text():
    from app.models.ai_provider_model import AIModel

    class _Stub:
        capabilities = None

    stub = _Stub()
    # Non-empty is kept as-is — an STT-only model stays audio_input-only
    # (text is NOT forced).
    stub.capabilities = ["audio_input"]
    assert AIModel.get_capabilities(stub) == ["audio_input"]

    stub.capabilities = ["vision", "text"]
    assert set(AIModel.get_capabilities(stub)) == {"vision", "text"}

    # Empty / null / malformed fall back to the text baseline.
    stub.capabilities = None
    assert AIModel.get_capabilities(stub) == ["text"]

    stub.capabilities = "not-a-list"
    assert AIModel.get_capabilities(stub) == ["text"]


# ---------------------------------------------------------------------------
# STT target resolution
# ---------------------------------------------------------------------------


def _fake_provider(api_key_plain="sk-test", api_base="https://api.openai.com/v1"):
    p = MagicMock()
    p.get_api_key_plaintext.return_value = api_key_plain
    p.api_base = api_base
    return p


def _fake_model(caps, model_name="whisper-1"):
    m = MagicMock()
    m.model_name = model_name
    m.get_capabilities.return_value = caps
    return m


def test_resolve_stt_target_accepts_audio_input_model():
    provider = _fake_provider()
    model = _fake_model(["text", "audio_input"])
    target = _resolve_stt_target(provider, model)
    assert isinstance(target, STTTarget)
    assert target.api_key == "sk-test"
    assert target.api_base == "https://api.openai.com/v1"
    assert target.model_name == "whisper-1"


def test_resolve_stt_target_rejects_text_only_model():
    provider = _fake_provider()
    model = _fake_model(["text"], model_name="gpt-4o-mini")
    with pytest.raises(TranscriptionError, match="audio_input"):
        _resolve_stt_target(provider, model)


def test_resolve_stt_target_rejects_missing_api_key():
    provider = _fake_provider(api_key_plain=None)
    model = _fake_model(["audio_input"])
    with pytest.raises(TranscriptionError, match="API key"):
        _resolve_stt_target(provider, model)


def test_resolve_stt_target_strips_trailing_slash():
    provider = _fake_provider(api_base="https://api.openai.com/v1/")
    model = _fake_model(["audio_input"])
    target = _resolve_stt_target(provider, model)
    assert not target.api_base.endswith("/")
