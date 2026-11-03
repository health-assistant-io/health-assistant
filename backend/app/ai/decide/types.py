"""Decision funnel vocabulary (plan 26 Phase 5, ported from study's Phase 3
Python reference — per-repo copy, drift-guarded by the conformance suite).

Health deltas: the family ``decision:<cap>`` slots are stored as
``AITaskAssignment.task_type`` values (``decision_tool_dispatch`` &c., §3
contract table) with ``priority``-ordered fallback across the scope cascade;
resolution NEVER falls back to the ``default`` task (D4 slot separation).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.ai.providers.enums import (
    DecisionCapability as DecisionCapability,
)
from app.ai.providers.enums import (
    DecisionTransport as DecisionTransport,
)
from app.ai.providers.enums import (
    SystemOneErrorKind as SystemOneErrorKind,
)
from app.ai.providers.enums import TaskType

SYSTEMONE_DECISION_CAPS: tuple[DecisionCapability, ...] = (
    DecisionCapability.TOOL_DISPATCH,
    DecisionCapability.BOOLEAN_GATE,
    DecisionCapability.CHOICE,
    DecisionCapability.SCORE,
)

DECISION_SLOTS: tuple[str, ...] = (
    TaskType.DECISION_TOOL_DISPATCH.value,
    TaskType.DECISION_BOOLEAN_GATE.value,
    TaskType.DECISION_CHOICE.value,
    TaskType.DECISION_SCORE.value,
)

#: ``open-args`` is vocabulary-only (D2b): reserved, never offered, never
#: assignable until a transport declares it.
RESERVED_DECISION_SLOTS: tuple[str, ...] = ("decision_open_args",)

DECISION_ACT_THRESHOLD_DEFAULT = 0.85
DECISION_CONFIRM_THRESHOLD_DEFAULT = 0.5

DECISION_PROBE_TIMEOUT_SECONDS = 10.0

DECISION_AUDIT_TASK = "decision"

#: PHI scope guard code (D17): the resolved assignment does not carry the
#: explicit PHI opt-in for a PHI-adjacent dispatch.
PHI_EXCLUDED_CODE = "phi-excluded"


def decision_task_type(capability: DecisionCapability) -> str:
    """The health task_type storing a decision capability's slot."""
    return {
        DecisionCapability.TOOL_DISPATCH: TaskType.DECISION_TOOL_DISPATCH.value,
        DecisionCapability.BOOLEAN_GATE: TaskType.DECISION_BOOLEAN_GATE.value,
        DecisionCapability.CHOICE: TaskType.DECISION_CHOICE.value,
        DecisionCapability.SCORE: TaskType.DECISION_SCORE.value,
    }[capability]


def is_decision_slot(value: str) -> bool:
    return value in DECISION_SLOTS


def decision_slot_capability(task_type: str) -> DecisionCapability | None:
    """The capability a decision task_type serves (``None`` for anything
    else, including the reserved ``decision_open_args``)."""
    return {
        TaskType.DECISION_TOOL_DISPATCH.value: DecisionCapability.TOOL_DISPATCH,
        TaskType.DECISION_BOOLEAN_GATE.value: DecisionCapability.BOOLEAN_GATE,
        TaskType.DECISION_CHOICE.value: DecisionCapability.CHOICE,
        TaskType.DECISION_SCORE.value: DecisionCapability.SCORE,
    }.get(task_type)


def transport_decision_caps(transport: str | None) -> frozenset[DecisionCapability]:
    if transport == DecisionTransport.SYSTEMONE.value:
        return frozenset(SYSTEMONE_DECISION_CAPS)
    return frozenset()


def capability_for_questions(
    questions: "list[DecisionQuestion]",
) -> DecisionCapability:
    if any(question.type == DecisionCapability.CHOICE for question in questions):
        return DecisionCapability.CHOICE
    if any(question.type == DecisionCapability.SCORE for question in questions):
        return DecisionCapability.SCORE
    return DecisionCapability.BOOLEAN_GATE


def sanitize_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return min(1.0, max(0.0, number))


def decision_band(
    confidence: float,
    act_threshold: float = DECISION_ACT_THRESHOLD_DEFAULT,
    confirm_threshold: float = DECISION_CONFIRM_THRESHOLD_DEFAULT,
) -> str:
    if confidence >= act_threshold:
        return "act"
    if confidence >= confirm_threshold:
        return "confirm"
    return "refuse"


@dataclass(frozen=True)
class DecisionQuestion:
    id: str
    type: DecisionCapability
    instructions: str
    options: Mapping[str, str | None] | None = None
    levels: tuple[str, ...] | None = None
    criteria: tuple[str, str] | None = None


@dataclass(frozen=True)
class DecisionAnswer:
    type: DecisionCapability
    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    legend: dict[str, str] | None = None
    probabilities: dict[str, float] | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class DecisionToolSchema:
    name: str
    description: str
    parameters: dict[str, Any] | None = None
    app_id: str | None = None


@dataclass(frozen=True)
class DecisionCall:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class DecisionOutcome:
    engine: str
    calls: tuple[DecisionCall, ...] = ()
    confidence: float = 0.0
    reasoning: str | None = None
    answers: dict[str, DecisionAnswer] | None = None
    usage: DecisionUsage | None = None
    resolved_model: str | None = None


@dataclass(frozen=True)
class DecisionRequest:
    input: str
    tools: tuple[DecisionToolSchema, ...] = ()
    questions: tuple[DecisionQuestion, ...] = ()
    system_prompt: str | None = None
    catalog_entities: Mapping[str, tuple[str, ...]] | None = None


class DecisionEngine(Protocol):
    transport: DecisionTransport

    def decide(self, request: DecisionRequest) -> DecisionOutcome: ...
