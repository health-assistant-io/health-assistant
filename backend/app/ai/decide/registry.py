from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.enums import (
    DecisionCapability,
    DecisionTransport,
    TaskType,
)
from app.models.ai_provider_model import (
    AIModel,
    AIProviderModel,
    AIScope,
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
    return AuditMeta(provider_id=resolution.provider.id, model=resolution.model.model_name)


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

    Same cascade as ``resolve_active_assignment`` (USER > TENANT > SYSTEM via
    the alphabetical enum-name ordering, then ``priority`` desc) but returns
    EVERY row — the §3 contract's fallback is priority ordering — and NEVER
    falls back to the ``default`` task (D4 slot separation).
    """
    query = select(AITaskAssignment).where(
        AITaskAssignment.task_type == task_type,
        AITaskAssignment.is_active == True,  # noqa: E712 — SQLAlchemy filter
    )
    conditions = [AITaskAssignment.scope == AIScope.SYSTEM]
    if tenant_id:
        conditions.append(
            (AITaskAssignment.scope == AIScope.TENANT) & (AITaskAssignment.tenant_id == tenant_id)
        )
    if user_id:
        conditions.append(
            (AITaskAssignment.scope == AIScope.USER) & (AITaskAssignment.user_id == user_id)
        )
    query = query.where(or_(*conditions)).order_by(
        AITaskAssignment.scope.desc(),
        AITaskAssignment.priority.desc(),
        AITaskAssignment.created_at,
    )
    return list((await db.execute(query)).scalars().all())


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


async def _compute_resolution(
    capability: DecisionCapability,
    db: AsyncSession,
    deps: DecisionEngineDeps,
    *,
    phi_adjacent: bool,
) -> DecisionResolution:
    task_type = decision_task_type(capability)
    candidates = await _decision_candidates(db, task_type, deps.tenant_id, deps.user_id)
    if not candidates:
        # ADR-0036 §2: an empty kind slot inherits the `decision` default
        # slot across the same scope cascade — never a hop onto ``default``
        # (D4 slot separation); the default is single-slot (ADR-0037).
        candidates = await _decision_candidates(
            db, TaskType.DECISION.value, deps.tenant_id, deps.user_id
        )
    if not candidates:
        return OffResolution()
    rejections: list[str] = []
    phi_blocked = False
    last_index = len(candidates) - 1
    for index, candidate in enumerate(candidates):
        if candidate.model_id is None:
            rejections.append(f"The '{task_type}' slot has no model bound.")
            continue
        model = (
            (await db.execute(select(AIModel).where(AIModel.id == candidate.model_id)))
            .scalars()
            .first()
        )
        if model is None:
            rejections.append(f"Model '{candidate.model_id}' is not registered.")
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
        if provider is None:
            rejections.append(f"Model '{model.model_name}' has no provider.")
            continue
        if phi_adjacent and not candidate.phi_allowed:
            phi_blocked = True
            rejections.append("PHI-adjacent dispatch is not enabled for this decision assignment.")
            continue
        transport = decision_transport_of(provider, model)
        is_decision_model = AIModelCapability.DECISION.value in model.get_capabilities()
        if transport is None or not is_decision_model:
            rejections.append(f"Model '{model.model_name}' is not a decision model.")
            continue
        registration = REGISTRATIONS[transport]
        if capability not in registration.capabilities:
            rejections.append(f"The transport of '{model.model_name}' does not serve {task_type}.")
            continue
        resolution = registration.resolve_candidate(provider, model, deps)
        if isinstance(resolution, UnavailableResolution) and index != last_index:
            rejections.append(resolution.reason)
            continue
        return resolution
    if phi_blocked:
        return UnavailableResolution(
            reason=(
                "PHI-adjacent dispatch is not enabled for the assigned decision "
                "models. Opt in explicitly on the decision assignment (D17)."
            ),
            code=PHI_EXCLUDED_CODE,
        )
    return UnconfiguredResolution(
        reason=rejections[0] if rejections else "No decision model is assigned."
    )


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
