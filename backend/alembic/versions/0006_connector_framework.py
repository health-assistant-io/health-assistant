"""Connector framework: typed credentials, first-class sync state.

One revision (plan 8 W1; parent plan constitution rules 6, 7a, 8, 10):

**``integration_credentials``** splits secret material out of
``user_integrations.user_config``. One sealed slot per
``(integration, kind)``: the at-rest cipher wrapper (context-bound to the
integration id) plus its ``kid`` as a typed column, the provider-declared
scope grant, expiry columns and ``rotated_at``/``revoked_at``. Rotation
reseals under the primary key's new ``_kid``; revocation fail-closes every
read (the sync engine raises the typed ``credential`` class and writes no
data).

**``integration_sync_state``** makes cursors and sync health rows instead of
``user_config["_sync_state"]`` JSONB: ``stream`` is the closed data-class
vocabulary (observations | documents | appointments | labs | telemetry),
``cursor_key`` the provider-scoped sub-cursor inside a stream (empty = the
stream's primary cursor), ``cursor_kind`` the closed timestamp | page_token |
etag | commit vocabulary. ``next_retry_at`` carries backoff as data — the
beat reads it instead of a code-embedded/Redis cooldown — and
``consecutive_failures`` / ``last_error_class`` record typed failure health
until a success resets it.

**``integration_sync_logs.error_class``** is the typed failure class on the
append-only run history (charter error codes + the engine's ``mapping_gap``
and ``credential`` classes), so routing never string-parses a message.

Data policy (owner doctrine, no users): the old JSONB cursor bag is
deliberately discarded, not backfilled. Downgrade restores the previous DDL
shape — the two tables and the log column disappear.

Revision ID: 0006_connector_framework
Revises: 0005_telemetry_unified
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_connector_framework"
down_revision: str | Sequence[str] | None = "0005_telemetry_unified"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREDENTIAL_KINDS = ("oauth2", "api_key", "signed")
_SYNC_STREAMS = ("observations", "documents", "appointments", "labs", "telemetry")
_CURSOR_KINDS = ("timestamp", "page_token", "etag", "commit")
_ERROR_CLASSES = (
    "not-ready",
    "needs-secret",
    "invalid",
    "unsupported",
    "io",
    "not-found",
    "path-denied",
    "net-denied",
    "auth",
    "rate-limited",
    "unreachable",
    "protocol",
    "provider",
    "budget",
    "canceled",
    "mapping_gap",
    "credential",
)


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_integration_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("sealed_blob", sa.Text(), nullable=False),
        sa.Column("kid", sa.String(length=64), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.String(length=255)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.CheckConstraint(
            f"kind IN ({_in(_CREDENTIAL_KINDS)})",
            name=op.f("ck_integration_credentials_integration_credential_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_integration_credentials_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_integration_credentials_user_integration_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_credentials")),
        sa.UniqueConstraint(
            "user_integration_id",
            "kind",
            name=op.f("uq_integration_credentials_integration_kind"),
        ),
    )
    for name, columns in (
        ("ix_integration_credentials_created_at", ["created_at"]),
        ("ix_integration_credentials_tenant_id", ["tenant_id"]),
        ("ix_integration_credentials_tenant_kid", ["tenant_id", "kid"]),
        ("ix_integration_credentials_updated_at", ["updated_at"]),
        ("ix_integration_credentials_user_integration_id", ["user_integration_id"]),
    ):
        op.create_index(name, "integration_credentials", columns, unique=False)

    op.create_table(
        "integration_sync_state",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_integration_id", sa.UUID(), nullable=False),
        sa.Column("stream", sa.String(length=16), nullable=False),
        sa.Column("cursor_key", sa.String(length=96), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "cursor_kind",
            sa.String(length=16),
            server_default=sa.text("'timestamp'"),
            nullable=False,
        ),
        sa.Column("cursor_value", sa.Text(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.String(length=24), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.CheckConstraint(
            f"stream IN ({_in(_SYNC_STREAMS)})",
            name=op.f("ck_integration_sync_state_integration_sync_stream"),
        ),
        sa.CheckConstraint(
            f"cursor_kind IN ({_in(_CURSOR_KINDS)})",
            name=op.f("ck_integration_sync_state_integration_cursor_kind"),
        ),
        sa.CheckConstraint(
            f"last_error_class IN ({_in(_ERROR_CLASSES)})",
            name=op.f("ck_integration_sync_state_integration_sync_error_class"),
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name=op.f("ck_integration_sync_state_consecutive_failures_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_integration_sync_state_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_integration_sync_state_user_integration_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_sync_state")),
        sa.UniqueConstraint(
            "user_integration_id",
            "stream",
            "cursor_key",
            name=op.f("uq_integration_sync_state_stream_cursor"),
        ),
    )
    for name, columns in (
        ("ix_integration_sync_state_created_at", ["created_at"]),
        ("ix_integration_sync_state_next_retry_at", ["next_retry_at"]),
        ("ix_integration_sync_state_tenant_id", ["tenant_id"]),
        ("ix_integration_sync_state_tenant_retry", ["tenant_id", "next_retry_at"]),
        ("ix_integration_sync_state_updated_at", ["updated_at"]),
        ("ix_integration_sync_state_user_integration_id", ["user_integration_id"]),
    ):
        op.create_index(name, "integration_sync_state", columns, unique=False)

    op.add_column(
        "integration_sync_logs",
        sa.Column("error_class", sa.String(length=24), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_integration_sync_logs_integration_sync_error_class"),
        "integration_sync_logs",
        f"error_class IN ({_in(_ERROR_CLASSES)})",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_integration_sync_logs_integration_sync_error_class"),
        "integration_sync_logs",
        type_="check",
    )
    op.drop_column("integration_sync_logs", "error_class")

    op.drop_table("integration_sync_state")
    op.drop_table("integration_credentials")
