"""Observation.document_id String -> UUID FK (audit B2)

Revision ID: m4d5e6f7a8b9
Revises: l3c4d5e6f7a8
Create Date: 2026-07-15

``fhir_observations.document_id`` was a free-text ``String`` that stored a
document UUID as text. Deleting a ``DocumentModel`` left orphan observations
with stale string references, and service code had to compare with
``str(obs.document_id) == str(d.id)`` because the types didn't line up.

This makes it a real ``UUID`` column with a foreign key to ``documents.id``
``ON DELETE SET NULL`` — so deleting a document nulls the link instead of
dangling it, and comparisons are plain ``UUID == UUID``.

The upgrade is defensive: it nulls any value that isn't a well-formed UUID and
any that doesn't reference an existing document *before* the type change + FK,
so the migration cannot fail on a populated dev DB. (The project has no users,
so this is belt-and-braces.) The application write paths were updated in the
same change to pass ``UUID`` objects (``fhir_service.create_observation``,
``ai.pipeline.persistence``, ``analytics_service``, ``document_service_db``).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "m4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "l3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FK_NAME = "fk_fhir_observations_document_id_documents"


def upgrade() -> None:
    # 1. Null anything that isn't a canonical UUID text (empty strings, junk).
    op.execute(
        "UPDATE fhir_observations SET document_id = NULL "
        "WHERE document_id IS NOT NULL "
        "AND document_id !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"
    )
    # 2. Null orphan references (no matching document) so the FK can be added.
    op.execute(
        "UPDATE fhir_observations o SET document_id = NULL "
        "WHERE document_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id::text = o.document_id)"
    )
    # 3. Widen the column to UUID. Indexes on the column (ix_fhir_observations
    #    _document_id) are rebuilt automatically by PostgreSQL.
    op.execute(
        "ALTER TABLE fhir_observations ALTER COLUMN document_id TYPE UUID "
        "USING document_id::uuid"
    )
    # 4. Add the foreign key.
    op.create_foreign_key(
        _FK_NAME,
        "fhir_observations",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_FK_NAME, "fhir_observations", type_="foreignkey")
    op.execute(
        "ALTER TABLE fhir_observations ALTER COLUMN document_id TYPE VARCHAR "
        "USING document_id::text"
    )
