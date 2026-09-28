"""H5 TOTP MFA columns on users (plan 16 H5, identity-auth §5.14/§20).

Adds the four MFA columns to ``users`` (no table rename, §5-consistent
additive change):

* ``mfa_secret_enc`` TEXT(512) NULL — the base32 TOTP secret,
  Fernet-encrypted under the DATA_KEY family (``enc::`` prefix, never
  plaintext at rest).
* ``mfa_recovery_codes`` TEXT NULL — JSON array of bcrypt hashes of the
  remaining single-use recovery codes (consumption removes the hash).
* ``mfa_pending`` JSONB NULL — an in-flight enrollment
  ``{"secret_enc": ..., "recovery": [hashes]}`` between
  ``/me/mfa/enroll`` and ``/me/mfa/confirm`` (also used by the
  admin-forced login-time enrollment path).
* ``mfa_enforced`` BOOLEAN NOT NULL DEFAULT false — admin-forced MFA;
  the user's next password login then requires enrollment before the
  challenge passes.

Revision ID: h5m2f3a4t6o7
Revises: a1u2d3i4t5e6
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "h5m2f3a4t6o7"
down_revision = "a1u2d3i4t5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("mfa_secret_enc", sa.String(length=512), nullable=True)
        )
        batch_op.add_column(sa.Column("mfa_recovery_codes", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "mfa_pending", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "mfa_enforced",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )


def downgrade() -> None:
    # Reversed for dev convenience only (H1 policy: no backwards
    # compatibility — pre-release databases are recreated).
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("mfa_enforced")
        batch_op.drop_column("mfa_pending")
        batch_op.drop_column("mfa_recovery_codes")
        batch_op.drop_column("mfa_secret_enc")
