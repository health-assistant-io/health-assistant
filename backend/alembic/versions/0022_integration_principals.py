"""Scoped integration & extension principals (ADR 0054 D8, Program Phase 13).

Creates ``integration_principals`` — the service identities that replace
owner-role impersonation for integration and extension writes. One row is an
accountable owner (``owner_user_id``) plus the *only* permissions the machine
identity exercises (``scopes``, a closed permission subset), a patient scope
(``all|granted|none`` + allowlist), a lifecycle (``active|revoked|rotated``)
and an optional opaque credential (``ha_int_<prefix>_<secret>``; indexed
prefix + sha256 at rest).

Row laws as constraints/indexes:

* ``ck_integration_principals_kind_vocabulary`` — ``integration|extension|service``;
* ``ck_integration_principals_status_vocabulary`` — ``active|revoked|rotated``;
* ``ck_integration_principals_patient_scope_vocabulary`` — ``all|granted|none``;
* ``ck_integration_principals_granted_scope_requires_ids`` — ``granted`` always
  names at least one patient id;
* ``ck_integration_principals_integration_kind_requires_binding`` — an
  integration principal always names its ``user_integrations`` row;
* ``ck_integration_principals_secret_pair_together`` — prefix and hash are
  both present or both absent (internally-provisioned principals carry none);
* ``uq_integration_principals_active_integration`` — at most one *active*
  principal per integration (rotation revokes the predecessor first);
* ``fk_integration_principals_integration_id`` — ``SET NULL`` so an erased
  connector does not erase the accountability row;
* tenant/owner FKs cascade — erasing the tenant or owner kills every stale
  bearer.

Row-level security: NOT NULL tenant, so the ``TENANT_ONLY`` flavor of
``0016_rls`` (read + write under the tenant GUC or the system flag).
``0016`` excludes this table from its flavor-map iteration
(``POST_0016_TABLES``) so the fresh-DB chain stays ``upgrade head``-clean;
this revision owns the policy and the runtime role's grants.

Downgrade drops the table (the owner doctrine applies — this program has no
production users yet).

Revision ID: 0022_integration_principals
Revises: 0021_api_tokens
Create Date: 2026-11-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from app.models.api_token import PATIENT_SCOPE_LENGTH, PatientScope
from app.models.integration_principal import (
    KIND_LENGTH,
    PATIENT_SCOPES,
    PRINCIPAL_KINDS,
    PRINCIPAL_STATUSES,
    STATUS_LENGTH,
    IntegrationPrincipalKind,
    IntegrationPrincipalStatus,
)

# revision identifiers, used by Alembic.
revision: str = "0022_integration_principals"
down_revision: str | Sequence[str] | None = "0021_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "neuronection_health_app"

_TENANT_CTX = "(SELECT NULLIF(current_setting('app.tenant_id', true), ''))::uuid"
_SYSTEM_CTX = "COALESCE((SELECT NULLIF(current_setting('app.system', true), ''))::boolean, false)"
TENANT_ONLY = f"tenant_id = {_TENANT_CTX} OR {_SYSTEM_CTX}"


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def upgrade() -> None:
    """Create the principal table with its row laws, RLS and grants."""
    op.create_table(
        "integration_principals",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                IntegrationPrincipalKind,
                native_enum=False,
                values_callable=_enum_values,
                length=KIND_LENGTH,
            ),
            nullable=False,
            server_default=IntegrationPrincipalKind.INTEGRATION.value,
        ),
        sa.Column("integration_id", sa.UUID(), nullable=True),
        sa.Column("scopes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "patient_scope",
            sa.Enum(
                PatientScope,
                native_enum=False,
                values_callable=_enum_values,
                length=PATIENT_SCOPE_LENGTH,
            ),
            nullable=False,
            server_default=PatientScope.ALL.value,
        ),
        sa.Column("patient_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "status",
            sa.Enum(
                IntegrationPrincipalStatus,
                native_enum=False,
                values_callable=_enum_values,
                length=STATUS_LENGTH,
            ),
            nullable=False,
            server_default=IntegrationPrincipalStatus.ACTIVE.value,
        ),
        sa.Column("secret_prefix", sa.String(length=24), nullable=True),
        sa.Column("secret_hash", sa.String(length=64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_from", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint("name <> ''", name=op.f("ck_integration_principals_name_required")),
        sa.CheckConstraint(
            f"kind IN ({_in(PRINCIPAL_KINDS)})",
            name=op.f("ck_integration_principals_kind_vocabulary"),
        ),
        sa.CheckConstraint(
            f"status IN ({_in(PRINCIPAL_STATUSES)})",
            name=op.f("ck_integration_principals_status_vocabulary"),
        ),
        sa.CheckConstraint(
            f"patient_scope IN ({_in(PATIENT_SCOPES)})",
            name=op.f("ck_integration_principals_patient_scope_vocabulary"),
        ),
        sa.CheckConstraint(
            "patient_scope <> 'granted' OR jsonb_array_length(patient_ids) > 0",
            name=op.f("ck_integration_principals_granted_scope_requires_ids"),
        ),
        sa.CheckConstraint(
            "kind <> 'integration' OR integration_id IS NOT NULL",
            name=op.f("ck_integration_principals_integration_kind_requires_binding"),
        ),
        sa.CheckConstraint(
            "(secret_prefix IS NULL) = (secret_hash IS NULL)",
            name=op.f("ck_integration_principals_secret_pair_together"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_integration_principals_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_integration_principals_owner_user_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_integration_principals_integration_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from"],
            ["integration_principals.id"],
            name=op.f("fk_integration_principals_rotated_from"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_principals")),
        sa.UniqueConstraint("secret_prefix", name=op.f("uq_integration_principals_secret_prefix")),
        sa.UniqueConstraint("secret_hash", name=op.f("uq_integration_principals_secret_hash")),
    )
    op.create_index(
        "ix_integration_principals_tenant_id", "integration_principals", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_integration_principals_owner_user_id",
        "integration_principals",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_principals_integration_id",
        "integration_principals",
        ["integration_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_principals_tenant_owner",
        "integration_principals",
        ["tenant_id", "owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_principals_created_at",
        "integration_principals",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_integration_principals_updated_at",
        "integration_principals",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        "uq_integration_principals_active_integration",
        "integration_principals",
        ["tenant_id", "integration_id"],
        unique=True,
        postgresql_where=sa.text("integration_id IS NOT NULL AND status = 'active'"),
    )
    op.execute("ALTER TABLE integration_principals ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY rls_tenant ON integration_principals "
        f"USING ({TENANT_ONLY}) WITH CHECK ({TENANT_ONLY})"
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON integration_principals TO {APP_ROLE}")


def downgrade() -> None:
    """Drop the principal table (policy and grants go with it)."""
    op.drop_index(
        "uq_integration_principals_active_integration", table_name="integration_principals"
    )
    op.drop_index("ix_integration_principals_updated_at", table_name="integration_principals")
    op.drop_index("ix_integration_principals_created_at", table_name="integration_principals")
    op.drop_index("ix_integration_principals_tenant_owner", table_name="integration_principals")
    op.drop_index("ix_integration_principals_integration_id", table_name="integration_principals")
    op.drop_index("ix_integration_principals_owner_user_id", table_name="integration_principals")
    op.drop_index("ix_integration_principals_tenant_id", table_name="integration_principals")
    op.drop_table("integration_principals")
