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
from typing import Callable, Dict, Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.ai.providers import factories
from app.ai.providers.enums import ProviderType

logger = logging.getLogger(__name__)

# A builder takes keyword-only LLM config and returns a BaseChatModel.
LLMBuilder = Callable[..., BaseChatModel]

# Registered LLM builders keyed by provider type. Add new providers here.
PROVIDER_FACTORIES: Dict[ProviderType, LLMBuilder] = {
    ProviderType.OPENAI: factories.build_openai,
    ProviderType.ANTHROPIC: factories.build_anthropic,
    ProviderType.OLLAMA: factories.build_ollama,
    ProviderType.AZURE_OPENAI: factories.build_azure_openai,
    ProviderType.BEDROCK: factories.build_bedrock,
}

# Fallback builder when a DB row carries an unknown / unrecognised provider_type
# (e.g. created before this enum existed, or a custom OpenAI-compatible endpoint
# typed as something exotic). Falls back to the OpenAI-compatible builder and
# logs a WARNING — never raises, so a single misconfigured row cannot break
# every LLM call.
DEFAULT_BUILDER: LLMBuilder = factories.build_openai


def get_llm_builder(provider_type: Optional[str]) -> LLMBuilder:
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
