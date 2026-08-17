"""Add mobile_push_targets for native push delivery (Phase 8).

One row per (user, device) registered for native push. Sibling to
``notification_subscriptions`` (Web Push / VAPID, used by the PWA). The
mobile app registers a device on first run after onboarding; the push
dispatch task reads active rows for a recipient user and POSTs the
notification payload to the device's endpoint.

Origin: app/dev/plans/bridge-sdk-integrations-enhancement-2026-08-12.md
        Phase 8 (cross-repo bridge enhancement plan).

Revision ID: m1o2b3i4l5e6
Revises: a1d2c3a4t5e6
Create Date: 2026-08-12
"""
from alembic import op

revision = "m1o2b3i4l5e6"
down_revision = "a1d2c3a4t5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mobile_push_targets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            device_id VARCHAR(255) NOT NULL,
            platform VARCHAR(32) NOT NULL,
            endpoint_url TEXT NOT NULL,
            encryption_pubkey TEXT,
            app_version VARCHAR(64),
            user_agent VARCHAR(512),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_seen_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mobile_push_targets_user_active
        ON mobile_push_targets (user_id, is_active)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mobile_push_targets_user_device
        ON mobile_push_targets (user_id, device_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_mobile_push_targets_user_device")
    op.execute("DROP INDEX IF EXISTS idx_mobile_push_targets_user_active")
    op.execute("DROP TABLE IF EXISTS mobile_push_targets")
