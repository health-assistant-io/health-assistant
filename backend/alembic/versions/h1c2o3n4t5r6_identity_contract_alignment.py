"""H1 identity contract alignment (plan 16 H1, identity-auth §5/§8)

Contract-shaped identity schema (family normative tables, no backwards
compat — dev DBs are recreated):

* ``users`` — rename ``hashed_password`` → ``password_hash`` (§5) and add
  ``full_name``, ``failed_login_attempts``/``locked_until`` (§7 lockout),
  ``token_version`` (§8 ``ver`` revocation), ``oidc_issuer``/``oidc_subject``
  (§14, unique pair). Existing emails are lowercased (§5: lowercased on
  write). Class S extensions ``role`` + ``tenant_id`` stay untouched.
* ``auth_sessions`` (§5) — refresh families: rolling ``expires_at`` +
  30-day ``absolute_expires_at`` cap, ``refresh_jti_hash``,
  ``revoked_at``, ``client_label`` (device list), ``created_at`` (the
  sanctioned product addition).
* ``instance_settings`` (§5) — ``key`` TEXT PK / ``value`` TEXT /
  ``updated_at``; ``auth_mode`` + ``demo_mode`` become DB facts written
  only at initialization (init-only, fail-closed).

Documented deviation (unchanged): PKs stay server-generated
(``gen_random_uuid()``) instead of app-side uuid4.

Revision ID: h1c2o3n4t5r6
Revises: b1y2o3k4s5e6
Create Date: 2026-09-25
"""

from alembic import op
import sqlalchemy as sa


revision = "h1c2o3n4t5r6"
down_revision = "b1y2o3k4s5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- users: §5 column rules -------------------------------------
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "hashed_password",
            new_column_name="password_hash",
            existing_type=sa.String(length=255),
            existing_nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "full_name", sa.String(length=200), nullable=False, server_default=""
            )
        )
        batch_op.add_column(
            sa.Column(
                "failed_login_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "token_version", sa.Integer(), nullable=False, server_default="1"
            )
        )
        batch_op.add_column(
            sa.Column("oidc_issuer", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oidc_subject", sa.String(length=500), nullable=True)
        )

    op.create_unique_constraint(
        "uq_users_oidc_issuer_subject", "users", ["oidc_issuer", "oidc_subject"]
    )

    # §5: email is lowercased on write — normalize pre-existing rows too.
    op.execute("UPDATE users SET email = lower(trim(email))")

    # --- auth_sessions: §5 refresh families --------------------------
    op.create_table(
        "auth_sessions",
        sa.Column("refresh_jti_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "client_label", sa.String(length=200), nullable=False, server_default=""
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_auth_sessions_user_id"), ["user_id"], unique=False
        )

    # --- instance_settings: §5 instance facts ------------------------
    op.create_table(
        "instance_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    # Irreversible by policy (no backwards compatibility — pre-release
    # databases are recreated); reversed here only for dev convenience.
    op.drop_table("instance_settings")
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_auth_sessions_user_id"))
    op.drop_table("auth_sessions")
    op.drop_constraint("uq_users_oidc_issuer_subject", "users", type_="unique")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("oidc_subject")
        batch_op.drop_column("oidc_issuer")
        batch_op.drop_column("token_version")
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_attempts")
        batch_op.drop_column("full_name")
        batch_op.alter_column(
            "password_hash",
            new_column_name="hashed_password",
            existing_type=sa.String(length=255),
            existing_nullable=True,
        )
