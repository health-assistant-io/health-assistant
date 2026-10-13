"""Local BYOK preset overlay (§15).

The canonical preset data lives in ``presets_data.py`` (GENERATED — never
hand-edit). This module is the ONLY place health-specific deviations live:

* ``wire_type`` → health's ``ProviderType`` enum mapping (ADR-0008 wire
  vocabulary vs. health's registry dispatch keys);
* per-app enablement: presets whose health factory is not wired yet are
  disabled here, with a recorded reason — never a canonical edit.

Plan 26 / ADR-0032 (Phase 5 + the 2026-10-13 §13.3 follow-up):
``ALL_PRESETS`` is the full mappable surface (including decision-only
presets such as ``typesafe``); ``SETUP_PRESETS`` stays the TILE surface
(family order minus health-disabled presets). The docs-verified
``typesafe`` preset rides ``presetOrder`` like every preset — the
exposure switch flipped 2026-10-13 (a canonical edit, never a per-repo
one). The setup endpoint accepts ``ALL_PRESETS``; decision-only presets
validate via the decision probe (D13), never a catalog fetch.

Canonical text: dev/guidelines/ai-features.md §15; data: dev/contracts/
ai-presets.json.
"""

from __future__ import annotations

from typing import Any

from app.ai.providers.enums import ProviderType

from .presets_data import KEY_PREFIX_HINTS as _KEY_PREFIX_HINT_DATA
from .presets_data import PRESET_ORDER as _PRESET_ORDER
from .presets_data import PRESETS as _PRESET_DATA

PRESET_ORDER: tuple[str, ...] = tuple(_PRESET_ORDER)

#: Canonical §15 wire type → health's ProviderType (the registry dispatch key).
#: ``openai_compatible`` covers OpenAI, OpenRouter, Groq, Mistral, DeepSeek and
#: Ollama (health's ``OPENAI`` builder is the wired OpenAI-compatible one).
#: ``decision`` (ADR-0032 §4/D13) maps to the decision-only provider type —
#: served by the decide funnel, never an LLM builder.
WIRE_TYPE_TO_PROVIDER_TYPE: dict[str, str] = {
    "openai_compatible": ProviderType.OPENAI.value,
    "decision": ProviderType.DECISION.value,
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

_PRESET_FIELDS = (
    "name",
    "wire_type",
    "base_url",
    "fixed_base",
    "local",
    "key_url",
    "preferred_model",
    "curated_models",
    "stt_model",
    "embeddings_model",
    "decision_models",
    "decision_default",
    "provisional",
    "steps",
    "free_tier_note",
)


def _build(key: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
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
        "embeddings_model": row["embeddings_model"],
        "decision_models": row["decision_models"],
        "decision_default": row["decision_default"],
        "provisional": row["provisional"],
        "steps": row["steps"],
        "free_tier_note": row["free_tier_note"],
    }


#: Every canonical preset health can dispatch (tiles + decision-only). The
#: provisional exposure switch rides ``PRESET_ORDER`` below.
ALL_PRESETS: dict[str, dict[str, Any]] = {
    key: _build(key, row)
    for key, row in _PRESET_DATA.items()
    if row["wire_type"] in WIRE_TYPE_TO_PROVIDER_TYPE and key not in DISABLED_PRESET_REASONS
}

#: The TILE surface (§15): family order, minus health-disabled presets.
#: Decision-only presets not in ``PRESET_ORDER`` never appear here.
SETUP_PRESETS: dict[str, dict[str, Any]] = {
    key: ALL_PRESETS[key] for key in _PRESET_ORDER if key in ALL_PRESETS
}

KEY_PREFIX_HINTS: tuple[dict[str, str], ...] = tuple(_KEY_PREFIX_HINT_DATA)


def is_preset_key(key: str) -> bool:
    return key in SETUP_PRESETS


def is_any_preset_key(key: str) -> bool:
    return key in ALL_PRESETS


def guess_preset_for_key(api_key: str | None) -> str | None:
    """Most-specific-first vendor guess from the key prefix (§15 hints)."""
    trimmed = (api_key or "").strip()
    if not trimmed:
        return None
    for hint in KEY_PREFIX_HINTS:
        if trimmed.startswith(hint["prefix"]):
            return hint["preset"]
    return None


def decision_models_for_preset(key: str) -> list[dict[str, Any]]:
    """The preset's decision models (presets v2), empty when it declares none."""
    models = ALL_PRESETS.get(key, {}).get("decision_models")
    if not models:
        return []
    return [dict(entry) for entry in models]


def decision_probe_model_id(preset: dict[str, Any]) -> str:
    """The model id a decision probe asks about for a decision preset.

    §15 single-source rule: the id comes from the synced canonical data
    (``decision_models[0].id``, falling back to ``decision_default``) —
    never from a code literal, so a canonical curation change flows
    through without a code edit. A decision preset that declares neither
    is a data inconsistency: surfaced as an error, not papered over with
    an invented id.
    """
    specs = preset.get("decision_models") or []
    if specs:
        return str(specs[0]["id"])
    default = preset.get("decision_default")
    if default:
        return str(default)
    raise ValueError("decision preset declares no decision models (canonical presets data)")
