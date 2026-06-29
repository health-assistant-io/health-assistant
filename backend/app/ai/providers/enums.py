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
from typing import List, Optional


class ProviderType(str, enum.Enum):
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

    # --- Processor-only backends (NOT LLM providers; routed through OCR/NLP factories) ---
    TESSERACT = "tesseract"
    SPACY = "spacy"

    @classmethod
    def from_string(cls, value: Optional[str]) -> Optional["ProviderType"]:
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
    def is_llm_capable(cls, value: Optional[str]) -> bool:
        """True if ``value`` maps to a provider that yields a ``BaseChatModel``.

        Processor-only backends (``tesseract``, ``spacy``) return False — they
        are dispatched through the OCR/NLP processor factories, not the LLM
        registry.
        """
        pt = cls.from_string(value)
        return pt in {
            cls.OPENAI,
            cls.ANTHROPIC,
            cls.OLLAMA,
            cls.AZURE_OPENAI,
            cls.BEDROCK,
        }


class TaskType(str, enum.Enum):
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

    @classmethod
    def all_values(cls) -> List[str]:
        """Ordered list of task_type string values.

        Replaces the hard-coded list that used to live inline in
        ``AIProviderService.get_config_summary``.
        """
        return [member.value for member in cls]

    @classmethod
    def from_string(cls, value: Optional[str]) -> Optional["TaskType"]:
        """Tolerant lookup; returns ``None`` for unknown values."""
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None
