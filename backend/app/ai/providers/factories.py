"""LLM builders for each LLM-capable ``ProviderType``.

A builder is a plain function that accepts keyword-only LLM configuration
(``api_key``, ``base_url``, ``model_name``, ``temperature``, ``max_tokens``)
and returns a LangChain ``BaseChatModel``. Builders are intentionally
side-effect-free and stateless — all tenancy/runtime context lives in the
caller.

Only OpenAI is wired today (the project is OpenAI-compatible by default).
Anthropic / Ollama / Azure OpenAI / Bedrock are registered as stubs that raise
``NotImplementedError`` so the provider enum + registry can be exercised end
to end before the SDK wiring lands. To finish a provider:

1. Add the SDK to ``backend/requirements.txt`` (e.g. ``langchain-anthropic``).
2. Implement its builder here.
3. (No registry change needed — it already points here.)
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def build_openai(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> BaseChatModel:
    """Build a ``ChatOpenAI`` (OpenAI-compatible: OpenAI, LocalAI, vLLM, ...)."""
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def build_anthropic(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> BaseChatModel:
    raise NotImplementedError(
        "Anthropic provider is reserved but not wired. Add langchain-anthropic to "
        "requirements.txt and implement this builder in app.ai.providers.factories."
    )


def build_ollama(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> BaseChatModel:
    raise NotImplementedError(
        "Ollama provider is reserved but not wired. Add langchain-ollama (or "
        "langchain-community) to requirements.txt and implement this builder."
    )


def build_azure_openai(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> BaseChatModel:
    raise NotImplementedError(
        "Azure OpenAI provider is reserved but not wired."
    )


def build_bedrock(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
) -> BaseChatModel:
    raise NotImplementedError(
        "Bedrock provider is reserved but not wired."
    )
