"""Agentic-chat tools package.

Public entry point: :func:`get_tools`. Importing this package registers every
domain tool factory via the ``@register_chat_tool`` decorator (the domain
modules are imported below for their registration side effect).
"""

from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# Importing the domain modules registers their factories as a side effect.
# Kept as explicit re-imports (no re-exports) — callers use ``get_tools``.
from app.ai.tools import (  # noqa: F401
    biomarkers,
    clinical_events,
    documents,
    examinations,
    hitl_proposals,
    medications,
    patient,
    system,
    taxonomy,
)
from app.ai.tools.registry import ToolContext, get_factories


def get_tools(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: UUID,
    examination_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
) -> List[Any]:
    """Return the full set of built-in chatbot tools bound to the given context.

    Each registered domain factory is invoked with a :class:`ToolContext` and
    its closure-bound tools are concatenated. Replaces the old
    ``ChatbotTools(db, tenant_id, patient_id, examination_id).get_tools()``.
    """
    ctx = ToolContext(
        db=db,
        tenant_id=tenant_id,
        patient_id=patient_id,
        examination_id=examination_id,
        user_id=user_id,
    )
    tools: List[Any] = []
    for factory in get_factories().values():
        tools.extend(factory(ctx))
    return tools


__all__ = ["get_tools", "ToolContext"]
