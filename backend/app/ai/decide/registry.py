from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.enums import (
    DecisionCapability,
    DecisionTransport,
    TaskType,
)
from app.ai.providers.resolution import (
    ResolvedAssignment,
    assignment_candidates,
    resolve_task,
)
from app.models.ai_provider_model import (
    AIModel,
    AIProviderModel,
    AITaskAssignment,
)
from app.models.enums import AIModelCapability

from .systemone.client import (
    KEYLESS_SYSTEMONE_API_KEY,
    SystemOneClient,
    preset_is_keyless,
    resolve_systemone_base_url,
)
from .systemone.engine import SystemOneDecisionEngine, SystemOneEngineParams
from .systemone.probe import DecisionProbeInput, DecisionProbeResult, probe_decision_provider
from .types import (
    DECISION_SLOTS,
    PHI_EXCLUDED_CODE,
    SYSTEMONE_DECISION_CAPS,
    DecisionEngine,
    decision_task_type,
)


@dataclass
class DecisionEngineDeps:
    db: AsyncSession
    tenant_id: UUID | None = None
    user_id: UUID | None = None
    get_api_key: Callable[[AIProviderModel], str | None] | None = None
    config_revision: Callable[..., Any] | None = None
    observe_resolved_model: Callable[[str, str], None] | None = None
    client: SystemOneClient | None = None
    transport: Any = None

    def api_key_of(self, provider: AIProviderModel) -> str | None:
        if self.get_api_key is not None:
            return self.get_api_key(provider)
        return provider.get_api_key_plaintext()


@dataclass(frozen=True)
class OffResolution:
    kind = "off"


@dataclass(frozen=True)
class UnconfiguredResolution:
    kind = "unconfigured"
    reason: str


@dataclass(frozen=True)
class UnavailableResolution:
    kind = "unavailable"
    reason: str
    code: str | None = None
    provider_id: UUID | None = None


@dataclass(frozen=True)
class SystemOneResolution:
    kind = "systemone"
    provider: AIProviderModel
    model: AIModel
    api_key: str
    base_url: str


DecisionResolution = (
    OffResolution | UnconfiguredResolution | UnavailableResolution | SystemOneResolution
)


@dataclass(frozen=True)
class AuditMeta:
    provider_id: UUID | None
    model: str
    model_id: UUID | None = None


@dataclass(frozen=True)
class DecisionTransportRegistration:
    transport: DecisionTransport
    capabilities: frozenset
    display_name: str
    resolve_candidate: Any
    create: Any
    audit: Any
    probe: Callable[[DecisionProbeInput], DecisionProbeResult]


def _resolve_systemone_candidate(
    provider: AIProviderModel, model: AIModel, deps: DecisionEngineDeps
) -> DecisionResolution:
    base_url = resolve_systemone_base_url(provider, model)
    if base_url is None:
        return UnavailableResolution(reason="The provider base URL is missing or invalid.")
    api_key = deps.api_key_of(provider)
    if not api_key:
        if not preset_is_keyless(provider.preset_key):
            return UnavailableResolution(
                reason="No API key is set for the decision provider.",
                code="needs-key",
                provider_id=provider.id,
            )
        api_key = KEYLESS_SYSTEMONE_API_KEY
    return SystemOneResolution(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def _create_systemone(resolution: DecisionResolution, deps: DecisionEngineDeps) -> DecisionEngine:
    if not isinstance(resolution, SystemOneResolution):
        raise ValueError("systemone registration received a non-systemone resolution")
    return SystemOneDecisionEngine(
        SystemOneEngineParams(
            api_key=resolution.api_key,
            base_url=resolution.base_url,
            model=resolution.model.model_name,
            client=deps.client,
            transport=deps.transport,
        )
    )


def _audit_systemone(resolution: DecisionResolution) -> AuditMeta | None:
    if not isinstance(resolution, SystemOneResolution):
        return None
    return AuditMeta(
        provider_id=resolution.provider.id,
        model=resolution.model.model_name,
        model_id=resolution.model.id,
    )


SYSTEMONE_REGISTRATION = DecisionTransportRegistration(
    transport=DecisionTransport.SYSTEMONE,
    capabilities=frozenset(SYSTEMONE_DECISION_CAPS),
    display_name="TypeSafe System One",
    resolve_candidate=_resolve_systemone_candidate,
    create=_create_systemone,
    audit=_audit_systemone,
    probe=probe_decision_provider,
)

REGISTRATIONS: dict[DecisionTransport, DecisionTransportRegistration] = {
    DecisionTransport.SYSTEMONE: SYSTEMONE_REGISTRATION,
}


def get_decision_transport_registration(
    transport: DecisionTransport,
) -> DecisionTransportRegistration:
    return REGISTRATIONS[transport]


def decision_transport_capabilities(
    transport: DecisionTransport,
) -> frozenset:
    return REGISTRATIONS[transport].capabilities


def decision_transport_display_name(transport: DecisionTransport) -> str:
    return REGISTRATIONS[transport].display_name


def decision_transport_of(provider: AIProviderModel, model: AIModel) -> DecisionTransport | None:
    if model.decision_transport:
        try:
            return DecisionTransport(model.decision_transport)
        except ValueError:
            return None
    if provider.provider_type == "decision":
        return DecisionTransport.SYSTEMONE
    return None


async def _decision_candidates(
    db: AsyncSession, task_type: str, tenant_id: UUID | None, user_id: UUID | None
) -> list[AITaskAssignment]:
    """Active assignment rows for a decision slot, in resolution order.

    Thin alias over the ONE scope-cascade query in
    :mod:`app.ai.providers.resolution` (plan 4 W2) — ordering, revocation and
    the reachable-scope conditions live there, so config-revision fingerprinting
    and resolution can never disagree about which rows exist.
    """
    return await assignment_candidates(db, task_type, tenant_id, user_id)


#: Public alias — the service summary and the funnel share this resolution.
resolve_decision_candidates = _decision_candidates


async def default_config_revision(
    db: AsyncSession, tenant_id: UUID | None = None, user_id: UUID | None = None
) -> str:
    """Stateless fingerprint of every row resolution reads (cache key).

    Survives health's multi-process web mode without a shared counter; a
    change to any decision slot row (or the model/provider it points at)
    rotates the revision.
    """
    parts: list[str] = []
    # ADR-0036: the `decision` default row participates in the revision —
    # every kind's resolution chain reads it through the same cascade.
    for slot in (TaskType.DECISION.value, *DECISION_SLOTS):
        rows = await _decision_candidates(db, slot, tenant_id, user_id)
        if not rows:
            parts.append(f"{slot}:-")
            continue
        entries = []
        for row in rows:
            if row.model_id is None:
                entries.append("none")
                continue
            model = (
                (await db.execute(select(AIModel).where(AIModel.id == row.model_id)))
                .scalars()
                .first()
            )
            if model is None:
                entries.append("dead")
                continue
            provider = (
                (
                    await db.execute(
                        select(AIProviderModel).where(AIProviderModel.id == model.provider_id)
                    )
                )
                .scalars()
                .first()
            )
            entries.append(
                ":".join(
                    str(value)
                    for value in (
                        row.id,
                        int(row.phi_allowed),
                        model.id,
                        int(model.is_active),
                        model.decision_transport or "-",
                        provider.is_active if provider is not None else "-",
                    )
                )
            )
        parts.append(f"{slot}={','.join(entries)}")
    return ";".join(parts)


_RESOLUTION_CACHE: dict[tuple[str, str, str, str, bool], tuple[str, DecisionResolution]] = {}


def clear_decision_resolution_cache() -> None:
    _RESOLUTION_CACHE.clear()


def _candidate_key(candidate: ResolvedAssignment) -> tuple[str, object, object, str]:
    """Identity of a resolved candidate (row + model + hop)."""
    return (
        candidate.task_id,
        candidate.assignment.id,
        getattr(candidate.model, "id", None),
        candidate.hop.value,
    )


async def _compute_resolution(
    capability: DecisionCapability,
    db: AsyncSession,
    deps: DecisionEngineDeps,
    *,
    phi_adjacent: bool,
) -> DecisionResolution:
    """Resolve a decision capability through the ONE precedence chain.

    Precedence (plan 4 W2 / ADR-0036 / ADR-0037): specialist kind primary →
    specialist kind fallback → decision capability default → unavailable. An
    EMPTY kind slot inherits the ``decision`` default; a configured-but-unusable
    one is reported honestly and never silently replaced. Scope cascade, active
    rows (``revoked_at IS NULL``) and ordering all come from
    :func:`app.ai.providers.resolution.resolve_task`; this function only supplies
    the decision-specific usability predicate (D2b capability routing + D17 PHI
    scope guard) and maps the winner onto a transport resolution.
    """
    task_type = decision_task_type(capability)
    rejections: list[str] = []
    unavailable: list[DecisionResolution] = []
    ready: dict[tuple[str, object, object, str], DecisionResolution] = {}
    phi_blocked = False

    def accept(candidate: ResolvedAssignment) -> str | None:
        nonlocal phi_blocked
        if candidate.model is None:
            return f"The '{candidate.task_id}' slot has no model bound."
        provider = candidate.provider
        if provider is None:
            return f"The '{candidate.task_id}' slot has no provider bound."
        if phi_adjacent and not candidate.assignment.phi_allowed:
            phi_blocked = True
            return "PHI-adjacent dispatch is not enabled for this decision assignment."
        transport = decision_transport_of(provider, candidate.model)
        is_decision_model = AIModelCapability.DECISION.value in candidate.model.get_capabilities()
        if transport is None or not is_decision_model:
            return f"Model '{candidate.model.model_name}' is not a decision model."
        registration = REGISTRATIONS[transport]
        if capability not in registration.capabilities:
            return f"The transport of '{candidate.model.model_name}' does not serve {task_type}."
        resolution = registration.resolve_candidate(provider, candidate.model, deps)
        if isinstance(resolution, UnavailableResolution):
            unavailable.append(resolution)
            return resolution.reason
        ready[_candidate_key(candidate)] = resolution
        return None

    resolved = await resolve_task(
        db,
        task_type,
        deps.tenant_id,
        deps.user_id,
        accept=accept,
        rejections=rejections,
    )
    if resolved is not None and resolved.provider is not None and resolved.model is not None:
        cached = ready.get(_candidate_key(resolved))
        if cached is not None:
            return cached
        transport = decision_transport_of(resolved.provider, resolved.model)
        if transport is not None:
            registration = REGISTRATIONS[transport]
            return registration.resolve_candidate(resolved.provider, resolved.model, deps)
        return UnavailableResolution(reason="unresolvable decision model")

    if phi_blocked:
        return UnavailableResolution(
            reason=(
                "PHI-adjacent dispatch is not enabled for the assigned decision "
                "models. Opt in explicitly on the decision assignment (D17)."
            ),
            code=PHI_EXCLUDED_CODE,
        )
    if unavailable:
        return unavailable[0]
    if not rejections:
        return OffResolution()
    return UnconfiguredResolution(reason=rejections[0])


async def resolve_decision(
    capability: DecisionCapability,
    db: AsyncSession,
    deps: DecisionEngineDeps | None = None,
    *,
    phi_adjacent: bool = False,
) -> DecisionResolution:
    deps = deps or DecisionEngineDeps(db=db)
    revision_source = deps.config_revision or default_config_revision
    revision = await revision_source(db, deps.tenant_id, deps.user_id)
    cache_key = (
        capability.value,
        str(deps.tenant_id),
        str(deps.user_id),
        revision,
        phi_adjacent,
    )
    cached = _RESOLUTION_CACHE.get(cache_key)
    if cached is not None:
        return cached[1]
    resolution = await _compute_resolution(capability, db, deps, phi_adjacent=phi_adjacent)
    _RESOLUTION_CACHE[cache_key] = (revision, resolution)
    return resolution


def engine_readiness(resolution: DecisionResolution) -> dict[str, str | None]:
    if isinstance(resolution, SystemOneResolution):
        return {"state": "ready", "reason": None}
    if isinstance(resolution, OffResolution):
        return {"state": "unavailable", "reason": "No decision model is assigned."}
    if isinstance(resolution, UnavailableResolution) and resolution.code == "needs-key":
        return {"state": "needs-key", "reason": f"provider:{resolution.provider_id or ''}"}
    if isinstance(resolution, UnavailableResolution):
        return {"state": "unavailable", "reason": resolution.reason}
    assert isinstance(resolution, UnconfiguredResolution)
    return {"state": "unavailable", "reason": resolution.reason}
