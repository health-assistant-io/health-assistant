"""concepts.scope re-backfill for tenant-scoped rows

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-09

The original ``e6f7a8b9c0d1`` migration added the ``scope`` column to
``concepts`` and backfilled ``scope = 'tenant' WHERE tenant_id IS NOT NULL``.
However ``ConceptService.create_concept`` (and the seed loader) construct
``Concept`` rows without explicitly setting ``scope``, so the model default
``CatalogScope.SYSTEM`` fires — meaning every tenant-scoped concept created
*after* that migration landed with ``tenant_id`` set but ``scope = 'system'``
(an inconsistency the catalog read path filters on ``scope`` and would
mis-filter).

This migration re-runs the same idempotent backfill to repair any straggler
rows. The root cause is also fixed in ``ConceptService.create_concept``
(taxonomy/catalog merge plan, Phase 2) so new writes set ``scope`` consistently
with ``tenant_id`` going forward.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "g8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: tenant-scoped concepts (tenant_id NOT NULL) get TENANT scope.
    # Guarded by the scope predicate so re-running is a no-op once consistent.
    op.execute(
        "UPDATE concepts SET scope = 'tenant' "
        "WHERE tenant_id IS NOT NULL AND scope = 'system'"
    )


def downgrade() -> None:
    # No meaningful downgrade — reverting would reintroduce the inconsistency.
    pass
