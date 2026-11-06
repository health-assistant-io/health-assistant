"""The decision funnel: resolve → create engine → decide → audit (ADR-0032).

Health adaptation of study's Phase 3 reference: async (health's stack),
``phi_adjacent`` runs the D17 PHI scope guard (a PHI-adjacent dispatch resolves
ONLY assignments that carry the explicit ``phi_allowed`` opt-in; otherwise the
point does not run — fail-open, ADR-0020 §6), and every invocation writes one
``ai_call_logs`` row (plan 4 W3 — the structured log line stays as a live
diagnostic, the table is the durable audit).
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.ai.call_audit import CallAuditContext, record_call
from app.ai.providers.enums import DecisionTransport
from app.models.enums import AICallStatus

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
    decision_task_type,
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
    """Funnel-level deps.

    The audit write opens its own short-lived session
    (:func:`app.ai.call_audit.record_call`) so a decision row lands even when the
    caller's transaction rolls back — the same fail-soft seam the chat gateway
    uses.
    """


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


async def _record_decision_call(
    deps: DecisionFunnelDeps,
    task_id: str,
    meta: AuditMeta,
    outcome: DecisionOutcome | None,
    started_at: datetime,
    latency_ms: int,
    *,
    error: BaseException | None = None,
) -> None:
    """Write the durable ``ai_call_logs`` row for one decision invocation."""
    from app.ai.call_audit import classify_error

    usage = outcome.usage if outcome is not None and outcome.usage else None
    if error is None:
        status, error_class = AICallStatus.OK, None
    else:
        status, error_class = classify_error(error)
    await record_call(
        CallAuditContext(
            task_id=task_id,
            tenant_id=deps.tenant_id,
            user_id=deps.user_id,
            provider_id=meta.provider_id,
            model_id=meta.model_id,
            model_name=meta.model,
        ),
        started_at=started_at,
        latency_ms=latency_ms,
        status=status,
        prompt_tokens=usage.input_tokens if usage else None,
        completion_tokens=usage.output_tokens if usage else None,
        error_class=error_class[:80] if error_class else None,
        meta={"resolved_model": outcome.resolved_model} if outcome is not None else None,
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
    started_at = datetime.now(UTC)
    started_at_mono = time.monotonic()
    try:
        engine = registration.create(resolution, deps)
        outcome = await asyncio.to_thread(engine.decide, request)
    except Exception as error:
        latency_ms = int((time.monotonic() - started_at_mono) * 1000)
        _audit_decision(meta, None, latency_ms, failed=True, reason=str(error))
        await _record_decision_call(
            deps,
            decision_task_type(capability),
            meta,
            None,
            started_at,
            latency_ms,
            error=error,
        )
        return DecisionErrorStatus(reason=str(error)[:500], transport=transport.value)
    latency_ms = int((time.monotonic() - started_at_mono) * 1000)
    _audit_decision(meta, outcome, latency_ms, failed=False)
    await _record_decision_call(
        deps, decision_task_type(capability), meta, outcome, started_at, latency_ms
    )
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
