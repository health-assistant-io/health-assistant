"""Map AI tasks to the model capabilities they require.

A model advertises a SET of capabilities (``AIModelCapability``: the §15 family
vocabulary text / vision / tools / stt / tts / embeddings / decision) on its
JSONB ``capabilities`` column. Each task type needs one specific capability;
the task registry (:mod:`app.ai.providers.task_registry`) is the single source
of truth for that mapping — this module only translates it into the
``AIModelCapability`` enum and provides the shared eligibility helpers.

Examples:
  * ``chat``        → needs ``text``
  * ``ocr``         → needs ``vision``
  * ``transcription`` → needs ``stt``
  * ``embed``       → needs ``embeddings``
  * ``decision_*``  → needs ``decision`` (never falls back to ``text``)
  * every other text-generation task (define_*, magic_fill, …) → ``text``
"""

from __future__ import annotations

from collections.abc import Iterable

from app.ai.providers.task_registry import required_capability
from app.models.enums import AIModelCapability


def required_capabilities_for_task(task_type: object) -> set[AIModelCapability]:
    """Return the set of capabilities a model must have to serve ``task_type``.

    Derived from the task registry — there is no hand-maintained map. Unknown
    task types (a stray DB row, a caller spelling a task wrong) fall back to
    the text baseline, matching the historical tolerant behaviour.
    """
    capability = required_capability(None if task_type is None else str(task_type))
    if capability is None:
        return {AIModelCapability.TEXT}
    return {AIModelCapability(capability)}


def normalize_capabilities(values: Iterable[object] | None) -> set[str]:
    """Coerce a raw capabilities payload (JSONB list of strings/enum) into a
    clean set of lowercase capability strings.

    ``text`` is the default modality (an empty/null payload falls back to
    ``{"text"}``) but is NOT forced when the payload already declares other
    capabilities — an STT-only model like ``whisper-1`` legitimately carries
    only ``stt``.
    """
    if not values:
        return {AIModelCapability.TEXT.value}
    result: set[str] = set()
    for v in values:
        cap = AIModelCapability.from_string(str(v))
        if cap is not None:
            result.add(cap.value)
    # An explicitly-empty list (e.g. all values were invalid) → text baseline.
    return result or {AIModelCapability.TEXT.value}


def model_supports(
    capabilities: Iterable[object] | None, required: Iterable[AIModelCapability]
) -> bool:
    """True when a model's capability set covers ALL ``required`` capabilities."""
    have = normalize_capabilities(capabilities)
    return all(c.value in have for c in required)
