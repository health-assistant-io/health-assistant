"""Local BYOK preset overlay (§15).

The canonical preset data lives in ``presets_data.py`` (GENERATED — never
hand-edit). This module is the ONLY place health-specific deviations live:

* ``wire_type`` → health's ``ProviderType`` enum mapping (ADR-0008 wire
  vocabulary vs. health's registry dispatch keys);
* per-app enablement: presets whose health factory is not wired yet are
  disabled here, with a recorded reason — never a canonical edit.

Canonical text: dev/guidelines/ai-features.md §15; data: dev/contracts/
ai-presets.json.
"""

from __future__ import annotations

from typing import Any, Optional

from app.ai.providers.enums import ProviderType
from .presets_data import KEY_PREFIX_HINTS as _KEY_PREFIX_HINT_DATA
from .presets_data import PRESET_ORDER as _PRESET_ORDER
from .presets_data import PRESETS as _PRESET_DATA

PRESET_ORDER: tuple[str, ...] = tuple(_PRESET_ORDER)

#: Canonical §15 wire type → health's ProviderType (the registry dispatch key).
#: ``openai_compatible`` covers OpenAI, OpenRouter, Groq, Mistral, DeepSeek and
#: Ollama (health's ``OPENAI`` builder is the wired OpenAI-compatible one).
WIRE_TYPE_TO_PROVIDER_TYPE: dict[str, str] = {
    "openai_compatible": ProviderType.OPENAI.value,
}

#: Presets not offered by health's setup surface yet, with the recorded reason.
#: Removing an entry is the ONLY change needed once the matching factory branch
#: lands in ``app/ai/chat_models.py`` + ``app/ai/providers/registry.py``.
DISABLED_PRESET_REASONS: dict[str, str] = {
    "gemini": (
        "health's registry has no wired 'google' LLM builder yet "
        "(build_google is unimplemented) — enable when the factory branch lands"
    ),
    "anthropic": (
        "health's anthropic builder is a reserved stub "
        "(raises NotImplementedError) — enable when langchain-anthropic is wired"
    ),
}

#: No base-URL overrides needed: health's setup fetcher appends the same
#: resource paths as the canonical data assumes (``{base}/models`` etc.).

SETUP_PRESETS: dict[str, dict[str, Any]] = {
    key: {
        "name": row["name"],
        "type": WIRE_TYPE_TO_PROVIDER_TYPE[row["wire_type"]],
        "wire_type": row["wire_type"],
        "base_url": row["base_url"],
        "fixed_base": row["fixed_base"],
        "local": row["local"],
        "key_url": row["key_url"],
        "preferred_model": row["preferred_model"],
        "curated_models": row["curated_models"],
        "stt_model": row["stt_model"],
        "steps": row["steps"],
        "free_tier_note": row["free_tier_note"],
    }
    for key in _PRESET_ORDER
    if key not in DISABLED_PRESET_REASONS
    for row in (_PRESET_DATA[key],)
    if row["wire_type"] in WIRE_TYPE_TO_PROVIDER_TYPE
}

KEY_PREFIX_HINTS: tuple[dict[str, str], ...] = tuple(_KEY_PREFIX_HINT_DATA)


def is_preset_key(key: str) -> bool:
    return key in SETUP_PRESETS


def guess_preset_for_key(api_key: str | None) -> Optional[str]:
    """Most-specific-first vendor guess from the key prefix (§15 hints)."""
    trimmed = (api_key or "").strip()
    if not trimmed:
        return None
    for hint in KEY_PREFIX_HINTS:
        if trimmed.startswith(hint["prefix"]):
            return hint["preset"]
    return None
