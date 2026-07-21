"""add document integration dedup columns + index

Item 3 of the integrations-sdk-improvements plan
(plan: dev/plans/integrations-sdk-improvements-2026-07-21.md).

The ``documents`` table has no ``source_integration_id`` / ``external_id``
columns today. The dedup contract for integration-pulled documents is
"providers advance their own cursor via ``set_sync_cursor``" — fragile
(provider bug / worker crash redelivers the same upstream files) and
unavailable to webhook-driven document ingestion.

This migration mirrors the dedup pattern we shipped on ``examinations``
(``e1x2a3m4i5n6_add_examination_integration_dedup.py``) and
``clinical_events`` (``b1l2i3n4t5ev_add_clinical_event_integration_dedup.py``):

  - ``source_integration_id``: nullable FK to ``user_integrations(id)``
    with ``ON DELETE SET NULL`` (deleting the integration doesn't lose
    the document; the dedup simply stops firing).
  - ``external_id``: free-text — the upstream's stable document id
    (lab report accession #, EHR attachment id, fax message id, ...).
  - Partial unique index on
    ``(tenant_id, patient_id, source_integration_id, external_id)``
    that fires only when all four columns are non-NULL. UI uploads
    (both fields NULL) bypass it; integration-sourced rows are deduped
    at the DB layer (catches the race window between the service's
    SELECT and INSERT).

Two new non-unique indexes (``ix_documents_source_integration_id`` and
``ix_documents_external_id``) keep the engine's lookup-by-key path fast.

Revision ID: d1o2c3u4m5e6
Revises: g1h2i3t4l5pr
Create Date: 2026-07-21
"""

from alembic import op


revision = "d1o2c3u4m5e6"
down_revision = "g1h2i3t4l5pr"
branch_labels = None
depends_on = None


_DEDUP_INDEX = "uq_document_integration_dedup"
_SRC_IX = "ix_documents_source_integration_id"
_EXT_IX = "ix_documents_external_id"


def upgrade() -> None:
    # ``source_integration_id`` — FK to user_integrations with ON DELETE SET NULL
    # (matches the pattern on examinations + clinical_events: deleting the
    # integration preserves the document row, the dedup just stops firing).
    op.execute(
        """
        ALTER TABLE documents
          ADD COLUMN IF NOT EXISTS source_integration_id UUID
            REFERENCES user_integrations(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        ALTER TABLE documents
          ADD COLUMN IF NOT EXISTS external_id VARCHAR(255)
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS {_SRC_IX} ON documents (source_integration_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS {_EXT_IX} ON documents (external_id)")
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_DEDUP_INDEX}
        ON documents (tenant_id, patient_id, source_integration_id, external_id)
        WHERE source_integration_id IS NOT NULL
          AND external_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_DEDUP_INDEX}")
    op.execute(f"DROP INDEX IF EXISTS {_EXT_IX}")
    op.execute(f"DROP INDEX IF EXISTS {_SRC_IX}")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS external_id")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS source_integration_id")
