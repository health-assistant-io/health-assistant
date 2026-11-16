"""WebAuthn/passkey credentials (ADR 0054 Phase 6 — program 12C2).

One tenant-owned table, ``webauthn_credentials``, holds the registered
authenticators for an account: the credential id (raw bytes, globally
unique), the CBOR/COSE public key, the signature counter, transport hints,
the AAGUID and the WebAuthn backup flags. ``sign_count`` powers clone
detection; ``disabled`` soft-disables a credential (a clone alarm never
deletes the only evidence).

Row-level security uses the NOT-NULL-tenant ``TENANT_ONLY`` flavor of
``0016_rls`` (read + write under the tenant GUC or the system flag).
``0016`` excludes this table from its flavor-map iteration
(``POST_0016_TABLES``) so the fresh-DB chain stays ``upgrade head``-clean;
this revision owns the policy and the runtime role's grants.

Downgrade drops the table (the owner doctrine applies — this program has
no production users yet).

Revision ID: 0020_webauthn
Revises: 0019_session_metadata
Create Date: 2026-11-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_webauthn"
down_revision: str | Sequence[str] | None = "0019_session_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "neuronection_health_app"

_TENANT_CTX = "(SELECT NULLIF(current_setting('app.tenant_id', true), ''))::uuid"
_SYSTEM_CTX = "COALESCE((SELECT NULLIF(current_setting('app.system', true), ''))::boolean, false)"
TENANT_ONLY = f"tenant_id = {_TENANT_CTX} OR {_SYSTEM_CTX}"


def upgrade() -> None:
    """Create the credential table with its row laws, RLS and grants."""
    op.create_table(
        "webauthn_credentials",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transports", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("aaguid", sa.String(length=36), nullable=True),
        sa.Column("backup_eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("backup_state", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_webauthn_credentials_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_webauthn_credentials_user_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webauthn_credentials")),
        sa.UniqueConstraint("credential_id", name=op.f("uq_webauthn_credentials_credential_id")),
    )
    op.create_index(
        "ix_webauthn_credentials_tenant_id", "webauthn_credentials", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_webauthn_credentials_user_id", "webauthn_credentials", ["user_id"], unique=False
    )
    op.create_index(
        "ix_webauthn_credentials_tenant_user",
        "webauthn_credentials",
        ["tenant_id", "user_id"],
        unique=False,
    )
    op.execute("ALTER TABLE webauthn_credentials ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY rls_tenant ON webauthn_credentials "
        f"USING ({TENANT_ONLY}) WITH CHECK ({TENANT_ONLY})"
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON webauthn_credentials TO {APP_ROLE}")


def downgrade() -> None:
    """Drop the credential table (policy and grants go with it)."""
    op.drop_index("ix_webauthn_credentials_tenant_user", table_name="webauthn_credentials")
    op.drop_index("ix_webauthn_credentials_user_id", table_name="webauthn_credentials")
    op.drop_index("ix_webauthn_credentials_tenant_id", table_name="webauthn_credentials")
    op.drop_table("webauthn_credentials")
