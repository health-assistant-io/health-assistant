"""System vocabulary seeding (AI task defs, proposal kinds, retention policies)."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class SystemSeedMixin:
    async def seed_ai_task_defs(self, session: AsyncSession = None) -> dict[str, int]:
        """Upsert the AI task vocabulary from the one TaskDef registry.

        Plan 4 W2: ``ai_task_defs`` is an *extensible domain vocabulary* seeded
        as data (constitution rule 7b). A new AI task is a registry entry, never
        a migration; this stage keeps label keys and enablement honest on every
        boot.
        """
        from app.ai.providers.task_defs import seed_task_defs

        if session is not None:
            if not await self._table_exists(session, "ai_task_defs"):
                logger.error("Table 'ai_task_defs' does not exist. Skipping seeding.")
                return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
            return await seed_task_defs(session)
        async with AsyncSessionLocal() as new_session:
            if not await self._table_exists(new_session, "ai_task_defs"):
                logger.error("Table 'ai_task_defs' does not exist. Skipping seeding.")
                return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
            result = await seed_task_defs(new_session)
            await new_session.commit()
            return result

    async def seed_proposal_kinds(self, session: AsyncSession = None) -> dict[str, int]:
        """Upsert the HITL proposal-kind vocabulary from the family contract.

        Plan 5 W1: ``proposal_kinds`` is an *extensible domain vocabulary*
        seeded as data (constitution rule 7b). A new kind is a contract entry in
        ``hitl-proposal-kinds.json``, never a migration; this stage keeps
        tier/schema/resolver/card-variant honest on every boot.
        """
        from app.extensions.proposal_kinds import seed_proposal_kinds

        if session is not None:
            if not await self._table_exists(session, "proposal_kinds"):
                logger.error("Table 'proposal_kinds' does not exist. Skipping seeding.")
                return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
            return await seed_proposal_kinds(session)
        async with AsyncSessionLocal() as new_session:
            if not await self._table_exists(new_session, "proposal_kinds"):
                logger.error("Table 'proposal_kinds' does not exist. Skipping seeding.")
                return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
            result = await seed_proposal_kinds(new_session)
            await new_session.commit()
            return result

    async def seed_retention_policies(self, session: AsyncSession = None) -> dict[str, int]:
        """Insert missing retention-policy rows (operator edits win).

        Plan 6 W3: retention windows are data. The defaults live in
        ``app.services.data_lifecycle.retention_policies``; this stage only inserts rows that
        do not exist yet, so an operator's ``days``/``enabled`` edit survives
        every boot.
        """
        from app.services.data_lifecycle.retention_policies import seed_retention_policies

        if session is not None:
            if not await self._table_exists(session, "retention_policies"):
                logger.error("Table 'retention_policies' does not exist. Skipping seeding.")
                return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
            return await seed_retention_policies(session)
        async with AsyncSessionLocal() as new_session:
            if not await self._table_exists(new_session, "retention_policies"):
                logger.error("Table 'retention_policies' does not exist. Skipping seeding.")
                return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
            result = await seed_retention_policies(new_session)
            await new_session.commit()
            return result
