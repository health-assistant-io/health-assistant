"""Per-tenant uniqueness for catalog/identity columns (audit B1)

Revision ID: l3c4d5e6f7a8
Revises: k2b3c4d5e6f7
Create Date: 2026-07-15

``slug`` / ``mrn`` were declared ``UNIQUE`` *globally* on four tables even
though ``tenant_id`` is nullable on each — so a system-wide (NULL tenant) row
and a tenant override could not share the same slug, and two tenants could not
each carry a patient with the same MRN. The same defect was already fixed for
``concepts`` (migration ``9a3f7c2e1b4d``): a unique index over
``(col, COALESCE(tenant_id, <sentinel>))`` makes NULL-tenant rows share one
synthetic tenant and every real tenant its own namespace, so the same slug /
MRN can coexist across tenants (but not within one tenant).

Applied here to the four tables where per-tenant uniqueness is the correct
invariant and that carry a ``tenant_id`` column:

* ``biomarker_definitions.slug`` (catalog override)
* ``anatomy_structures.slug`` (anatomy catalog)
* ``clinical_event_types.slug`` (event-type catalog)
* ``fhir_patients.mrn`` (per-institution record number)

**Deliberately NOT changed:**

* ``users.email`` — remains globally unique. It is the login identifier and
  ``get_user_by_email`` uses ``scalar_one_or_none()``; allowing the same email
  across tenants would make login ambiguous. The data model is one-tenant-per-user.
* ``units.symbol`` — remains globally unique. ``units`` carries no ``tenant_id``
  column; units (e.g. ``mg/dL``) are a universal reference catalog.

The global unique is strictly *stricter* than the per-tenant unique, so no
existing data can violate the new index — the upgrade cannot fail on a
populated DB. The model columns drop ``unique=True`` to match (the COALESCE
index is created here, mirroring the ``concepts`` precedent).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "l3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "k2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same sentinel as the concepts migration (9a3f7c2e1b4d) — all NULL-tenant
# (system) rows collapse into one synthetic tenant so two system rows still
# can't share a slug, while a system row and a tenant row can.
_NULL_TENANT = "00000000-0000-0000-0000-000000000000"


# (table, column, name of the existing global-unique object to drop, True if
# it is a CONSTRAINT (DROP CONSTRAINT), False if it is a standalone INDEX
# (DROP INDEX), new COALESCE index name)
_SPECS = [
    (
        "biomarker_definitions",
        "slug",
        "ix_biomarker_definitions_slug",
        False,
        "ix_biomarker_definitions_slug_tenant",
    ),
    (
        "anatomy_structures",
        "slug",
        "ix_anatomy_structures_slug",
        False,
        "ix_anatomy_structures_slug_tenant",
    ),
    (
        "clinical_event_types",
        "slug",
        "clinical_event_types_slug_key",
        True,
        "ix_clinical_event_types_slug_tenant",
    ),
    (
        "fhir_patients",
        "mrn",
        "fhir_patients_mrn_key",
        True,
        "ix_fhir_patients_mrn_tenant",
    ),
]


def upgrade() -> None:
    coalesce = f"COALESCE(tenant_id, '{_NULL_TENANT}'::uuid)"
    for table, column, old_obj, is_constraint, new_index in _SPECS:
        if is_constraint:
            op.execute(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {old_obj}"
            )
        else:
            op.execute(f"DROP INDEX IF EXISTS {old_obj}")
        op.execute(
            f"CREATE UNIQUE INDEX {new_index} ON {table} "
            f"({column}, {coalesce})"
        )


def downgrade() -> None:
    # Recreate the original global-unique objects and drop the COALESCE indexes.
    # (Only sensible on a near-empty DB; left for completeness.)
    for table, column, old_obj, is_constraint, new_index in reversed(_SPECS):
        op.execute(f"DROP INDEX IF EXISTS {new_index}")
        if is_constraint:
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {old_obj} UNIQUE ({column})"
            )
        else:
            op.execute(
                f"CREATE UNIQUE INDEX {old_obj} ON {table} ({column})"
            )
