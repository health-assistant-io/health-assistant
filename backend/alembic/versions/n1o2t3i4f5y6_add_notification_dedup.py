"""add notification digest_key + dedup_expires_at

Item 4 of the integrations-sdk-improvements plan
(plan: dev/plans/integrations-sdk-improvements-2026-07-21.md).

Today ``notification_service.emit`` always inserts a new Notification
row. Providers that emit threshold alerts (e.g. "HR > 100" on every
sync) flood the user's inbox.

This migration adds two columns + an index so the platform can collapse
repeated emissions of the same logical event into a single active
Notification row inside a TTL window:

  - ``dedup_key`` VARCHAR(64) — the provider-supplied key (typically
    ``"{domain}:{type_id}:{scope}"``).
  - ``dedup_expires_at`` TIMESTAMPTZ — after this timestamp, a new
    emission with the same key creates a fresh row (so a daily summary
    can be emitted once per day, not once ever).
  - Non-unique composite index ``ix_notification_dedup_lookup`` on
    ``(tenant_id, dedup_key, dedup_expires_at)`` so the engine's
    ``SELECT ... WHERE tenant_id=? AND dedup_key=? AND dedup_expires_at
    > now()`` lookup is fast.

Design note: no unique partial index. The TTL semantics require that
multiple rows with the same ``(tenant_id, dedup_key)`` CAN coexist over
time (the old one expired, a new one was inserted). Postgres partial
indexes can't reference ``now()`` in the predicate (non-immutable), so
a unique constraint can't enforce "at most one ACTIVE row". The
application-level lookup-then-insert in ``notification_service.emit``
handles the common case; the race window is benign (worst case: two
notifications emitted instead of one — the pre-item-4 behaviour).

Revision ID: n1o2t3i4f5y6
Revises: d1o2c3u4m5e6
Create Date: 2026-07-21
"""

from alembic import op


revision = "n1o2t3i4f5y6"
down_revision = "d1o2c3u4m5e6"
branch_labels = None
depends_on = None


_KEY_IX = "ix_notifications_dedup_key"
_LOOKUP_IX = "ix_notification_dedup_lookup"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE notifications
          ADD COLUMN IF NOT EXISTS dedup_key VARCHAR(64)
        """
    )
    op.execute(
        """
        ALTER TABLE notifications
          ADD COLUMN IF NOT EXISTS dedup_expires_at TIMESTAMPTZ
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS {_KEY_IX} ON notifications (dedup_key)")
    # Composite index tuned for the lookup-then-insert path in emit().
    # Partial (dedup_key IS NOT NULL) so it doesn't bloat for the many
    # notifications that don't use digestion.
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {_LOOKUP_IX}
        ON notifications (tenant_id, dedup_key, dedup_expires_at)
        WHERE dedup_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_LOOKUP_IX}")
    op.execute(f"DROP INDEX IF EXISTS {_KEY_IX}")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS dedup_expires_at")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS dedup_key")
