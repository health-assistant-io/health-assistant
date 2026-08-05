"""state biomarkers: value_type discriminator + state catalog

Adds categorical / qualitative value support alongside the existing numeric
biomarker model. The plan is ``dev/plans/state-biomarkers-2026-08-05.md``.

The consolidated baseline (``8ddb7ef7ca4d``) also defines this schema for
fresh installs, so this incremental migration uses ``IF NOT EXISTS`` /
exception-guarded DDL throughout — it's idempotent whether the schema was
created by the baseline or by this migration.

Schema changes:

* ``biomarker_definitions.value_type`` — NOT NULL enum discriminator
  (``quantity`` | ``state``), default ``quantity``.
* ``biomarker_definitions.supports_multi_state`` — NOT NULL boolean,
  default FALSE.
* Two CHECK constraints on ``biomarker_definitions``.
* ``biomarker_states`` — universal catalog, unique on ``(code, system)``.
* ``biomarker_allowed_states`` — join table.

Revision ID: s1t2a3t4e5b6
Revises: t1e2l3o4n5g6
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "s1t2a3t4e5b6"
down_revision = "t1e2l3o4n5g6"
branch_labels = None
depends_on = None


def _constraint_exists(name: str, table: str) -> bool:
    """Check if a CHECK constraint already exists (avoids duplicate_object)."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname = :n "
            "AND conrelid = (SELECT oid FROM pg_class WHERE relname = :t)"
        ),
        {"n": name, "t": table},
    )
    return result.scalar() is not None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column already exists (the baseline may have created it)."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.scalar() is not None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
        ),
        {"t": table},
    )
    return result.scalar() is not None


def _index_exists(name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
        {"n": name},
    )
    return result.scalar() is not None


def _enum_exists(name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"),
        {"n": name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    # --- enum type ---
    if not _enum_exists("biomarkervaluetype"):
        op.execute(
            "CREATE TYPE biomarkervaluetype AS ENUM ('quantity', 'state')"
        )

    # --- biomarker_definitions columns (idempotent — baseline may have them) ---
    if not _column_exists("biomarker_definitions", "value_type"):
        op.execute(
            "ALTER TABLE biomarker_definitions "
            "ADD COLUMN value_type biomarkervaluetype NOT NULL DEFAULT 'quantity'"
        )
    if not _column_exists("biomarker_definitions", "supports_multi_state"):
        op.execute(
            "ALTER TABLE biomarker_definitions "
            "ADD COLUMN supports_multi_state BOOLEAN NOT NULL DEFAULT false"
        )

    if not _index_exists("ix_biomarker_definitions_value_type"):
        op.execute(
            "CREATE INDEX ix_biomarker_definitions_value_type "
            "ON biomarker_definitions (value_type)"
        )

    # CHECK constraints (can't use IF NOT EXISTS — guard via lookup).
    if not _constraint_exists(
        "ck_biomarker_definitions_state_not_telemetry", "biomarker_definitions"
    ):
        op.create_check_constraint(
            "ck_biomarker_definitions_state_not_telemetry",
            "biomarker_definitions",
            "is_telemetry = FALSE OR value_type != 'state'",
        )
    if not _constraint_exists(
        "ck_biomarker_definitions_state_no_unit", "biomarker_definitions"
    ):
        op.create_check_constraint(
            "ck_biomarker_definitions_state_no_unit",
            "biomarker_definitions",
            "value_type != 'state' OR preferred_unit_id IS NULL",
        )

    if not _column_exists("biomarker_states", "category"):
        op.execute(
            "ALTER TABLE biomarker_states ADD COLUMN category VARCHAR(80)"
        )

    # --- biomarker_states table ---
    if not _table_exists("biomarker_states"):
        op.execute(
            """
            CREATE TABLE biomarker_states (
                slug VARCHAR(80) NOT NULL,
                code VARCHAR(100) NOT NULL,
                system VARCHAR(255) NOT NULL,
                display VARCHAR(255) NOT NULL,
                description TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                id UUID NOT NULL DEFAULT gen_random_uuid(),
                created_by UUID,
                updated_by UUID,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                CONSTRAINT uq_biomarker_states_code_system UNIQUE (code, system),
                CONSTRAINT biomarker_states_pkey PRIMARY KEY (id)
            )
            """
        )
    if not _index_exists("ix_biomarker_states_slug"):
        op.execute(
            "CREATE UNIQUE INDEX ix_biomarker_states_slug ON biomarker_states (slug)"
        )
    if not _index_exists("ix_biomarker_states_created_at"):
        op.execute(
            "CREATE INDEX ix_biomarker_states_created_at ON biomarker_states (created_at)"
        )
    if not _index_exists("ix_biomarker_states_updated_at"):
        op.execute(
            "CREATE INDEX ix_biomarker_states_updated_at ON biomarker_states (updated_at)"
        )

    # --- biomarker_allowed_states table ---
    if not _table_exists("biomarker_allowed_states"):
        op.execute(
            """
            CREATE TABLE biomarker_allowed_states (
                biomarker_id UUID NOT NULL,
                state_id UUID NOT NULL,
                is_normal BOOLEAN NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                id UUID NOT NULL DEFAULT gen_random_uuid(),
                CONSTRAINT uq_biomarker_allowed_states UNIQUE (biomarker_id, state_id),
                CONSTRAINT biomarker_allowed_states_pkey PRIMARY KEY (id),
                CONSTRAINT biomarker_allowed_states_biomarker_id_fkey
                    FOREIGN KEY (biomarker_id) REFERENCES biomarker_definitions(id)
                    ON DELETE CASCADE,
                CONSTRAINT biomarker_allowed_states_state_id_fkey
                    FOREIGN KEY (state_id) REFERENCES biomarker_states(id)
                    ON DELETE RESTRICT
            )
            """
        )
    if not _index_exists("ix_biomarker_allowed_states_biomarker_id"):
        op.execute(
            "CREATE INDEX ix_biomarker_allowed_states_biomarker_id "
            "ON biomarker_allowed_states (biomarker_id)"
        )
    if not _index_exists("ix_biomarker_allowed_states_state_id"):
        op.execute(
            "CREATE INDEX ix_biomarker_allowed_states_state_id "
            "ON biomarker_allowed_states (state_id)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_biomarker_allowed_states_state_id")
    op.execute("DROP INDEX IF EXISTS ix_biomarker_allowed_states_biomarker_id")
    op.execute("DROP TABLE IF EXISTS biomarker_allowed_states")

    op.execute("DROP INDEX IF EXISTS ix_biomarker_states_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_biomarker_states_created_at")
    op.execute("DROP INDEX IF EXISTS ix_biomarker_states_slug")
    op.execute("DROP TABLE IF EXISTS biomarker_states")

    op.execute(
        "ALTER TABLE biomarker_definitions "
        "DROP CONSTRAINT IF EXISTS ck_biomarker_definitions_state_no_unit"
    )
    op.execute(
        "ALTER TABLE biomarker_definitions "
        "DROP CONSTRAINT IF EXISTS ck_biomarker_definitions_state_not_telemetry"
    )
    op.execute("DROP INDEX IF EXISTS ix_biomarker_definitions_value_type")
    op.execute(
        "ALTER TABLE biomarker_definitions DROP COLUMN IF EXISTS supports_multi_state"
    )
    op.execute("ALTER TABLE biomarker_definitions DROP COLUMN IF EXISTS value_type")
    op.execute("DROP TYPE IF EXISTS biomarkervaluetype")
