"""Add practitioner_id to documents for FHIR DocumentReference.author (F11)

Revision ID: f11a2b3c4d5e
Revises: d2e3f4a5b6c7
Create Date: 2026-06-30 00:00:00.000000

Closes audit F11: ``DocumentReference.author`` previously emitted
``Practitioner/<owner_id>`` but ``documents.owner_id`` is a ``ForeignKey``
to ``users.id``, not ``doctors.id`` — so external clients resolving the
reference got 404. This migration adds a nullable ``practitioner_id``
column (FK to ``doctors.id``, ON DELETE SET NULL), backfills it from the
existing owner→doctor mapping, and adds an index for search.

The application layer (``DocumentModel.to_fhir_dict``) emits
``Practitioner/<practitioner_id>`` when the column is set, and omits the
``author`` element otherwise (rather than emitting a wrong reference).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f11a2b3c4d5e"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the nullable column. Type matches doctors.id (UUID).
    op.add_column(
        "documents",
        sa.Column(
            "practitioner_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Resolved Practitioner (DoctorModel) id for FHIR "
            "DocumentReference.author. Backfilled from owner_id at migration "
            "time; set on new uploads via owner→doctor lookup.",
        ),
    )

    # 2. Backfill from the existing owner→doctor mapping. Each user may have
    #    at most one doctor row per tenant; pick the first match deterministically.
    op.execute(
        """
        UPDATE documents d
        SET practitioner_id = sub.id
        FROM (
            SELECT DISTINCT ON (d2.tenant_id, d2.owner_id)
                   d2.id, d2.tenant_id, d2.owner_id
            FROM documents d2
            JOIN doctors doc ON doc.user_id = d2.owner_id
                            AND doc.tenant_id = d2.tenant_id
            WHERE d2.owner_id IS NOT NULL
            ORDER BY d2.tenant_id, d2.owner_id, doc.id
        ) AS sub
        WHERE d.tenant_id = sub.tenant_id
          AND d.owner_id = sub.owner_id
        """
    )

    # 3. Add the FK constraint + index.
    op.create_foreign_key(
        "fk_documents_practitioner_id_doctors",
        "documents",
        "doctors",
        ["practitioner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_practitioner_id",
        "documents",
        ["practitioner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_practitioner_id", table_name="documents")
    op.drop_constraint(
        "fk_documents_practitioner_id_doctors",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "practitioner_id")
