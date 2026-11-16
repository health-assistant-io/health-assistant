"""Session device metadata + security notification vocabulary (Program Phase 12C1).

Three nullable columns on ``auth_sessions`` — the family row is already the
device record (identity-auth §5/§12), it just carried no device facts:

* ``ip`` — the client address observed at issuance (``String(45)``: the
  widest textual IP, IPv6 with a zone suffix), from
  ``security_policy.request_client_ip`` (proxy-header aware).
* ``user_agent`` — the raw request ``User-Agent`` (sanitized upstream by
  ``device_hint`` for the label; this column keeps the bounded original for
  the device list, ``String(512)``).
* ``last_seen_at`` — the throttled activity stamp (≥60 s granularity,
  written on session verification), which is what
  ``GET /me/sessions`` reports.

All three are nullable: existing families and the auth layer's own
issuance paths (refresh rotation, test helpers) keep working without them,
and a NULL is "unknown", never a default. Row-level security is unchanged
— ``auth_sessions`` stays the ``users``-anchored child of ``0016_rls``
(nothing here is a new policy surface, only three data columns).

The revision also widens two vocabularies the login-alert feature reads:

* ``notificationtype`` gains ``SECURITY_EVENT``;
* ``notificationcategory`` gains ``security``.

Postgres cannot drop an enum label, so downgrade removes the columns and
leaves the two labels in place (harmless for older code — an unused label
is not a membership rule); the upgrade statements use ``IF NOT EXISTS`` so
a downgrade/upgrade round-trip is clean.

Downgrade drops the columns (the owner doctrine applies — this program has
no production users yet).

Revision ID: 0019_session_metadata
Revises: 0018_enterprise_identity
Create Date: 2026-11-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_session_metadata"
down_revision: str | Sequence[str] | None = "0018_enterprise_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the device columns and widen the notification vocabularies."""
    op.add_column("auth_sessions", sa.Column("ip", sa.String(length=45), nullable=True))
    op.add_column("auth_sessions", sa.Column("user_agent", sa.String(length=512), nullable=True))
    op.add_column(
        "auth_sessions",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'SECURITY_EVENT'")
    op.execute("ALTER TYPE notificationcategory ADD VALUE IF NOT EXISTS 'security'")


def downgrade() -> None:
    """Drop the device columns (enum labels stay — Postgres cannot remove one)."""
    op.drop_column("auth_sessions", "last_seen_at")
    op.drop_column("auth_sessions", "user_agent")
    op.drop_column("auth_sessions", "ip")
