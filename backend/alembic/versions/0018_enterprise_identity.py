"""Enterprise identity: per-tenant OIDC SSO + SCIM 2.0 (ADR 0054 D2, Phase 12B).

Five tenant-owned tables behind the two Phase-6 enterprise-identity surfaces:

* ``tenant_sso_configs`` — one OIDC provider binding per (tenant, issuer):
  client credentials by **secret slot** (never a value), the
  ``allowed_domains`` JIT ceiling, ``default_role`` + ``group_role_map``
  (both capped to the non-SYSTEM_ADMIN role vocabulary — SSO is never a
  path to SYSTEM_ADMIN, mirrored as ``ck_tenant_sso_configs_default_role_vocabulary``).
* ``user_identities`` — the **link-only** identity table (D2: one identity
  = one tenant): ``(issuer, subject)`` is globally unique (no account can
  be double-linked, in any tenant), ``(tenant, user, issuer)`` unique, and
  ``ck_user_identities_email_verified_required`` pins the verified-email law
  at the row level.
* ``scim_tokens`` — tenant-bounded SCIM bearer credentials by sha256
  (plaintext shown once at creation, never stored); ``active = false`` is
  the revocation.
* ``scim_groups`` / ``scim_group_members`` — the RFC 7644 directory state
  the ``/scim/v2/Groups`` surface reads and writes (display name unique per
  tenant; membership rows unique per pair). Groups carry no role of their
  own — the SSO config's ``group_role_map`` decides at login/apply time.

Row-level security: the four NOT NULL-tenant tables carry the
``TENANT_ONLY`` flavor of ``0016_rls`` (read + write under the tenant GUC
or the system flag); ``scim_group_members`` (no tenant column) uses the
tenant-derived-child flavor — its policy anchors on the parent
``scim_groups`` row, composing the same way ``0016``'s ``PARENT_ANCHORS``
do. All five get the runtime role's DML grants, re-asserted self-healing
like ``0016`` does. ``0016`` excludes these tables from its flavor-map
iteration (the later-revision exclusion, 0017 precedent) so the fresh-DB
chain stays ``upgrade head``-clean.

Downgrade drops the tables (the owner doctrine applies — this program has
no production users yet).

Revision ID: 0018_enterprise_identity
Revises: 0017_access_elevations
Create Date: 2026-11-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from app.models.tenant_sso_config import SSO_ASSIGNABLE_ROLES

# revision identifiers, used by Alembic.
revision: str = "0018_enterprise_identity"
down_revision: str | Sequence[str] | None = "0017_access_elevations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "neuronection_health_app"

_TENANT_CTX = "(SELECT NULLIF(current_setting('app.tenant_id', true), ''))::uuid"
_SYSTEM_CTX = "COALESCE((SELECT NULLIF(current_setting('app.system', true), ''))::boolean, false)"

#: Identical to ``0016_rls.TENANT_ONLY`` — every table here is NOT NULL-tenant.
TENANT_ONLY = f"tenant_id = {_TENANT_CTX} OR {_SYSTEM_CTX}"


def _enable_rls_and_grants(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY rls_tenant ON {table} USING ({TENANT_ONLY}) WITH CHECK ({TENANT_ONLY})"
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")


def _enable_parent_rls_and_grants(table: str, parent: str, fk_column: str) -> None:
    """The tenant-derived-child flavor: anchored on the parent's own policy."""
    exists = f"EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {table}.{fk_column})"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY rls_tenant_parent ON {table} USING ({exists}) WITH CHECK ({exists})")
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    """Create the enterprise-identity tables with their row laws and RLS."""
    # --- tenant_sso_configs -------------------------------------------------
    op.create_table(
        "tenant_sso_configs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret_ref", sa.String(length=256), nullable=False),
        sa.Column(
            "allowed_domains", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "default_role",
            sa.Enum(*SSO_ASSIGNABLE_ROLES, native_enum=False, values_callable=_enum_values),
            nullable=False,
        ),
        sa.Column("group_role_map", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("jit_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("scopes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint(
            f"default_role IN ({_in(SSO_ASSIGNABLE_ROLES)})",
            name=op.f("ck_tenant_sso_configs_default_role_vocabulary"),
        ),
        sa.CheckConstraint(
            "client_secret_ref <> ''",
            name=op.f("ck_tenant_sso_configs_client_secret_ref_required"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_sso_configs_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_sso_configs")),
        sa.UniqueConstraint("tenant_id", "issuer", name="uq_tenant_sso_configs_tenant_id_issuer"),
    )
    op.create_index(
        "ix_tenant_sso_configs_tenant_enabled",
        "tenant_sso_configs",
        ["tenant_id", "enabled"],
        unique=False,
    )
    op.create_index(
        "ix_tenant_sso_configs_tenant_id",
        "tenant_sso_configs",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_tenant_sso_configs_created_at", "tenant_sso_configs", ["created_at"], unique=False
    )
    op.create_index(
        "ix_tenant_sso_configs_updated_at", "tenant_sso_configs", ["updated_at"], unique=False
    )
    _enable_rls_and_grants("tenant_sso_configs")

    # --- user_identities ----------------------------------------------------
    op.create_table(
        "user_identities",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_user_identities_user_id"),
            nullable=False,
        ),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint(
            "email_verified IS TRUE",
            name=op.f("ck_user_identities_email_verified_required"),
        ),
        sa.CheckConstraint("subject <> ''", name=op.f("ck_user_identities_subject_required")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_user_identities_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identities")),
        sa.UniqueConstraint("issuer", "subject", name="uq_user_identities_issuer_subject"),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "issuer", name="uq_user_identities_tenant_id_user_id_issuer"
        ),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"], unique=False)
    op.create_index("ix_user_identities_tenant_id", "user_identities", ["tenant_id"], unique=False)
    op.create_index(
        "ix_user_identities_tenant_issuer_subject",
        "user_identities",
        ["tenant_id", "issuer", "subject"],
        unique=False,
    )
    op.create_index(
        "ix_user_identities_created_at", "user_identities", ["created_at"], unique=False
    )
    op.create_index(
        "ix_user_identities_updated_at", "user_identities", ["updated_at"], unique=False
    )
    _enable_rls_and_grants("user_identities")

    # --- scim_tokens ----------------------------------------------------------
    op.create_table(
        "scim_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint("token_hash <> ''", name=op.f("ck_scim_tokens_token_hash_required")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_scim_tokens_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scim_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_scim_tokens_token_hash"),
    )
    op.create_index(
        "ix_scim_tokens_tenant_active", "scim_tokens", ["tenant_id", "active"], unique=False
    )
    op.create_index("ix_scim_tokens_active", "scim_tokens", ["active"], unique=False)
    op.create_index("ix_scim_tokens_tenant_id", "scim_tokens", ["tenant_id"], unique=False)
    op.create_index("ix_scim_tokens_created_at", "scim_tokens", ["created_at"], unique=False)
    op.create_index("ix_scim_tokens_updated_at", "scim_tokens", ["updated_at"], unique=False)
    _enable_rls_and_grants("scim_tokens")

    # --- scim_groups ----------------------------------------------------------
    op.create_table(
        "scim_groups",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_scim_groups_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scim_groups")),
        sa.UniqueConstraint(
            "tenant_id", "display_name", name="uq_scim_groups_tenant_id_display_name"
        ),
    )
    op.create_index("ix_scim_groups_external_id", "scim_groups", ["external_id"], unique=False)
    op.create_index(
        "ix_scim_groups_tenant_external_id",
        "scim_groups",
        ["tenant_id", "external_id"],
        unique=False,
    )
    op.create_index("ix_scim_groups_tenant_id", "scim_groups", ["tenant_id"], unique=False)
    op.create_index("ix_scim_groups_created_at", "scim_groups", ["created_at"], unique=False)
    op.create_index("ix_scim_groups_updated_at", "scim_groups", ["updated_at"], unique=False)
    _enable_rls_and_grants("scim_groups")

    # --- scim_group_members ---------------------------------------------------
    op.create_table(
        "scim_group_members",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "group_id",
            sa.UUID(),
            sa.ForeignKey(
                "scim_groups.id", ondelete="CASCADE", name="fk_scim_group_members_group_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_scim_group_members_user_id"),
            nullable=False,
        ),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("added_by", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scim_group_members")),
        sa.UniqueConstraint("group_id", "user_id", name="uq_scim_group_members_group_id_user_id"),
    )
    op.create_index(
        "ix_scim_group_members_group_id", "scim_group_members", ["group_id"], unique=False
    )
    op.create_index(
        "ix_scim_group_members_user_id", "scim_group_members", ["user_id"], unique=False
    )
    _enable_parent_rls_and_grants("scim_group_members", "scim_groups", "group_id")


def downgrade() -> None:
    """Drop the enterprise-identity tables (destructive owner doctrine)."""
    op.drop_table("scim_group_members")
    op.drop_table("scim_groups")
    op.drop_table("scim_tokens")
    op.drop_table("user_identities")
    op.drop_table("tenant_sso_configs")
