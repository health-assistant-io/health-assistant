"""Non-LLM engine tasks — the honest home for RxNorm / analytics work.

Plan 4 W4 removed ``medication_interaction`` and ``anomaly_detection`` from the
AI task vocabulary: neither is a model call. Medication interaction checking is
an RxNorm lookup (``app.services.clinical.medication_interactor``) and anomaly detection
is the analytics service (``app.services.analytics.…``); both run as
Celery background jobs (``app.workers.ai_tasks``). They have no provider, no
model, no prompt, no fallback chain — so they must not appear in
``ai_task_defs``, on the AI-settings task surface, or in ``ai_call_logs``.

This registry keeps them first-class (named, discoverable, test-pinned) without
pretending they are AI tasks. Adding an engine task = one entry here; there is
no capability or assignment vocabulary to extend.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class EngineKind(enum.StrEnum):
    """Which non-LLM engine performs the work."""

    RXNORM = "rxnorm"
    ANALYTICS = "analytics"


@dataclass(frozen=True)
class EngineTaskDef:
    """One non-LLM task.

    ``entrypoint`` is ``module:callable`` (import-checked by the registry test),
    ``trigger`` follows the family taxonomy, and ``summary`` is the plain-English
    description a doc or a debug surface can render — deliberately not an i18n
    key: no product UI claims model transparency for these.
    """

    id: str
    engine: EngineKind
    entrypoint: str
    trigger: str
    summary: str


ENGINE_TASKS: tuple[EngineTaskDef, ...] = (
    EngineTaskDef(
        id="medication_interaction",
        engine=EngineKind.RXNORM,
        entrypoint="app.services.clinical.medication_interactor:MedicationInteractor.check_interactions",
        trigger="background",
        summary="Checks pairs of medications for known interactions via RxNorm (no model).",
    ),
    EngineTaskDef(
        id="anomaly_detection",
        engine=EngineKind.ANALYTICS,
        entrypoint="app.services.analytics.insights:get_biomarker_anomalies",
        trigger="background",
        summary="Flags biomarker values outside a patient's expected range (analytics, no model).",
    ),
)

ENGINE_TASK_IDS: tuple[str, ...] = tuple(task.id for task in ENGINE_TASKS)

ENGINE_TASK_DEFS: dict[str, EngineTaskDef] = {task.id: task for task in ENGINE_TASKS}


def get_engine_task(task_id: str | None) -> EngineTaskDef | None:
    """Return the engine task for ``task_id`` (``None`` when not one)."""
    if not task_id:
        return None
    return ENGINE_TASK_DEFS.get(str(task_id))
