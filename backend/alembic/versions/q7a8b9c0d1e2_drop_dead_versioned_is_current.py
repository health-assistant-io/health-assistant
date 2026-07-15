"""Drop dead VersionedMixin.is_current column (audit B4)

Revision ID: q7a8b9c0d1e2
Revises: o6f7a8b9c0d1
Create Date: 2026-07-15

``VersionedMixin`` declared two columns: ``version`` and ``is_current``.
The audit (B4) found ``is_current`` is dead data — it is never queried,
filtered, or read anywhere in the codebase (only listed in a mass-assignment
readonly guard). ``version``, by contrast, is a *live* feature: the FHIR R4
facade bumps it on every update (``facade/crud.update``), reads it for
``If-Match`` optimistic locking (HTTP 412), and exposes it via the ``ETag``
header (``W/"<version>"``) — covered by ``test_fhir_r4_versioning.py``.

The audit's literal recommendation was "implement (event hook) or remove"
the whole mixin, but removing ``version`` would destroy a working, tested
FHIR feature. This migration therefore removes only the genuinely-dead
``is_current`` column from all 18 tables that carry it, and leaves the
``version`` column (and the now single-column ``VersionedMixin``) in place.

Breaking change is acceptable: the project has no users.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q7a8b9c0d1e2"
down_revision: Union[str, None] = "o6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables carrying the dead ``is_current`` column (every model mixing in
# VersionedMixin). Enumerated from Base.metadata at write time.
_TABLES = [
    "biomarker_definitions",
    "clinical_events",
    "concepts",
    "doctors",
    "documents",
    "examinations",
    "fhir_allergy_intolerances",
    "fhir_communications",
    "fhir_diagnostic_reports",
    "fhir_medications",
    "fhir_observations",
    "fhir_organizations",
    "fhir_patients",
    "patient_immunizations",
    "patient_layouts",
    "telemetry_data",
    "tenants",
    "users",
]


def upgrade() -> None:
    # Defensive: only drop where the column actually exists, so this migration
    # is idempotent on a partially-migrated / fresh DB.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in _TABLES:
        existing = {c["name"] for c in inspector.get_columns(table_name)}
        if "is_current" in existing:
            op.drop_column(table_name, "is_current")


def downgrade() -> None:
    # Re-add the column as it was declared (Boolean, server default true) so a
    # downgrade restores the pre-migration schema shape.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in _TABLES:
        existing = {c["name"] for c in inspector.get_columns(table_name)}
        if "is_current" not in existing:
            op.add_column(
                table_name,
                sa.Column(
                    "is_current",
                    sa.Boolean(),
                    nullable=True,
                    server_default=sa.text("true"),
                ),
            )
