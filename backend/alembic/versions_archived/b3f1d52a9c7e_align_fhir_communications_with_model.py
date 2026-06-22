"""align_fhir_communications_with_model

``CommunicationModel`` uses ``VersionedMixin`` (adds ``version`` +
``is_current`` columns) and inherits ``TenantMixin`` (``tenant_id``
nullable). The migration that created the ``fhir_communications`` table
(``34414d55a822``) omitted the version columns and declared ``tenant_id``
as ``NOT NULL``.

Effect:
- Any ORM access to ``communication.version`` / ``communication.is_current``
  raised because the columns weren't in the result set.
- The facade's PUT version-bump hook crashed.
- ``alembic check`` produced a spurious diff every run.
- App code that constructed ``CommunicationModel(tenant_id=None, ...)``
  would fail at commit time on the NOT NULL constraint.

This migration adds the missing columns (matching ``VersionedMixin`` —
``nullable=True``, no server_default, consistent with every other table
that uses the mixin), relaxes ``tenant_id`` to nullable (matching
``TenantMixin``), and creates the ``notifications.communication_id`` index
(the column was added to the DB by ``34414d55a822`` and surfaced on the
``Notification`` model, but the index wasn't created).

Revision ID: b3f1d52a9c7e
Revises: 7a9c2e1b4f3a
Create Date: 2026-06-22 00:01:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3f1d52a9c7e"
down_revision: Union[str, Sequence[str], None] = "7a9c2e1b4f3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fhir_communications",
        sa.Column("version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "fhir_communications",
        sa.Column("is_current", sa.Boolean(), nullable=True),
    )
    op.alter_column(
        "fhir_communications",
        "tenant_id",
        existing_type=sa.UUID(),
        nullable=True,
    )
    op.create_index(
        "ix_notifications_communication_id",
        "notifications",
        ["communication_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_communication_id", table_name="notifications")
    op.alter_column(
        "fhir_communications",
        "tenant_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.drop_column("fhir_communications", "is_current")
    op.drop_column("fhir_communications", "version")

