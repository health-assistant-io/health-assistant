"""cascade patient deletion (D9)

Revision ID: 223497128688
Revises: ca8c6c3350e6
Create Date: 2026-06-22

Policy decision: deleting a Patient removes their ENTIRE clinical record.
Previously, patient deletion CASCADEd to medications/allergies/events but
SET NULL on examinations/documents/devices/chat_sessions — leaving orphaned
rows that referenced no patient. All patient_id FKs now use CASCADE.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "223497128688"
down_revision: Union[str, Sequence[str], None] = "ca8c6c3350e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables whose patient_id FK must change from SET NULL → CASCADE.
# Each tuple: (table, constraint_name)
CASCADE_TABLES = [
    ("chat_sessions", "chat_sessions_patient_id_fkey"),
    ("documents", "documents_patient_id_fkey"),
    ("examinations", "examinations_patient_id_fkey"),
    ("fhir_devices", "fhir_devices_patient_id_fkey"),
]


def upgrade() -> None:
    """Change patient_id FK from SET NULL to CASCADE on 4 tables."""
    for table, constraint_name in CASCADE_TABLES:
        op.drop_constraint(constraint_name, table, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            "fhir_patients",
            ["patient_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Revert patient_id FK back to SET NULL on 4 tables."""
    for table, constraint_name in CASCADE_TABLES:
        op.drop_constraint(constraint_name, table, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            "fhir_patients",
            ["patient_id"],
            ["id"],
            ondelete="SET NULL",
        )
