"""add fhir_patients.extensions jsonb

Adds a nullable ``extensions`` JSONB column to ``fhir_patients`` holding
local-keyed FHIR R4 extensions (race / ethnicity / preferred_language /
insurance_provider — see ``app/services/fhir_extensions.py``). The column
is additive; existing rows keep ``NULL``. No indexes (extensions are
read wholesale with the patient; no predicate queries planned for v1).

This is iteration 1 of the in-app setup-wizard plan
(``dev/audits/setup-wizard-design.md``).

Revision ID: s1e2t3u4p5w6
Revises: f1m2u3l4t5i6
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "s1e2t3u4p5w6"
down_revision = "f1m2u3l4t5i6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fhir_patients",
        sa.Column("extensions", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fhir_patients", "extensions")