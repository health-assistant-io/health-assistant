"""Task-assignment resolution logic.

Extracted from ``AIProviderService.get_active_assignment_for_task`` so the
scope/priority/fallback rules can be unit-tested in isolation and reused
without instantiating the full DB-injected service.

Resolution rule (unchanged behaviour):

1. Query ``ai_task_assignments`` rows where ``task_type == X`` and active,
   scoped to SYSTEM, or TENANT+tenant_id, or USER+user_id.
2. Order by ``scope DESC, priority DESC`` and pick the first row.
3. If nothing matches and ``task_type != "default"``, recurse on ``"default"``.

.. note:: The ``scope.desc()`` ordering relies on the alphabetical ordering of
   the enum member names: ``"USER" > "TENANT" > "SYSTEM"``. This is brittle —
   adding an out-of-order scope name (e.g. ``"GLOBAL"``) would silently break
   resolution. The fix is an explicit ``CASE`` ordering, tracked as a TODO in
   the refactor plan; deliberately NOT changed here to keep this phase
   behaviour-equivalent.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_provider_model import AITaskAssignment, AIScope


async def resolve_active_assignment(
    db: AsyncSession,
    task_type: str,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
) -> Optional[AITaskAssignment]:
    """Resolve the active provider/model assignment for a task type.

    Order of specificity: USER > TENANT > SYSTEM (via ``scope.desc()``), then
    by ``priority.desc()``. Falls back to the ``"default"`` task assignment
    when no specific-task assignment matches.
    """
    # 1. Try the specific task.
    query = select(AITaskAssignment).where(
        AITaskAssignment.task_type == task_type,
        AITaskAssignment.is_active == True,  # noqa: E712 — SQLAlchemy filter
    )

    conditions = [AITaskAssignment.scope == AIScope.SYSTEM]
    if tenant_id:
        conditions.append(
            (AITaskAssignment.scope == AIScope.TENANT)
            & (AITaskAssignment.tenant_id == tenant_id)
        )
    if user_id:
        conditions.append(
            (AITaskAssignment.scope == AIScope.USER)
            & (AITaskAssignment.user_id == user_id)
        )
    query = query.where(or_(*conditions))

    # TODO(ai-refactor): replace the alphabetical-desc trick with an explicit
    # CASE-based ordering so adding a new AIScope member cannot silently break
    # resolution priority. Out of scope for this move/split phase.
    query = query.order_by(
        AITaskAssignment.scope.desc(),  # USER > TENANT > SYSTEM (alphabetical)
        AITaskAssignment.priority.desc(),
    )

    result = await db.execute(query)
    assignment = result.scalars().first()
    if assignment:
        return assignment

    # 2. Fall back to the "default" task assignment (recurse once).
    if task_type != "default":
        return await resolve_active_assignment(
            db, "default", tenant_id, user_id
        )

    return None
