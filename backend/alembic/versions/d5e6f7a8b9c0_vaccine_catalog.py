"""vaccine catalog + patient immunizations (Phase 5)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-08

Phase 5 of the unified-catalog architecture (``dev/plans/unified-catalog-
architecture-2026-07-08.md``). Adds two tables:

- ``vaccine_catalog`` — canonical CVX-coded vaccine reference definitions
  (mirrors ``medication_catalog``), incl. the ``class_concept_id`` taxonomy FK.
- ``patient_immunizations`` — patient-instance dose records that project to
  FHIR R4 ``Immunization`` (mirrors ``fhir_medications``), with a real
  ``patient_id`` column so the facade ``patient`` search param works.

Downgrade drops both.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vaccine_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("coding_system", sa.String(50), nullable=True),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("target_diseases", postgresql.JSONB(), nullable=True),
        sa.Column("dose_schedule", postgresql.JSONB(), nullable=True),
        sa.Column("contraindications", sa.Text(), nullable=True),
        sa.Column("side_effects", postgresql.JSONB(), nullable=True),
        sa.Column(
            "class_concept_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("concepts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vaccine_catalog_slug", "vaccine_catalog", ["slug"])
    op.create_index(
        "ix_vaccine_catalog_class_concept_id",
        "vaccine_catalog",
        ["class_concept_id"],
    )
    op.create_index("ix_vaccine_catalog_tenant_id", "vaccine_catalog", ["tenant_id"])

    op.create_table(
        "patient_immunizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fhir_patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vaccine_catalog_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vaccine_catalog.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "completed", "entered-in-error", "not-done", name="immunizationstatus"
            ),
            nullable=False,
        ),
        sa.Column("vaccine_code", postgresql.JSONB(), nullable=False),
        sa.Column("administered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dose_number", sa.String(20), nullable=True),
        sa.Column("lot_number", sa.String(100), nullable=True),
        sa.Column("manufacturer", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_patient_immunizations_tenant_id",
        "patient_immunizations",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_patient_immunizations_patient_id", "patient_immunizations", ["patient_id"]
    )
    op.create_index(
        "ix_patient_immunizations_vaccine_catalog_id",
        "patient_immunizations",
        ["vaccine_catalog_id"],
    )
    op.create_index(
        "ix_patient_immunizations_tenant_id", "patient_immunizations", ["tenant_id"]
    )
    op.create_index(
        "ix_patient_immunizations_administered_at",
        "patient_immunizations",
        ["administered_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patient_immunizations_administered_at", table_name="patient_immunizations"
    )
    op.drop_index("ix_patient_immunizations_tenant_id", table_name="patient_immunizations")
    op.drop_index(
        "ix_patient_immunizations_vaccine_catalog_id",
        table_name="patient_immunizations",
    )
    op.drop_index(
        "ix_patient_immunizations_patient_id", table_name="patient_immunizations"
    )
    op.drop_table("patient_immunizations")
    # Drop the enum type created implicitly for the status column.
    sa.Enum(name="immunizationstatus").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_vaccine_catalog_tenant_id", table_name="vaccine_catalog")
    op.drop_index(
        "ix_vaccine_catalog_class_concept_id", table_name="vaccine_catalog"
    )
    op.drop_index("ix_vaccine_catalog_slug", table_name="vaccine_catalog")
    op.drop_table("vaccine_catalog")
