"""Service-account flag on users (F19)

Revision ID: f19a2b3c4d5e
Revises: i7b8c9d0e1f2
Create Date: 2026-07-01

Adds ``users.is_service_account`` (BOOLEAN NOT NULL DEFAULT false) and relaxes
``users.hashed_password`` to nullable so password-less machine accounts can be
created. Service accounts are minted by admins via ``POST /auth/service-account``
and carry a long-lived JWT with ``is_service_account=True`` — they authenticate
against the FHIR facade and REST API as a bearer token without a password.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f19a2b3c4d5e"
down_revision: Union[str, Sequence[str], None] = "i7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS is_service_account BOOLEAN DEFAULT false"
    )
    op.execute(
        "UPDATE users SET is_service_account = false WHERE is_service_account IS NULL"
    )
    op.alter_column(
        "users", "is_service_account",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default="false",
    )
    # Relax hashed_password so service accounts (which have no password) can be created.
    op.alter_column(
        "users", "hashed_password",
        existing_type=sa.String(255),
        nullable=True,
    )
    op.create_index(
        "ix_users_is_service_account",
        "users",
        ["is_service_account"],
        postgresql_where=sa.text("is_service_account = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_is_service_account", table_name="users")
    # Re-set NULL passwords to a dummy hash before re-tightening.
    op.execute(
        "UPDATE users SET hashed_password = '!' WHERE hashed_password IS NULL"
    )
    op.alter_column(
        "users", "hashed_password",
        existing_type=sa.String(255),
        nullable=False,
    )
    op.alter_column(
        "users", "is_service_account",
        existing_type=sa.Boolean(),
        nullable=True,
        server_default=None,
    )
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_service_account")
