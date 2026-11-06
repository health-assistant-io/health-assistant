"""Task-assignment resolution — the ONE precedence implementation (plan 4 W2).

Precedence is fixed and enforced here, nowhere else (ADR-0037):

1. **specialist primary** — an active assignment row for the task itself, most
   specific scope first (USER > TENANT > SYSTEM);
2. **specialist fallback** — the ``fallback_provider_id``/``fallback_model_id``
   pair on that specialist row (columns, never a second row);
3. **capability default** — the task declared as the capability default for the
   task's capability (``default`` for text, ``ocr`` for vision,
   ``transcription`` for stt, ``embed`` for embeddings, ``decision`` for
   decision slots);
4. **unavailable** — ``None``.

A capability default has no fallback hop of its own: a default *is* the
fallback (ADR-0037). "Active" means exactly ``revoked_at IS NULL``; the
database's partial unique index guarantees at most one active row per
``(tenant_id, user_id, task_type_id)``, so the scope cascade is an ordering over
at most three rows.

The alphabetical ``scope.desc()`` trick and the ``priority`` ordering are gone —
scope specificity is an explicit ``CASE`` so adding an :class:`AIScope` member
can no longer silently reorder resolution.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.task_defs import task_def_id
from app.ai.providers.task_registry import (
    TaskDef,
    capability_default_task,
    get_task_def,
)
from app.models.ai_provider_model import AIModel, AIProviderModel, AIScope, AITaskAssignment


class ResolutionHop(enum.StrEnum):
    """Which rung of the precedence chain produced the resolution."""

    PRIMARY = "primary"
    FALLBACK = "fallback"
    DEFAULT = "default"


@dataclass(frozen=True)
class ResolvedAssignment:
    """A resolved provider/model for a task, plus how it was reached.

    ``requested_task_id`` is what the caller asked for; ``task_id`` is the slot
    that actually won (a different id when the capability default served the
    request). ``assignment`` is the row that carries the resolution — for a
    fallback hop that is still the specialist row (the pair lives on it).
    """

    requested_task_id: str
    task_id: str
    hop: ResolutionHop
    assignment: AITaskAssignment
    provider: AIProviderModel | None
    model: AIModel | None


#: Optional usability predicate: returns a human rejection reason, or ``None``
#: when the candidate is usable. The decision funnel supplies one (capability
#: routing + PHI scope guard); the chat path supplies none.
AcceptFn = Callable[[ResolvedAssignment], str | None]


def _scope_specificity():
    """Explicit scope ordering: USER (3) > TENANT (2) > SYSTEM (1)."""
    return case(
        (AITaskAssignment.scope == AIScope.USER, 3),
        (AITaskAssignment.scope == AIScope.TENANT, 2),
        (AITaskAssignment.scope == AIScope.SYSTEM, 1),
        else_=0,
    )


def _reachable_conditions(tenant_id: UUID | None, user_id: UUID | None):
    conditions = [AITaskAssignment.scope == AIScope.SYSTEM]
    if tenant_id:
        conditions.append(
            (AITaskAssignment.scope == AIScope.TENANT) & (AITaskAssignment.tenant_id == tenant_id)
        )
    if user_id:
        conditions.append(
            (AITaskAssignment.scope == AIScope.USER) & (AITaskAssignment.user_id == user_id)
        )
    return or_(*conditions)


async def assignment_candidates(
    db: AsyncSession,
    task_id: str,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
) -> list[AITaskAssignment]:
    """Active rows for a task in resolution order (most specific scope first).

    The window for ``decision/config revision`` fingerprinting and for the
    resolver's own scope cascade — one query shape for both, so they can never
    disagree about which rows exist.
    """
    task = get_task_def(task_id)
    if task is None:
        return []
    query = (
        select(AITaskAssignment)
        .where(
            AITaskAssignment.task_type_id == task_def_id(task.id),
            AITaskAssignment.revoked_at.is_(None),
            _reachable_conditions(tenant_id, user_id),
        )
        .order_by(
            _scope_specificity().desc(),
            AITaskAssignment.created_at,
            AITaskAssignment.id,
        )
    )
    result = await db.execute(query)
    return list(result.scalars().unique().all())


async def _load_pair(
    db: AsyncSession, provider_id: UUID | None, model_id: UUID | None
) -> tuple[AIProviderModel | None, AIModel | None]:
    provider = None
    model = None
    if model_id is not None:
        model = (await db.execute(select(AIModel).where(AIModel.id == model_id))).scalars().first()
    if provider_id is not None:
        provider = (
            (await db.execute(select(AIProviderModel).where(AIProviderModel.id == provider_id)))
            .scalars()
            .first()
        )
    elif model is not None:
        provider = (
            (
                await db.execute(
                    select(AIProviderModel).where(AIProviderModel.id == model.provider_id)
                )
            )
            .scalars()
            .first()
        )
    return provider, model


def _candidate(
    requested_task_id: str,
    task: TaskDef,
    hop: ResolutionHop,
    row: AITaskAssignment,
    provider: AIProviderModel | None,
    model: AIModel | None,
) -> ResolvedAssignment:
    return ResolvedAssignment(
        requested_task_id=requested_task_id,
        task_id=task.id,
        hop=hop,
        assignment=row,
        provider=provider,
        model=model,
    )


async def _resolve_slot(
    db: AsyncSession,
    requested_task_id: str,
    task: TaskDef,
    tenant_id: UUID | None,
    user_id: UUID | None,
    *,
    accept: AcceptFn | None,
    rejections: list[str] | None,
) -> tuple[ResolvedAssignment | None, bool]:
    """Resolve one slot; return ``(candidate, had_rows)``.

    ``had_rows`` is the honest-vs-inherit distinction: an EMPTY slot may inherit
    the capability default, but a configured-yet-unusable slot must be reported
    as unavailable rather than silently replaced (ADR-0036 "no third hop").
    """
    rows = await assignment_candidates(db, task.id, tenant_id, user_id)

    def reject(reason: str) -> None:
        if rejections is not None:
            rejections.append(reason)

    # 1. specialist primary (for a capability default: its own slot)
    for row in rows:
        provider, model = await _load_pair(db, row.provider_id, row.model_id)
        if provider is None:
            reject(f"The '{task.id}' slot has no provider bound.")
            continue
        candidate = _candidate(requested_task_id, task, ResolutionHop.PRIMARY, row, provider, model)
        reason = accept(candidate) if accept is not None else None
        if reason is not None:
            reject(reason)
            continue
        return candidate, True

    # 2. specialist fallback — only specialist rows carry one (ADR-0037)
    if not task.capability_default:
        for row in rows:
            if row.fallback_model_id is None:
                continue
            provider, model = await _load_pair(db, row.fallback_provider_id, row.fallback_model_id)
            if provider is None or model is None:
                reject(f"The '{task.id}' fallback points at a missing provider or model.")
                continue
            candidate = _candidate(
                requested_task_id, task, ResolutionHop.FALLBACK, row, provider, model
            )
            reason = accept(candidate) if accept is not None else None
            if reason is not None:
                reject(reason)
                continue
            return candidate, True
        if rows:
            reject(f"The '{task.id}' slot has no usable fallback configured.")

    return None, bool(rows)


async def resolve_task(
    db: AsyncSession,
    task_id: str,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    *,
    accept: AcceptFn | None = None,
    rejections: list[str] | None = None,
) -> ResolvedAssignment | None:
    """Resolve a task to a provider/model through the full precedence chain.

    Returns ``None`` when nothing is usable ("unavailable") — callers decide
    what that means (the chat gateway falls back to environment settings, the
    decision funnel reports ``off``/``unavailable`` and does not run).

    ``accept`` lets a caller reject unusable candidates *without* owning a
    second precedence implementation (the decision funnel's capability routing +
    PHI guard). ``rejections`` collects human-readable reasons for diagnostics.
    """
    task = get_task_def(task_id)
    if task is None:
        return None

    resolved, had_rows = await _resolve_slot(
        db, task.id, task, tenant_id, user_id, accept=accept, rejections=rejections
    )
    if resolved is not None:
        return resolved
    if had_rows:
        return None

    default_task = capability_default_task(task.capability)
    if default_task is None or default_task.id == task.id:
        return None

    fallback, _ = await _resolve_slot(
        db, task.id, default_task, tenant_id, user_id, accept=accept, rejections=rejections
    )
    if fallback is None:
        return None
    return replace(fallback, hop=ResolutionHop.DEFAULT)
