"""Scoped API tokens / personal access tokens (ADR 0054 Phase 6 — program 12C2B).

Creates ``api_tokens`` — one owner-bound, tenant-bounded credential per row,
hashed at rest (``token_prefix`` lookup + ``token_hash`` sha256 of the
plaintext), with a closed permission subset (``permissions`` JSONB), a
patient scope vocabulary (``patient_scope`` + ``patient_ids``), optional
expiry, revocation and rotation linkage.

Row laws as constraints/indexes:

* ``ck_api_tokens_patient_scope_vocabulary`` — ``all|granted|none``;
* ``ck_api_tokens_granted_scope_requires_ids`` — ``granted`` always names at
  least one patient id (an empty allowlist is not a scope);
* ``uq_api_tokens_token_prefix`` / ``uq_api_tokens_token_hash`` — the two
  lookup keys are unique, so a prefix collision can never resolve two rows;
* ``fk_api_tokens_owner_user_id`` / ``fk_api_tokens_tenant_id`` — cascade,
  so erasing the owner or tenant kills every stale bearer;
* ``fk_api_tokens_rotated_from`` — ``SET NULL``: the replacement row
  survives even if the predecessor is purged.

Row-level security: NOT NULL tenant, so the ``TENANT_ONLY`` flavor of
``0016_rls`` (read + write under the tenant GUC or the system flag).
``0016`` excludes this table from its flavor-map iteration
(``POST_0016_TABLES``) so the fresh-DB chain stays ``upgrade head``-clean;
this revision owns the policy and the runtime role's grants.

Downgrade drops the table (the owner doctrine applies — this program has
no production users yet).

Revision ID: 0021_api_tokens
Revises: 0020_webauthn
Create Date: 2026-11-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from app.models.api_token import PATIENT_SCOPE_LENGTH, PATIENT_SCOPES, PatientScope

# revision identifiers, used by Alembic.
revision: str = "0021_api_tokens"
down_revision: str | Sequence[str] | None = "0020_webauthn"
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
    """Create the token table with its row laws, RLS and grants."""
    op.create_table(
        "api_tokens",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("permissions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("name <> ''", name=op.f("ck_api_tokens_name_required")),
        sa.CheckConstraint("token_prefix <> ''", name=op.f("ck_api_tokens_token_prefix_required")),
        sa.CheckConstraint("token_hash <> ''", name=op.f("ck_api_tokens_token_hash_required")),
        sa.CheckConstraint(
            f"patient_scope IN ({_in(PATIENT_SCOPES)})",
            name=op.f("ck_api_tokens_patient_scope_vocabulary"),
        ),
        sa.CheckConstraint(
            "patient_scope <> 'granted' OR jsonb_array_length(patient_ids) > 0",
            name=op.f("ck_api_tokens_granted_scope_requires_ids"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_api_tokens_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_api_tokens_owner_user_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from"],
            ["api_tokens.id"],
            name=op.f("fk_api_tokens_rotated_from"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_prefix", name=op.f("uq_api_tokens_token_prefix")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
    )
    op.create_index("ix_api_tokens_tenant_id", "api_tokens", ["tenant_id"], unique=False)
    op.create_index("ix_api_tokens_owner_user_id", "api_tokens", ["owner_user_id"], unique=False)
    op.create_index(
        "ix_api_tokens_tenant_owner", "api_tokens", ["tenant_id", "owner_user_id"], unique=False
    )
    op.create_index("ix_api_tokens_created_at", "api_tokens", ["created_at"], unique=False)
    op.create_index("ix_api_tokens_updated_at", "api_tokens", ["updated_at"], unique=False)
    op.execute("ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY rls_tenant ON api_tokens USING ({TENANT_ONLY}) WITH CHECK ({TENANT_ONLY})"
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON api_tokens TO {APP_ROLE}")


def downgrade() -> None:
    """Drop the token table (policy and grants go with it)."""
    op.drop_index("ix_api_tokens_updated_at", table_name="api_tokens")
    op.drop_index("ix_api_tokens_created_at", table_name="api_tokens")
    op.drop_index("ix_api_tokens_tenant_owner", table_name="api_tokens")
    op.drop_index("ix_api_tokens_owner_user_id", table_name="api_tokens")
    op.drop_index("ix_api_tokens_tenant_id", table_name="api_tokens")
    op.drop_table("api_tokens")
