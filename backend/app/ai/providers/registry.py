"""Provider-type -> LLM builder registry.

This is the single dispatch point that lets ``AIProviderService.get_llm``
support new LLM providers WITHOUT editing its body: register a
``(ProviderType, builder)`` pair in ``PROVIDER_FACTORIES`` below and the
resolution path picks it up automatically.

Processor-only backends (``tesseract``, ``spacy``) are deliberately absent —
they never produce a ``BaseChatModel`` and are dispatched through the OCR/NLP
processor factories instead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel

from app.ai import chat_models
from app.ai.providers.enums import ProviderType

logger = logging.getLogger(__name__)

# A builder takes keyword-only LLM config and returns a BaseChatModel.
LLMBuilder = Callable[..., BaseChatModel]

# Registered LLM builders keyed by provider type. Add new providers here.
PROVIDER_FACTORIES: dict[ProviderType, LLMBuilder] = {
    ProviderType.OPENAI: chat_models.build_openai,
    ProviderType.ANTHROPIC: chat_models.build_anthropic,
    ProviderType.OLLAMA: chat_models.build_ollama,
    ProviderType.AZURE_OPENAI: chat_models.build_azure_openai,
    ProviderType.BEDROCK: chat_models.build_bedrock,
    ProviderType.MOCK: chat_models.build_mock,
}

# Fallback builder when a DB row carries an unknown / unrecognised provider_type
# (e.g. created before this enum existed, or a custom OpenAI-compatible endpoint
# typed as something exotic). Falls back to the OpenAI-compatible builder and
# logs a WARNING — never raises, so a single misconfigured row cannot break
# every LLM call.
DEFAULT_BUILDER: LLMBuilder = chat_models.build_openai


def get_llm_builder(provider_type: str | None) -> LLMBuilder:
    """Resolve an LLM builder for a ``provider_type`` string (from the DB row).

    Returns the matching registered builder, or ``DEFAULT_BUILDER`` (with a
    warning) when the provider_type is unknown or missing. Never raises.
    """
    pt = ProviderType.from_string(provider_type)
    if pt is None or pt not in PROVIDER_FACTORIES:
        logger.warning(
            "No LLM builder registered for provider_type=%r; falling back to "
            "the OpenAI-compatible default builder.",
            provider_type,
        )
        return DEFAULT_BUILDER
    return PROVIDER_FACTORIES[pt]


#: Provider types registered only as reserved stubs: their builder raises
#: ``NotImplementedError`` on first call, so a provider row carrying one is
#: inert configuration. Write paths reject them
#: (:func:`ensure_wired_provider_type`); ``tests/test_ai_enums_registry.py``
#: pins this set to the stub builders so the two cannot drift.
UNWIRED_PROVIDER_TYPES: frozenset[ProviderType] = frozenset(
    {
        ProviderType.ANTHROPIC,
        ProviderType.OLLAMA,
        ProviderType.AZURE_OPENAI,
        ProviderType.BEDROCK,
    }
)


def unwired_provider_type(provider_type: str | None) -> ProviderType | None:
    """The reserved provider type, or ``None`` when it is wired/unknown."""
    pt = ProviderType.from_string(provider_type)
    if pt is not None and pt in UNWIRED_PROVIDER_TYPES:
        return pt
    return None


def ensure_wired_provider_type(provider_type: str | None) -> None:
    """Refuse a reserved (not-yet-wired) provider type at write time.

    Raises ``ValidationError`` (HTTP 422) carrying the API's typed machine
    error vocabulary — ``{"code": "provider_type_unwired", "message": ...}`` —
    instead of persisting a row whose first LLM call would raise
    ``NotImplementedError``.
    """
    from app.core.errors import ValidationError

    unwired = unwired_provider_type(provider_type)
    if unwired is None:
        return
    raise ValidationError(
        {
            "code": "provider_type_unwired",
            "message": (
                f"provider type '{unwired.value}' is reserved and not wired yet; "
                "create an OpenAI-compatible provider (provider_type 'openai') "
                "or wait until its builder lands"
            ),
        },
        status_code=422,
    )
