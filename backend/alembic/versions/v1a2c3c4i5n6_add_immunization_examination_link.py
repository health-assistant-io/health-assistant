"""add examination_id to patient_immunizations

Links each administered dose to the clinical encounter (examination) during
which it was given, mirroring ``fhir_medications.examination_id``. Powers the
examination InstanceField in the vaccination form (FHIR R4 ``Immunization.encounter``).

Revision ID: v1a2c3c4i5n6
Revises: n1o2t3i4f5y6
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa


revision = "v1a2c3c4i5n6"
down_revision = "n1o2t3i4f5y6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("patient_immunizations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("examination_id", sa.UUID(), nullable=True)
        )
        batch_op.create_index(
            "ix_patient_immunizations_examination_id",
            ["examination_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_patient_immunizations_examination_id_examinations",
            "examinations",
            ["examination_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("patient_immunizations", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_patient_immunizations_examination_id_examinations",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_patient_immunizations_examination_id")
        batch_op.drop_column("examination_id")
