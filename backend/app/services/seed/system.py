"""System vocabulary seeding (AI task defs, proposal kinds, retention policies).

These three stages are **app-only** (never pack items): their content is
code/contract-owned (the TaskDef registry, the family proposal-kind
contract, the retention defaults), so they take no pack items — only the
required session. They flush and never commit; the caller owns the
transaction (ADR 0056).
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SystemSeedMixin:
    async def seed_ai_task_defs(self, session: AsyncSession) -> dict[str, int]:
        """Upsert the AI task vocabulary from the one TaskDef registry.

        Plan 4 W2: ``ai_task_defs`` is an *extensible domain vocabulary* seeded
        as data (constitution rule 7b). A new AI task is a registry entry, never
        a migration; this stage keeps label keys and enablement honest on every
        boot. The caller owns the session and the transaction.
        """
        from app.ai.providers.task_defs import seed_task_defs

        if not await self._table_exists(session, "ai_task_defs"):
            logger.error("Table 'ai_task_defs' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
        return await seed_task_defs(session)

    async def seed_proposal_kinds(self, session: AsyncSession) -> dict[str, int]:
        """Upsert the HITL proposal-kind vocabulary from the family contract.

        Plan 5 W1: ``proposal_kinds`` is an *extensible domain vocabulary*
        seeded as data (constitution rule 7b). A new kind is a contract entry in
        ``hitl-proposal-kinds.json``, never a migration; this stage keeps
        tier/schema/resolver/card-variant honest on every boot. The caller owns
        the session and the transaction.
        """
        from app.extensions.proposal_kinds import seed_proposal_kinds

        if not await self._table_exists(session, "proposal_kinds"):
            logger.error("Table 'proposal_kinds' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
        return await seed_proposal_kinds(session)

    async def seed_retention_policies(self, session: AsyncSession) -> dict[str, int]:
        """Insert missing retention-policy rows (operator edits win).

        Plan 6 W3: retention windows are data. The defaults live in
        ``app.services.data_lifecycle.retention_policies``; this stage only inserts rows that
        do not exist yet, so an operator's ``days``/``enabled`` edit survives
        every boot. The caller owns the session and the transaction.
        """
        from app.services.data_lifecycle.retention_policies import seed_retention_policies

        if not await self._table_exists(session, "retention_policies"):
            logger.error("Table 'retention_policies' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
        return await seed_retention_policies(session)
