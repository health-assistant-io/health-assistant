"""Aggregate tools from all integrations that expose them, for the chat.

Called from :mod:`app.ai.assistance.service` when a patient context
is active. Discovers the user's active integrations, asks each integration's
provider (via the SDK ``supports_tools`` / ``get_tools`` contract) for its
LangChain tools, and merges them with the built-in ``ChatbotTools``.

This module is **domain-agnostic**: it does not import or reference any
specific integration. Any integration whose provider returns
``supports_tools() == True`` and implements ``get_tools()`` is picked up
automatically.

Design goals:
- **Per-patient scoping (v1)**: only integrations bound to
  ``(user_id, patient_id)`` in the current tenant are loaded.
- **Error isolation**: if one integration fails to produce tools, it is
  skipped — the chat continues with the remaining integrations and
  built-ins.
- **Caps**: ``INTEGRATION_MAX_TOOLS_PER_SESSION`` bounds the total number of
  integration tools exposed in one chat turn.
"""

from __future__ import annotations

import logging
from typing import Any, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.integration_registry import integration_registry
from app.models.user_integration import UserIntegration
from app.models.enums import IntegrationStatus

logger = logging.getLogger(__name__)


async def aggregate(
    db: AsyncSession,
    user_id: UUID,
    tenant_id: UUID,
    patient_id: UUID,
) -> List[Any]:
    """Return LangChain tools from all tool-exposing integrations for this context.

    Returns an empty list if no integrations expose tools, none are active,
    or every one fails (so the chat can continue with built-in tools).
    """
    stmt = (
        select(UserIntegration)
        .where(
            UserIntegration.status == IntegrationStatus.ACTIVE,
            UserIntegration.user_id == user_id,
            UserIntegration.patient_id == patient_id,
            UserIntegration.tenant_id == tenant_id,
        )
        .order_by(UserIntegration.created_at)
    )
    try:
        result = await db.execute(stmt)
    except Exception as e:
        logger.warning(f"Integration tool aggregator DB query failed: {e}")
        return []
    integrations = result.scalars().all()
    if not integrations:
        return []

    cap = settings.INTEGRATION_MAX_TOOLS_PER_SESSION
    all_tools: List[Any] = []

    for integration in integrations:
        if len(all_tools) >= cap:
            logger.info(
                f"Integration tool cap ({cap}) reached; skipping remaining instances."
            )
            break
        provider = integration_registry.get_provider(integration.provider)
        if provider is None or not provider.supports_tools():
            continue
        try:
            tools = await provider.get_tools(integration)
        except Exception as e:
            logger.warning(
                f"Skipping tools from {integration.provider}/{integration.id} "
                f"({integration.instance_name}): {e}"
            )
            continue
        remaining = max(0, cap - len(all_tools))
        all_tools.extend(tools[:remaining])

    if all_tools:
        logger.info(
            f"Integration tool aggregator: {len(all_tools)} tool(s) exposed "
            f"for user={user_id} patient={patient_id}"
        )
    return all_tools
