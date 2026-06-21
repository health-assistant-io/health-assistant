"""create fhir_provenance table

Revision ID: c987390e2778
Revises: 0ecc9ad85909
Create Date: 2026-06-21 16:00:00.000000

Audit item C10: a FHIR R4 Provenance resource records the who/when/why of
every create/update/delete on a clinical resource. Unlike the existing
audit_logs table (which is internal), Provenance is a FHIR resource that
travels with the data on export. Provenance is immutable (no SoftDelete,
no update); it's append-only by spec.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "c987390e2778"
down_revision = "0ecc9ad85909"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fhir_provenance",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("target", JSONB, nullable=False),  # [{reference: "Patient/abc"}]
        sa.Column("recorded", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("activity", JSONB, nullable=True),  # CodeableConcept (CREATE/UPDATE/DELETE)
        sa.Column("agent", JSONB, nullable=False),  # [{who: {reference: "User/uuid"}, type: {...}}]
        sa.Column("entity", JSONB, nullable=True),  # [{role: "source", what: {reference: "..."}}]
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_provenance_target",
        "fhir_provenance",
        ["target"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_provenance_recorded",
        "fhir_provenance",
        ["recorded"],
    )


def downgrade() -> None:
    op.drop_index("idx_provenance_recorded", table_name="fhir_provenance")
    op.drop_index("idx_provenance_target", table_name="fhir_provenance")
    op.drop_table("fhir_provenance")
