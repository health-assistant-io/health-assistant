"""Observation.interpretation: String -> JSONB (I6)

Revision ID: i6a7b8c9d0e1
Revises: f11a2b3c4d5e
Create Date: 2026-06-30

Stores the canonical FHIR R4 interpretation shape (``0..* CodeableConcept``,
a list) instead of a flattened display string. The backfill wraps legacy
plain-string rows in ``[{"text": <value>}]`` so the round-trip
import → export preserves the LOINC/OBSINT coding instead of collapsing
to a lossy display string.

The reverse (downgrade) flattens the list back to a display string using
the same precedence as ``_flatten_interpretation`` (coding.display →
coding.code → text).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "i6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "f11a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE fhir_observations
        ALTER COLUMN interpretation TYPE JSONB
        USING CASE
            WHEN interpretation IS NULL THEN NULL
            WHEN interpretation ~ '^\\s*(\\[|\\{)' THEN interpretation::jsonb
            ELSE jsonb_build_array(jsonb_build_object('text', interpretation))
        END
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE fhir_observations
        ALTER COLUMN interpretation TYPE TEXT
        USING CASE
            WHEN interpretation IS NULL THEN NULL
            WHEN jsonb_typeof(interpretation) = 'array'
                 AND jsonb_array_length(interpretation) > 0
            THEN COALESCE(
                interpretation->0->'coding'->0->>'display',
                interpretation->0->'coding'->0->>'code',
                interpretation->0->>'text',
                ''
            )
            ELSE interpretation::text
        END
        """
    )
