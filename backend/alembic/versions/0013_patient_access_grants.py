"""Care-team / patient access grants (ADR 0054 Phase 2 — program Phase 7A).

Creates ``patient_access_grants`` — one row per (grantee principal, patient)
access decision — with the closed vocabularies mirrored in CHECK constraints
(permission ``read|write|admin``, source ``admin|care_team|patient|guardian``,
status ``pending|active|revoked|expired|declined``). ``break_glass`` is
deliberately NOT a grant source (ADR 0054 D1): break-glass elevations are
their own rows in ``access_elevations`` (a later program phase).

Laws enforced as indexes:

* ``ck_patient_access_grants_grantee_exactly_one`` — a grant targets exactly
  one grantee principal (a user XOR a doctor directory entry).
* ``uq_patient_access_grants_active_user`` /
  ``uq_patient_access_grants_active_doctor`` — partial unique
  ``(tenant_id, patient_id, grantee_*)`` among ``status = 'active'`` rows:
  one live grant per grantee identity per patient; superseded (revoked /
  expired / declined) rows survive as the sharing ledger.

Downgrade drops the table (the owner doctrine applies — this program has no
production users yet).

Revision ID: 0013_patient_access_grants
Revises: 0012_vaccine_target_edges
Create Date: 2026-11-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.patient_access_grant import (
    PERMISSIONS,
    SOURCES,
    STATUSES,
    GrantPermission,
    GrantSource,
    GrantStatus,
)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


# revision identifiers, used by Alembic.
revision: str = "0013_patient_access_grants"
down_revision: str | Sequence[str] | None = "0012_vaccine_target_edges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the grant table with its CHECK mirrors, cascade FKs and indexes."""
    op.create_table(
        "patient_access_grants",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("grantee_user_id", sa.UUID(), nullable=True),
        sa.Column("grantee_doctor_id", sa.UUID(), nullable=True),
        sa.Column(
            "permission",
            sa.Enum(GrantPermission, native_enum=False, values_callable=_enum_values),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(GrantSource, native_enum=False, values_callable=_enum_values),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(GrantStatus, native_enum=False, values_callable=_enum_values),
            nullable=False,
        ),
        sa.Column("granted_by", sa.UUID(), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint(
            "((grantee_user_id IS NULL) <> (grantee_doctor_id IS NULL))",
            name=op.f("ck_patient_access_grants_grantee_exactly_one"),
        ),
        sa.CheckConstraint(
            f"permission IN ({_in(PERMISSIONS)})",
            name=op.f("ck_patient_access_grants_permission_vocabulary"),
        ),
        sa.CheckConstraint(
            f"source IN ({_in(SOURCES)})",
            name=op.f("ck_patient_access_grants_source_vocabulary"),
        ),
        sa.CheckConstraint(
            f"status IN ({_in(STATUSES)})",
            name=op.f("ck_patient_access_grants_status_vocabulary"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_patient_access_grants_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_patient_access_grants_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grantee_user_id"],
            ["users.id"],
            name=op.f("fk_patient_access_grants_grantee_user_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grantee_doctor_id"],
            ["doctors.id"],
            name=op.f("fk_patient_access_grants_grantee_doctor_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_patient_access_grants_granted_by"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_access_grants")),
    )
    for name, columns in (
        ("ix_patient_access_grants_patient_id", ["patient_id"]),
        ("ix_patient_access_grants_grantee_user_id", ["grantee_user_id"]),
        ("ix_patient_access_grants_grantee_doctor_id", ["grantee_doctor_id"]),
        ("ix_patient_access_grants_tenant_id", ["tenant_id"]),
        ("ix_patient_access_grants_expires_at", ["expires_at"]),
        (
            "ix_patient_access_grants_tenant_patient",
            ["tenant_id", "patient_id"],
        ),
        (
            "ix_patient_access_grants_tenant_grantee_user",
            ["tenant_id", "grantee_user_id"],
        ),
        (
            "ix_patient_access_grants_tenant_grantee_doctor",
            ["tenant_id", "grantee_doctor_id"],
        ),
        ("ix_patient_access_grants_created_at", ["created_at"]),
        ("ix_patient_access_grants_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "patient_access_grants", columns, unique=False)
    op.create_index(
        "uq_patient_access_grants_active_user",
        "patient_access_grants",
        ["tenant_id", "patient_id", "grantee_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND grantee_user_id IS NOT NULL"),
        sqlite_where=sa.text("status = 'active' AND grantee_user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_patient_access_grants_active_doctor",
        "patient_access_grants",
        ["tenant_id", "patient_id", "grantee_doctor_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND grantee_doctor_id IS NOT NULL"),
        sqlite_where=sa.text("status = 'active' AND grantee_doctor_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop the grant table (destructive owner doctrine — the program has no users)."""
    op.drop_index("uq_patient_access_grants_active_doctor", table_name="patient_access_grants")
    op.drop_index("uq_patient_access_grants_active_user", table_name="patient_access_grants")
    for name in (
        "ix_patient_access_grants_tenant_grantee_doctor",
        "ix_patient_access_grants_tenant_grantee_user",
        "ix_patient_access_grants_tenant_patient",
        "ix_patient_access_grants_updated_at",
        "ix_patient_access_grants_created_at",
        "ix_patient_access_grants_expires_at",
        "ix_patient_access_grants_tenant_id",
        "ix_patient_access_grants_grantee_doctor_id",
        "ix_patient_access_grants_grantee_user_id",
        "ix_patient_access_grants_patient_id",
    ):
        op.drop_index(name, table_name="patient_access_grants")
    op.drop_table("patient_access_grants")
