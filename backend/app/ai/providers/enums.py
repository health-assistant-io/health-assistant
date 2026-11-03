"""AI provider/task enums.

These are the single source of truth replacing the hard-coded string lists that
previously lived inside ``ai_provider_service.get_config_summary`` and the
scattered ``provider_type == "openai"`` comparisons.

Both enums inherit ``(str, Enum)`` so their members are plain strings: the DB
columns (``ai_providers.provider_type``, ``ai_task_assignments.task_type``)
remain plain ``String`` columns and need NO migration. ``TaskType.CHAT == "chat"``
and ``TaskType.CHAT.value == "chat"`` both hold.
"""

from __future__ import annotations

import enum


class ProviderType(enum.StrEnum):
    """Supported AI/processor backend types.

    The ``provider_type`` column on ``ai_providers`` carries one of these values
    (historically as a free-form string). Member values are lowercase to match
    the OCR/NLP factory dispatch keys (``"openai"``, ``"tesseract"``, ``"spacy"``).

    Reserved members (``ANTHROPIC``, ``OLLAMA``, ``AZURE_OPENAI``, ``BEDROCK``)
    are not yet wired to an LLM builder — their factories raise
    ``NotImplementedError`` until the matching SDK is added.
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


class TaskType(enum.StrEnum):
    """Canonical AI task types.

    Values match the ``task_type`` string column on ``ai_task_assignments`` and
    the frontend ``AIConfigSummary`` field keys verbatim — no renames, no
    migration. New task types are added here (single source of truth) and
    automatically surface in the config summary via :meth:`all_values`.
    """

    DEFAULT = "default"
    OCR = "ocr"
    NLP = "nlp"
    MEDICATION_INTERACTION = "medication_interaction"
    ANOMALY_DETECTION = "anomaly_detection"
    FILL_BIOMARKER_FORM = "fill_biomarker_form"
    FILL_MEDICATION_FORM = "fill_medication_form"
    MAGIC_FILL_EXAMINATION = "magic_fill_examination"
    DEFINE_BIOMARKER = "define_biomarker"
    DEFINE_MEDICATION = "define_medication"
    DEFINE_ANATOMY_GRAPH = "define_anatomy_graph"
    SUGGEST_CATEGORY_ICON = "suggest_category_icon"
    GENERATE_CATEGORY_ICON = "generate_category_icon"
    CHAT = "chat"
    TRANSCRIPTION = "transcription"
    #: Embedding vectors (§15 family ``embeddings`` capability) — the embed
    #: slot the setup gap-fill binds the preset's docs-verified default into.
    EMBED = "embed"

    # --- Decision slots (plan 26 / ADR-0032; D17: PHI scope guard applies) ---
    #: Health's storage of the family ``decision:<cap>`` slots (§3 contract
    #: table); fallback = ``priority`` ordering across the scope cascade.
    #: Resolution NEVER falls back to ``default`` (D4 slot separation).
    DECISION_TOOL_DISPATCH = "decision_tool_dispatch"
    DECISION_BOOLEAN_GATE = "decision_boolean_gate"
    DECISION_CHOICE = "decision_choice"
    DECISION_SCORE = "decision_score"

    # --- Decision default (ADR-0036 §1; D17 PHI scope guard applies) ---
    #: The ``decision`` default slot joins the vocabulary beside the kind
    #: overrides: resolution order is kind override → decision default →
    #: unavailable — never a hop onto ``default`` (D4 slot separation).
    #: Single-slot per ADR-0037 (no fallback of its own).
    DECISION = "decision"

    @classmethod
    def all_values(cls) -> list[str]:
        """Ordered list of task_type string values.

        Replaces the hard-coded list that used to live inline in
        ``AIProviderService.get_config_summary``.
        """
        return [member.value for member in cls]

    @classmethod
    def from_string(cls, value: str | None) -> TaskType | None:
        """Tolerant lookup; returns ``None`` for unknown values."""
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None

    @classmethod
    def is_decision_default(cls, value: str | None) -> bool:
        """True for the ``decision`` default slot (ADR-0036)."""
        return value == cls.DECISION.value

    @classmethod
    def is_decision(cls, value: str | None) -> bool:
        """True for the four ``decision_*`` slots (plan 26 / ADR-0032).

        Decision slots live in the same table as every other assignment but
        obey D4 slot separation: they resolve only decision-capable models
        and never fall back to the ``default`` task.
        """
        return value in (
            cls.DECISION_TOOL_DISPATCH.value,
            cls.DECISION_BOOLEAN_GATE.value,
            cls.DECISION_CHOICE.value,
            cls.DECISION_SCORE.value,
        )
