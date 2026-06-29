"""Unit tests for the AI provider/task enums and the LLM-builder registry.

These pin the Phase 1 foundation: the canonical task-type list, the provider
enum membership, tolerant lookups, and the registry fallback semantics. They
guard against drift in later refactor phases.
"""
import pytest

from langchain_openai import ChatOpenAI

from app.ai.providers import factories
from app.ai.providers.enums import ProviderType, TaskType
from app.ai.providers.registry import (
    DEFAULT_BUILDER,
    PROVIDER_FACTORIES,
    get_llm_builder,
)


# ---------------------------------------------------------------------------
# TaskType
# ---------------------------------------------------------------------------

class TestTaskType:
    def test_values_are_plain_strings(self):
        assert TaskType.CHAT == "chat"
        assert TaskType.CHAT.value == "chat"

    def test_all_values_matches_canonical_contract(self):
        # This is the exact list that used to be hard-coded inline in
        # AIProviderService.get_config_summary. Locking it in as a contract:
        # adding/removing a task type is an intentional, reviewed change.
        assert TaskType.all_values() == [
            "default",
            "ocr",
            "nlp",
            "medication_interaction",
            "anomaly_detection",
            "fill_biomarker_form",
            "fill_medication_form",
            "magic_fill_examination",
            "define_biomarker",
            "define_medication",
            "define_anatomy_graph",
            "suggest_category_icon",
            "generate_category_icon",
            "chat",
        ]

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
        assert get_llm_builder("openai") is factories.build_openai

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
        assert DEFAULT_BUILDER is factories.build_openai
        assert get_llm_builder("never-heard-of-it") is factories.build_openai
        assert get_llm_builder(None) is factories.build_openai

    def test_unknown_falls_back_silently(self):
        # Must not raise — a single bad DB row must not break every LLM call.
        builder = get_llm_builder("garbage")
        assert callable(builder)

    def test_stub_providers_raise_not_implemented(self):
        # Reserved providers are registered but unwired — calling them surfaces
        # a clear error rather than silently producing an OpenAI client.
        with pytest.raises(NotImplementedError):
            get_llm_builder("anthropic")(
                api_key="k", base_url="b", model_name="m",
                temperature=0.5, max_tokens=10,
            )
        with pytest.raises(NotImplementedError):
            get_llm_builder("ollama")(
                api_key="k", base_url="b", model_name="m",
                temperature=0.5, max_tokens=10,
            )


# ---------------------------------------------------------------------------
# factories.build_openai
# ---------------------------------------------------------------------------

class TestBuildOpenAI:
    def test_returns_chat_openai(self):
        llm = factories.build_openai(
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
