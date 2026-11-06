"""The one AI task registry (plan 4 §2.1).

Every AI task is declared exactly once, here, as a :class:`TaskDef`. Everything
else is derived from this tuple and never hand-maintained:

* :class:`app.ai.providers.enums.TaskType` — generated into
  ``app/ai/providers/task_types.py`` by ``scripts/sync-task-registry.mjs``
  (the import path ``app.ai.providers.enums.TaskType`` is stable);
* ``TASK_REQUIRED_CAPABILITY`` / the setup slot map — derived functions in
  :mod:`app.ai.providers.capabilities` and :mod:`app.ai.providers.setup`;
* ``frontend/src/generated/taskRegistry.ts`` — the same generator, gated by
  ``scripts/check-task-registry.sh`` (drift gate: a hand-edited generated file
  or a registry edit that was never regenerated turns CI red);
* ``ai_task_defs`` rows — seeded at boot from this registry
  (:mod:`app.ai.providers.task_defs`); the table is a lookup, so a new task is
  a row + code, never a migration (constitution rule 7b).

Adding a task = one :class:`TaskDef` entry + its label values + regenerating.
The completeness wall (``tests/test_ai_task_registry.py``) asserts every task
has a label key in every shipped locale, a capability that has a declared
capability-default task (so resolution always terminates at a provider or an
honest "unavailable"), and a row in the generated TypeScript.

This module deliberately imports nothing from ``app`` — the generator loads it
by path (no application settings, no DB, no package side effects).

Taxonomy (plan 4 W4): ``medication_interaction`` and ``anomaly_detection`` are
NOT AI tasks. They are engine tasks (RxNorm / analytics workers) and live in
:mod:`app.services.engine.engine_task_registry`; they must never reappear here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class TaskTrigger(enum.StrEnum):
    """How a task is executed (family trigger taxonomy)."""

    SYNC = "sync"
    STREAM = "stream"
    BACKGROUND = "background"


class TaskPhiClass(enum.StrEnum):
    """Whether a task's prompt may carry PHI (D17 guardrail input)."""

    NONE = "none"
    PHI = "phi"


class TaskRoute(enum.StrEnum):
    """The call shape recorded on ``ai_call_logs.route`` (closed vocabulary).

    Mirrors :class:`app.models.enums.AICallRoute`; declared here as plain
    strings so this module stays import-free for the generator.
    """

    CHAT = "chat"
    DECISION = "decision"
    EMBED = "embed"
    STT = "stt"
    OCR = "ocr"
    EXTRACT = "extract"


@dataclass(frozen=True)
class TaskDef:
    """One AI task.

    ``capability`` is the model capability a provider/model must advertise to
    serve the task (the §15 vocabulary: ``text | vision | stt | embeddings |
    decision``). ``capability_default`` marks the ONE task that is a capability
    default (§15 defaults: ``default`` = text, ``ocr`` = vision,
    ``transcription`` = stt, ``embed`` = embeddings, ``decision`` = decision).

    ``accepts_priority`` is part of the registry on purpose: the single-slot
    law (ADR-0037) dropped ``priority`` from the schema and the API, so every
    task declares ``False``. Re-introducing an ordering knob means editing this
    field and the law test that pins the whole column to ``False``.

    ``route`` is what a call for the task records in ``ai_call_logs.route``;
    ``trigger`` says how the product runs it; ``phi_class`` says whether its
    prompts may carry PHI.
    """

    id: str
    label_key: str
    capability: str
    trigger: TaskTrigger
    phi_class: TaskPhiClass
    accepts_priority: bool
    decision_kinds: tuple[str, ...] = ()
    enabled: bool = True
    route: TaskRoute = TaskRoute.CHAT
    capability_default: bool = False


def _label(task_id: str) -> str:
    """The uniform i18n key for a task label (one convention, no exceptions)."""
    return f"settings.ai.task_{task_id}"


_DECISION_KINDS = ("tool-dispatch", "boolean-gate", "choice", "score")

TASKS: tuple[TaskDef, ...] = (
    TaskDef(
        id="default",
        label_key=_label("default"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        capability_default=True,
    ),
    TaskDef(
        id="ocr",
        label_key=_label("ocr"),
        capability="vision",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        route=TaskRoute.OCR,
        capability_default=True,
    ),
    TaskDef(
        id="nlp",
        label_key=_label("nlp"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        route=TaskRoute.EXTRACT,
    ),
    TaskDef(
        id="fill_biomarker_form",
        label_key=_label("fill_biomarker_form"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        route=TaskRoute.EXTRACT,
    ),
    TaskDef(
        id="fill_medication_form",
        label_key=_label("fill_medication_form"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        route=TaskRoute.EXTRACT,
    ),
    TaskDef(
        id="magic_fill_examination",
        label_key=_label("magic_fill_examination"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        route=TaskRoute.EXTRACT,
    ),
    TaskDef(
        id="define_biomarker",
        label_key=_label("define_biomarker"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
    ),
    TaskDef(
        id="define_medication",
        label_key=_label("define_medication"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
    ),
    TaskDef(
        id="define_anatomy_graph",
        label_key=_label("define_anatomy_graph"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.NONE,
        accepts_priority=False,
    ),
    TaskDef(
        id="suggest_category_icon",
        label_key=_label("suggest_category_icon"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.NONE,
        accepts_priority=False,
    ),
    TaskDef(
        id="generate_category_icon",
        label_key=_label("generate_category_icon"),
        capability="text",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.NONE,
        accepts_priority=False,
    ),
    TaskDef(
        id="chat",
        label_key=_label("chat"),
        capability="text",
        trigger=TaskTrigger.STREAM,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
    ),
    TaskDef(
        id="transcription",
        label_key=_label("transcription"),
        capability="stt",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        route=TaskRoute.STT,
        capability_default=True,
    ),
    TaskDef(
        id="embed",
        label_key=_label("embed"),
        capability="embeddings",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        route=TaskRoute.EMBED,
        capability_default=True,
    ),
    TaskDef(
        id="decision_tool_dispatch",
        label_key=_label("decision_tool_dispatch"),
        capability="decision",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        decision_kinds=("tool-dispatch",),
        route=TaskRoute.DECISION,
    ),
    TaskDef(
        id="decision_boolean_gate",
        label_key=_label("decision_boolean_gate"),
        capability="decision",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        decision_kinds=("boolean-gate",),
        route=TaskRoute.DECISION,
    ),
    TaskDef(
        id="decision_choice",
        label_key=_label("decision_choice"),
        capability="decision",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        decision_kinds=("choice",),
        route=TaskRoute.DECISION,
    ),
    TaskDef(
        id="decision_score",
        label_key=_label("decision_score"),
        capability="decision",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        decision_kinds=("score",),
        route=TaskRoute.DECISION,
    ),
    TaskDef(
        id="decision",
        label_key=_label("decision"),
        capability="decision",
        trigger=TaskTrigger.SYNC,
        phi_class=TaskPhiClass.PHI,
        accepts_priority=False,
        decision_kinds=_DECISION_KINDS,
        route=TaskRoute.DECISION,
        capability_default=True,
    ),
)

#: Registry ids in declaration order (``default`` first — it is the text
#: capability default every text task falls back to).
TASK_IDS: tuple[str, ...] = tuple(task.id for task in TASKS)

TASK_DEFS: dict[str, TaskDef] = {task.id: task for task in TASKS}

DECISION_TASK_IDS: tuple[str, ...] = tuple(
    task.id for task in TASKS if task.decision_kinds and not task.capability_default
)

_DECISION_DEFAULT_BY_CAPABILITY: dict[str, TaskDef] = {
    task.capability: task for task in TASKS if task.capability_default
}


def get_task_def(task_id: str | None) -> TaskDef | None:
    """Return the definition for ``task_id`` (``None`` for unknown/missing)."""
    if not task_id:
        return None
    return TASK_DEFS.get(str(task_id))


def capability_default_task(capability: str | None) -> TaskDef | None:
    """The single declared capability-default task for a capability.

    This is the last hop of the resolution chain (specialist primary →
    specialist fallback → capability default → unavailable), so a capability
    without a default is a registry gap, not a runtime surprise.
    """
    if not capability:
        return None
    return _DECISION_DEFAULT_BY_CAPABILITY.get(str(capability))


def required_capability(task_id: str | None) -> str | None:
    """The capability a task requires (``None`` for an unknown task)."""
    task = get_task_def(task_id)
    return task.capability if task is not None else None


def is_decision_task(task_id: str | None) -> bool:
    """True for the four decision kind slots and the decision default."""
    task = get_task_def(task_id)
    return task is not None and task.capability == "decision"


def setup_slot_task_id(slot: str) -> str | None:
    """Map a §15 setup slot (``chat``/``vision``/``stt``/``embeddings``) onto
    the health task that owns it — i.e. the capability default for the slot's
    capability."""
    capability = {
        "chat": "text",
        "vision": "vision",
        "stt": "stt",
        "embeddings": "embeddings",
    }.get(slot)
    task = capability_default_task(capability) if capability else None
    return task.id if task is not None else None


def registry_payload() -> dict:
    """JSON-serialisable projection consumed by ``scripts/sync-task-registry.mjs``.

    The generator loads this module by path and prints
    ``json.dumps(registry_payload())`` — the Python registry stays the source
    of truth; the TypeScript module and the generated enum are projections.
    """
    return {
        "schema": 1,
        "tasks": [
            {
                "id": task.id,
                "labelKey": task.label_key,
                "capability": task.capability,
                "trigger": task.trigger.value,
                "phiClass": task.phi_class.value,
                "acceptsPriority": task.accepts_priority,
                "decisionKinds": list(task.decision_kinds),
                "enabled": task.enabled,
                "route": task.route.value,
                "capabilityDefault": task.capability_default,
            }
            for task in TASKS
        ],
        "taskTypeMembers": {task.id.upper(): task.id for task in TASKS},
        "decisionTaskIds": list(DECISION_TASK_IDS),
        "decisionDefaultId": next(
            (task.id for task in TASKS if task.id == "decision"),
            None,
        ),
    }
