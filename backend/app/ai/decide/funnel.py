"""The decision funnel: resolve → create engine → decide → audit (ADR-0032).

Health adaptation of study's Phase 3 reference: async (health's stack), the
audit row is a structured log line — health has no per-AI-call table yet
(ledgered in plan 26 §14) — and ``phi_adjacent`` runs the D17 PHI scope
guard: a PHI-adjacent dispatch resolves ONLY assignments that carry the
explicit ``phi_allowed`` opt-in; otherwise the point does not run
(fail-open, ADR-0020 §6).
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from uuid import UUID

from app.ai.providers.enums import DecisionTransport

from .registry import (
    REGISTRATIONS,
    AuditMeta,
    DecisionEngineDeps,
    OffResolution,
    SystemOneResolution,
    UnavailableResolution,
    UnconfiguredResolution,
    resolve_decision,
)
from .types import (
    DECISION_ACT_THRESHOLD_DEFAULT,
    DECISION_AUDIT_TASK,
    DECISION_CONFIRM_THRESHOLD_DEFAULT,
    DecisionCapability,
    DecisionOutcome,
    DecisionQuestion,
    DecisionRequest,
    DecisionToolSchema,
    capability_for_questions,
    decision_band,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecisionOffStatus:
    status = "off"


@dataclass(frozen=True)
class DecisionUnconfiguredStatus:
    status = "unconfigured"
    reason: str


@dataclass(frozen=True)
class DecisionUnavailableStatus:
    status = "unavailable"
    reason: str
    code: str | None = None
    provider_id: UUID | None = None


@dataclass(frozen=True)
class DecisionErrorStatus:
    status = "error"
    reason: str
    transport: str | None = None


@dataclass(frozen=True)
class DecisionDecidedStatus:
    status = "decided"
    band: str
    outcome: DecisionOutcome


DecisionStatus = (
    DecisionOffStatus
    | DecisionUnconfiguredStatus
    | DecisionUnavailableStatus
    | DecisionErrorStatus
    | DecisionDecidedStatus
)


@dataclass(frozen=True)
class RunDecisionParams:
    input: str
    tools: tuple[DecisionToolSchema, ...] = ()
    questions: tuple[DecisionQuestion, ...] = ()
    system_prompt: str | None = None
    catalog_entities: dict[str, tuple[str, ...]] | None = None
    capability: DecisionCapability | None = None
    act_threshold: float = DECISION_ACT_THRESHOLD_DEFAULT
    confirm_threshold: float = DECISION_CONFIRM_THRESHOLD_DEFAULT
    #: D17 PHI scope guard: when True the dispatch is treated as
    #: PHI-adjacent and only assignments with the explicit ``phi_allowed``
    #: opt-in may serve it.
    phi_adjacent: bool = False


@dataclass
class DecisionFunnelDeps(DecisionEngineDeps):
    """Funnel-level deps — extends the registry deps with nothing extra in
    health: the audit record is a structured log line, so there is no
    session factory to route writes through (study's shape kept the field
    for its ``AiInteraction`` writes)."""


def resolution_transport(resolution: object) -> DecisionTransport | None:
    if isinstance(resolution, SystemOneResolution):
        return DecisionTransport.SYSTEMONE
    return None


def _audit_decision(
    meta: AuditMeta,
    outcome: DecisionOutcome | None,
    latency_ms: int,
    *,
    failed: bool,
    reason: str | None = None,
) -> None:
    usage = outcome.usage if outcome is not None and outcome.usage else None
    logger.info(
        "AI decision audit: provider_id=%s model=%s failed=%s",
        meta.provider_id,
        meta.model,
        failed,
        extra={
            "task": DECISION_AUDIT_TASK,
            "provider_id": meta.provider_id,
            "model": meta.model,
            "failed": failed,
            "reason": (reason or "")[:200] if failed else None,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "latency_ms": latency_ms,
            "resolved_model": outcome.resolved_model if outcome is not None else None,
        },
    )


async def run_decision(deps: DecisionFunnelDeps, params: RunDecisionParams) -> DecisionStatus:
    capability = params.capability
    if capability is None:
        if params.questions:
            capability = capability_for_questions(list(params.questions))
        else:
            capability = DecisionCapability.TOOL_DISPATCH
    resolution = await resolve_decision(capability, deps.db, deps, phi_adjacent=params.phi_adjacent)
    if isinstance(resolution, OffResolution):
        return DecisionOffStatus()
    if isinstance(resolution, UnconfiguredResolution):
        return DecisionUnconfiguredStatus(reason=resolution.reason)
    if isinstance(resolution, UnavailableResolution):
        return DecisionUnavailableStatus(
            reason=resolution.reason, code=resolution.code, provider_id=resolution.provider_id
        )
    transport = resolution_transport(resolution)
    if transport is None:
        return DecisionErrorStatus(reason="unresolvable decision model")
    registration = REGISTRATIONS[transport]
    meta = registration.audit(resolution)
    if meta is None:
        return DecisionErrorStatus(reason="no audit meta for decision resolution")
    request = DecisionRequest(
        input=params.input,
        tools=params.tools,
        questions=params.questions,
        system_prompt=params.system_prompt,
        catalog_entities=params.catalog_entities,
    )
    started_at = time.monotonic()
    try:
        engine = registration.create(resolution, deps)
        outcome = await asyncio.to_thread(engine.decide, request)
    except Exception as error:
        latency_ms = int((time.monotonic() - started_at) * 1000)
        _audit_decision(meta, None, latency_ms, failed=True, reason=str(error))
        return DecisionErrorStatus(reason=str(error)[:500], transport=transport.value)
    latency_ms = int((time.monotonic() - started_at) * 1000)
    _audit_decision(meta, outcome, latency_ms, failed=False)
    if (
        outcome.resolved_model
        and outcome.resolved_model != resolution.model.model_name
        and deps.observe_resolved_model is not None
    ):
        with contextlib.suppress(Exception):
            deps.observe_resolved_model(resolution.model.model_name, outcome.resolved_model)
    return DecisionDecidedStatus(
        band=decision_band(outcome.confidence, params.act_threshold, params.confirm_threshold),
        outcome=outcome,
    )
