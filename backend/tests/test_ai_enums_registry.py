"""Unit tests for the AI provider/task enums and the LLM-builder registry.

These pin the Phase 1 foundation: the canonical task-type list, the provider
enum membership, tolerant lookups, and the registry fallback semantics. They
guard against drift in later refactor phases.
"""

from unittest.mock import MagicMock

import pytest
from langchain_openai import ChatOpenAI

from app.ai import chat_models
from app.ai.providers.enums import ProviderType, TaskType
from app.ai.providers.registry import (
    DEFAULT_BUILDER,
    PROVIDER_FACTORIES,
    get_llm_builder,
)
from app.ai.providers.task_registry import TASK_IDS, TASKS, get_task_def
from app.services.engine.engine_task_registry import ENGINE_TASKS

# ---------------------------------------------------------------------------
# TaskType
# ---------------------------------------------------------------------------


class TestTaskType:
    def test_values_are_plain_strings(self):
        assert TaskType.CHAT == "chat"
        assert TaskType.CHAT.value == "chat"

    def test_task_types_are_the_registry(self):
        """Parity: the generated TaskType ≡ the TaskDef registry (plan 4 W1).

        The registry is the source of truth; this asserts the generated enum was
        regenerated (``node scripts/sync-task-registry.mjs --py …``) and that no
        one hand-edited either side. Adding a task is a registry edit + a
        regeneration, never a second list.
        """
        assert TaskType.all_values() == list(TASK_IDS)
        for task_id in TASK_IDS:
            assert TaskType(task_id).value == task_id
            assert get_task_def(task_id) is not None

    def test_engine_tasks_are_not_llm_tasks(self):
        """Plan 4 W4: RxNorm / analytics work is not an AI task.

        ``medication_interaction`` and ``anomaly_detection`` moved to
        ``app.services.engine.engine_task_registry`` — they must never reappear as LLM
        task ids (no slot, no assignment, no audit route).
        """
        assert "medication_interaction" not in TaskType.all_values()
        assert "anomaly_detection" not in TaskType.all_values()
        for task in ENGINE_TASKS:
            assert task.id not in TaskType.all_values()

    def test_every_task_declares_the_single_slot_law(self):
        """ADR-0037: no task accepts priority — the knob is gone from the schema."""
        assert all(task.accepts_priority is False for task in TASKS)

    def test_default_is_first(self):
        # get_config_summary relies on "default" being present (fallback target).
        assert TaskType.all_values()[0] == "default"

    def test_from_string_roundtrip(self):
        for member in TaskType:
            assert TaskType.from_string(member.value) is member

    def test_from_string_tolerant(self):
        assert TaskType.from_string(None) is None
        assert TaskType.from_string("not-a-task") is None
        assert TaskType.from_string("") is None


# ---------------------------------------------------------------------------
# ProviderType
# ---------------------------------------------------------------------------


class TestProviderType:
    def test_known_values(self):
        assert ProviderType.OPENAI.value == "openai"
        assert ProviderType.TESSERACT.value == "tesseract"
        assert ProviderType.SPACY.value == "spacy"
        assert ProviderType.ANTHROPIC.value == "anthropic"

    def test_from_string_tolerant(self):
        assert ProviderType.from_string("openai") is ProviderType.OPENAI
        assert ProviderType.from_string(None) is None
        assert ProviderType.from_string("unknown") is None

    def test_is_llm_capable(self):
        assert ProviderType.is_llm_capable("openai") is True
        assert ProviderType.is_llm_capable("anthropic") is True
        assert ProviderType.is_llm_capable("ollama") is True
        # processor-only backends are NOT LLM-capable
        assert ProviderType.is_llm_capable("tesseract") is False
        assert ProviderType.is_llm_capable("spacy") is False
        # unknown degrades gracefully
        assert ProviderType.is_llm_capable("nope") is False
        assert ProviderType.is_llm_capable(None) is False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_openai_resolves_to_openai_builder(self):
        assert get_llm_builder("openai") is chat_models.build_openai

    def test_all_llm_providers_registered(self):
        # Every LLM-capable provider type must have a registered builder.
        for pt in [
            ProviderType.OPENAI,
            ProviderType.ANTHROPIC,
            ProviderType.OLLAMA,
            ProviderType.AZURE_OPENAI,
            ProviderType.BEDROCK,
        ]:
            assert pt in PROVIDER_FACTORIES

    def test_processor_backends_not_in_registry(self):
        # tesseract / spacy are processor backends, not LLM providers.
        assert ProviderType.TESSERACT not in PROVIDER_FACTORIES
        assert ProviderType.SPACY not in PROVIDER_FACTORIES

    def test_unknown_falls_back_to_default(self):
        assert DEFAULT_BUILDER is chat_models.build_openai
        assert get_llm_builder("never-heard-of-it") is chat_models.build_openai
        assert get_llm_builder(None) is chat_models.build_openai

    def test_unknown_falls_back_silently(self):
        # Must not raise — a single bad DB row must not break every LLM call.
        builder = get_llm_builder("garbage")
        assert callable(builder)

    def test_stub_providers_raise_not_implemented(self):
        # Reserved providers are registered but unwired — calling them surfaces
        # a clear error rather than silently producing an OpenAI client.
        with pytest.raises(NotImplementedError):
            get_llm_builder("anthropic")(
                api_key="k",
                base_url="b",
                model_name="m",
                temperature=0.5,
                max_tokens=10,
            )
        with pytest.raises(NotImplementedError):
            get_llm_builder("ollama")(
                api_key="k",
                base_url="b",
                model_name="m",
                temperature=0.5,
                max_tokens=10,
            )


# ---------------------------------------------------------------------------
# chat_models.build_openai
# ---------------------------------------------------------------------------


class TestBuildOpenAI:
    def test_returns_chat_openai(self):
        llm = chat_models.build_openai(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model_name="gpt-4o-mini",
            temperature=0.3,
            max_tokens=1234,
        )
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-4o-mini"
        # max_tokens is exposed as max_tokens on the ChatOpenAI instance
        assert llm.max_tokens == 1234

    def test_reasoning_effort_setting_forwarded(self):
        llm = chat_models.build_openai(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model_name="gpt-5.6-luna",
            temperature=0.7,
            max_tokens=65536,
            reasoning_effort="none",
        )
        assert isinstance(llm, ChatOpenAI)
        assert llm.reasoning_effort == "none"

    def test_reasoning_effort_none_keeps_provider_default(self):
        llm = chat_models.build_openai(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model_name="gpt-4o",
            temperature=0.7,
            max_tokens=1024,
        )
        assert llm.reasoning_effort is None


class TestModelReasoningEffortSetting:
    def test_reads_from_model_settings_jsonb(self):
        from app.ai.providers.service import _model_reasoning_effort

        model = MagicMock()
        model.settings = {"reasoning_effort": "none"}
        assert _model_reasoning_effort(model) == "none"

    def test_absent_settings_returns_none(self):
        from app.ai.providers.service import _model_reasoning_effort

        assert _model_reasoning_effort(None) is None
        model = MagicMock()
        model.settings = None
        assert _model_reasoning_effort(model) is None
        model.settings = {}
        assert _model_reasoning_effort(model) is None
