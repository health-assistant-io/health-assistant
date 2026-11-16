
"""documents.object_key: server-generated object keys for document bytes

Revision ID: 0025_document_object_key
Revises: 0024_catalog_cleanup
Create Date: 2026-11-14

Health's documents were the last durable byte root on a host path: the row
stored an absolute ``file_path`` and Celery/DICOM consumed that path. The
family object-storage contract moves the bytes behind an ``ObjectStore`` and
stores a server-generated key instead.

This revision only adds the nullable indexed ``object_key`` column. It does
**not** backfill keys: the bytes are still at the pre-contract ``file_path``
until a one-shot migration copies them, and a backfilled row with a missing
object would make the worker prefer an unreadable key. New writes set the
column; the facade's dual-read (and the worker's local fallback) keeps every
existing row working. The copy → verify → key-backfill → flip sequence is the
migration tool's job.

Downgrade drops the index and the column; the pre-contract ``file_path`` rows
are intact, so the old code path remains valid.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025_document_object_key"
down_revision = "0024_catalog_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("object_key", sa.String(length=1024), nullable=True))
    op.create_index("ix_documents_object_key", "documents", ["object_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_documents_object_key", table_name="documents")
    op.drop_column("documents", "object_key")
