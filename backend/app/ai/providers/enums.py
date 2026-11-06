"""AI provider/task enums.

These are the single source of truth replacing the hard-coded string lists that
previously lived inside ``ai_provider_service.get_config_summary`` and the
scattered ``provider_type == "openai"`` comparisons.

``TaskType`` is GENERATED from the one task registry
(:mod:`app.ai.providers.task_registry`) into ``task_types.py`` by
``scripts/sync-task-registry.mjs``; this module re-exports it so
``app.ai.providers.enums.TaskType`` stays the stable import path. The provider
enum below is hand-written (provider types are a closed behavioral vocabulary).

Both enums inherit ``str`` (``StrEnum``) so their members are plain strings: the
DB columns (``ai_providers.provider_type``) remain plain ``String`` columns and
need NO migration.
"""

from __future__ import annotations

import enum

from app.ai.providers.task_types import TaskType

__all__ = [
    "DecisionCapability",
    "DecisionTransport",
    "ProviderType",
    "SystemOneErrorKind",
    "TaskType",
]


class ProviderType(enum.StrEnum):
    """Supported AI/processor backend types.

    The ``provider_type`` column on ``ai_providers`` carries one of these values
    (historically as a free-form string). Member values are lowercase to match
    the OCR/NLP factory dispatch keys (``"openai"``, ``"tesseract"``, ``"spacy"``).

    Reserved members (``ANTHROPIC``, ``OLLAMA``, ``AZURE_OPENAI``, ``BEDROCK``)
    are not yet wired to an LLM builder — their factories raise
    ``NotImplementedError`` until the matching SDK is added, and provider/model
    write paths refuse them up front (``provider_type_unwired``, 422).
    """

    # --- LLM-capable providers (routed through the provider registry) ---
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    AZURE_OPENAI = "azure_openai"
    BEDROCK = "bedrock"
    #: Deterministic scripted model (no network, no API key) for demos,
    #: UI screenshot capture and offline dev. Seeded by ``seed_demo.py``;
    #: never a default outside explicitly configured demo tenants.
    MOCK = "mock"

    # --- Processor-only backends (NOT LLM providers; routed through OCR/NLP factories) ---
    TESSERACT = "tesseract"
    SPACY = "spacy"

    # --- Decision-only providers (ADR-0032 / D13: wire ``systemone``) ---
    #: Serves ``decision_*`` task slots only, through the decide funnel —
    #: never yields a ``BaseChatModel``; routing it at chat is a config
    #: error the resolver rejects.
    DECISION = "decision"

    @classmethod
    def from_string(cls, value: str | None) -> ProviderType | None:
        """Tolerant lookup used by the registry/resolution path.

        Returns ``None`` for unknown/missing values instead of raising, so a DB
        row created before this enum existed (or carrying a typo) degrades
        gracefully — callers fall back rather than 500 on every LLM call.
        """
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None

    @classmethod
    def is_llm_capable(cls, value: str | None) -> bool:
        """True if ``value`` maps to a provider that yields a ``BaseChatModel``.

        Processor-only backends (``tesseract``, ``spacy``) and decision-only
        providers (``decision``) return False — they are dispatched through
        the OCR/NLP factories or the decide funnel, not the LLM registry.
        """
        pt = cls.from_string(value)
        return pt in {
            cls.OPENAI,
            cls.ANTHROPIC,
            cls.OLLAMA,
            cls.AZURE_OPENAI,
            cls.BEDROCK,
            cls.MOCK,
        }


class DecisionCapability(enum.StrEnum):
    """The sparse decision-capability matrix a transport declares (D2b).

    Family slots are health task types (``decision_tool_dispatch`` &c.); a
    decision point requests ONE capability and the resolver routes to a
    transport that declares it. ``OPEN_ARGS`` is vocabulary-only — reserved,
    never assignable until a transport declares it.
    """

    TOOL_DISPATCH = "tool-dispatch"
    BOOLEAN_GATE = "boolean-gate"
    CHOICE = "choice"
    SCORE = "score"
    OPEN_ARGS = "open-args"


class DecisionTransport(enum.StrEnum):
    """Decision transports (ADR-0032: transports replace engines)."""

    SYSTEMONE = "systemone"


class SystemOneErrorKind(enum.StrEnum):
    """Typed transport error kinds mapped off the TypeSafe SDK exceptions."""

    AUTH = "auth"
    RATE_LIMIT = "rate-limit"
    OVERLOADED = "overloaded"
    VALIDATION = "validation"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
