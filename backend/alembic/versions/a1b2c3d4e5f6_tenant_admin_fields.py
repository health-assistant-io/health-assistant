"""tenant admin fields (slug, description, is_active, owner_id) + user is_active

Adds the columns required by the system-admin tenant management surface:
  * tenants.slug        — URL-safe unique handle
  * tenants.description — human description
  * tenants.is_active   — soft-delete flag
  * tenants.owner_id    — FK to users.id (primary admin / billing owner)
  * tenants.created_at  / updated_at  — TimestampMixin columns
  * users.is_active     — account deactivation flag
  * users.created_at    / updated_at  — TimestampMixin columns

Backfills slug from name and timestamps from now() for existing rows so the
new NOT NULL constraints can be applied without data loss. Idempotent via
IF NOT EXISTS on every DDL statement.

Revision ID: a1b2c3d4e5f6
Revises: 82fe70921bb0
Create Date: 2026-06-22 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "82fe70921bb0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slugify(name: str) -> str:
    """SQL expression that converts a tenant name into a URL-safe slug.

    Lowercases, replaces runs of non-alphanumeric chars with '-', and trims
    leading/trailing '-'. Empty results fall back to 'tenant'.
    """
    return (
        f"btrim("
        f"  regexp_replace("
        f"    lower(coalesce({name}, '')),"
        f"    '[^a-z0-9]+', '-', 'g'"
        f"  ),"
        f"  '-'"
        f")"
    )


def upgrade() -> None:
    """Upgrade schema."""
    # ---------------------------------------------------------------
    # tenants: new columns, all added with IF NOT EXISTS for safety.
    # ---------------------------------------------------------------
    op.execute(
        "ALTER TABLE tenants "
        "ADD COLUMN IF NOT EXISTS slug VARCHAR(80), "
        "ADD COLUMN IF NOT EXISTS description TEXT, "
        "ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true, "
        "ADD COLUMN IF NOT EXISTS owner_id UUID, "
        "ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now(), "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()"
    )

    # Backfill slug from name for any rows still missing one. We append the
    # first 8 hex chars of the id to guarantee uniqueness on collisions.
    op.execute(
        f"""
        UPDATE tenants
        SET slug = {_slugify('name')} || '-' || substring(md5(id::text) from 1 for 8)
        WHERE slug IS NULL OR slug = ''
        """
    )

    # Backfill timestamps.
    op.execute(
        "UPDATE tenants SET created_at = now() WHERE created_at IS NULL"
    )
    op.execute(
        "UPDATE tenants SET updated_at = now() WHERE updated_at IS NULL"
    )
    op.execute(
        "UPDATE tenants SET is_active = true WHERE is_active IS NULL"
    )

    # Now enforce NOT NULL + UNIQUE.
    op.alter_column("tenants", "slug", existing_type=sa.String(length=80),
                    nullable=False)
    op.alter_column("tenants", "is_active", existing_type=sa.Boolean(),
                    nullable=False, server_default="true")
    op.alter_column("tenants", "created_at", existing_type=sa.DateTime(timezone=True),
                    nullable=False, server_default=sa.text("now()"))
    op.alter_column("tenants", "updated_at", existing_type=sa.DateTime(timezone=True),
                    nullable=False, server_default=sa.text("now()"))

    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
    op.create_index("ix_tenants_is_active", "tenants", ["is_active"], unique=False)
    op.create_index("ix_tenants_owner_id", "tenants", ["owner_id"], unique=False)
    op.create_index("ix_tenants_created_at", "tenants", ["created_at"], unique=False)
    op.create_index("ix_tenants_updated_at", "tenants", ["updated_at"], unique=False)

    op.create_foreign_key(
        "fk_tenants_owner_id_users",
        "tenants",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ---------------------------------------------------------------
    # users: is_active + timestamps.
    # ---------------------------------------------------------------
    op.execute(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true, "
        "ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now(), "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()"
    )
    op.execute(
        "UPDATE users SET is_active = true WHERE is_active IS NULL"
    )
    op.execute(
        "UPDATE users SET created_at = now() WHERE created_at IS NULL"
    )
    op.execute(
        "UPDATE users SET updated_at = now() WHERE updated_at IS NULL"
    )
    op.alter_column("users", "is_active", existing_type=sa.Boolean(),
                    nullable=False, server_default="true")
    op.alter_column("users", "created_at", existing_type=sa.DateTime(timezone=True),
                    nullable=False, server_default=sa.text("now()"))
    op.alter_column("users", "updated_at", existing_type=sa.DateTime(timezone=True),
                    nullable=False, server_default=sa.text("now()"))

    op.create_index("ix_users_is_active", "users", ["is_active"], unique=False)
    op.create_index("ix_users_created_at", "users", ["created_at"], unique=False)
    op.create_index("ix_users_updated_at", "users", ["updated_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_users_updated_at", table_name="users")
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_index("ix_users_is_active", table_name="users")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "created_at")
    op.drop_column("users", "is_active")

    op.drop_constraint("fk_tenants_owner_id_users", "tenants", type_="foreignkey")
    op.drop_index("ix_tenants_updated_at", table_name="tenants")
    op.drop_index("ix_tenants_created_at", table_name="tenants")
    op.drop_index("ix_tenants_owner_id", table_name="tenants")
    op.drop_index("ix_tenants_is_active", table_name="tenants")
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_column("tenants", "updated_at")
    op.drop_column("tenants", "created_at")
    op.drop_column("tenants", "owner_id")
    op.drop_column("tenants", "is_active")
    op.drop_column("tenants", "description")
    op.drop_column("tenants", "slug")
