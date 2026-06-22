"""add_missing_enum_values

Reconcile three PG enums whose Python enum counterparts define values that
were never added to the database:

- ``medicationstatus`` is missing ``INACTIVE`` and ``CANCELLED``. The
  initial schema created 7 values; the ``002fb3b9f7fe_standardize_enum_values``
  migration only renamed existing lowercase forms to uppercase — it never
  added the missing ones. Creating ``Medication(status=INACTIVE)`` raised
  ``DataError``.

- ``allergycriticality`` has the wrong token. Initial schema created
  ``unable_to_assess`` (underscore); the standardize migration tried to
  rename ``unable-to-assess`` (hyphen) → silently no-op'd. The Python
  ``AllergyCriticality.UNABLE_TO_ASSESS`` value did not exist in the DB.

- ``aiscope`` is missing ``ORGANIZATION``. Migration ``2983797a70d0``
  created only SYSTEM/TENANT/USER. Creating an ``AIProviderModel`` or
  ``AITaskAssignment`` with ``scope=ORGANIZATION`` raised ``DataError``.

This migration is forward-only and idempotent (uses ``IF NOT EXISTS`` where
supported). PG 12+ supports ``ALTER TYPE ... ADD VALUE`` inside a transaction
block, so no ``COMMIT`` escape is needed.

Revision ID: 7a9c2e1b4f3a
Revises: 34414d55a822
Create Date: 2026-06-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "7a9c2e1b4f3a"
down_revision: Union[str, Sequence[str], None] = "34414d55a822"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "  SELECT 1 FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "  WHERE typname = 'medicationstatus' AND enumlabel = 'INACTIVE'"
        ") THEN "
        "  ALTER TYPE medicationstatus ADD VALUE 'INACTIVE'; "
        "END IF; END $$;"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "  SELECT 1 FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "  WHERE typname = 'medicationstatus' AND enumlabel = 'CANCELLED'"
        ") THEN "
        "  ALTER TYPE medicationstatus ADD VALUE 'CANCELLED'; "
        "END IF; END $$;"
    )

    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS ("
        "  SELECT 1 FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "  WHERE typname = 'allergycriticality' AND enumlabel = 'unable_to_assess'"
        ") AND NOT EXISTS ("
        "  SELECT 1 FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "  WHERE typname = 'allergycriticality' AND enumlabel = 'UNABLE_TO_ASSESS'"
        ") THEN "
        "  ALTER TYPE allergycriticality RENAME VALUE 'unable_to_assess' TO 'UNABLE_TO_ASSESS'; "
        "END IF; END $$;"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "  SELECT 1 FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "  WHERE typname = 'allergycriticality' AND enumlabel = 'UNABLE_TO_ASSESS'"
        ") THEN "
        "  ALTER TYPE allergycriticality ADD VALUE 'UNABLE_TO_ASSESS'; "
        "END IF; END $$;"
    )

    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "  SELECT 1 FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "  WHERE typname = 'aiscope' AND enumlabel = 'ORGANIZATION'"
        ") THEN "
        "  ALTER TYPE aiscope ADD VALUE 'ORGANIZATION'; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    pass
