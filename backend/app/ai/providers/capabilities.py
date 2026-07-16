"""Map AI tasks to the model capabilities they require.

A model advertises a SET of capabilities (``AIModelCapability``: text / vision
/ audio_input) on its JSONB ``capabilities`` column. Each task type needs at
least one specific capability; this module is the single source of truth for
that mapping so the task-assignment picker only offers eligible models and the
runtime factories can sanity-check their resolved model.

Examples:
  * ``chat``        → needs ``text``
  * ``ocr``         → needs ``vision``
  * ``transcription`` → needs ``audio_input``
  * every other text-generation task (define_*, magic_fill, …) → ``text``
"""

from __future__ import annotations

from typing import Iterable, Optional, Set

from app.ai.providers.enums import TaskType
from app.models.enums import AIModelCapability


# The capability each task type REQUIRES (a model must advertise it to be
# eligible). Tasks not listed default to {TEXT} (the baseline modality every
# model carries). Kept as TaskType keys so renames are caught at import time.
TASK_REQUIRED_CAPABILITY: dict = {
    TaskType.CHAT: {AIModelCapability.TEXT},
    TaskType.OCR: {AIModelCapability.VISION},
    TaskType.TRANSCRIPTION: {AIModelCapability.AUDIO_INPUT},
}


def required_capabilities_for_task(task_type: object) -> Set[AIModelCapability]:
    """Return the set of capabilities a model must have to serve ``task_type``.

    Unknown/unmapped task types (the long tail of text-generation tasks) fall
    back to ``{TEXT}``.
    """
    key = TaskType.from_string(str(task_type)) if task_type is not None else None
    return set(TASK_REQUIRED_CAPABILITY.get(key, {AIModelCapability.TEXT}))


def normalize_capabilities(values: Optional[Iterable[object]]) -> Set[str]:
    """Coerce a raw capabilities payload (JSONB list of strings/enum) into a
    clean set of lowercase capability strings.

    ``text`` is the default modality (an empty/null payload falls back to
    ``{"text"}``) but is NOT forced when the payload already declares other
    capabilities — an STT-only model like ``whisper-1`` legitimately carries
    only ``audio_input``.
    """
    if not values:
        return {AIModelCapability.TEXT.value}
    result: Set[str] = set()
    for v in values:
        cap = AIModelCapability.from_string(str(v))
        if cap is not None:
            result.add(cap.value)
    # An explicitly-empty list (e.g. all values were invalid) → text baseline.
    return result or {AIModelCapability.TEXT.value}


def model_supports(
    capabilities: Optional[Iterable[object]], required: Iterable[AIModelCapability]
) -> bool:
    """True when a model's capability set covers ALL ``required`` capabilities."""
    have = normalize_capabilities(capabilities)
    return all(c.value in have for c in required)
