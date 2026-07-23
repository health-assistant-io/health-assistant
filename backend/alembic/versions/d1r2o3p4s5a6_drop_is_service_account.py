"""drop users.is_service_account (F19 service accounts retired)

The long-lived service-account JWT path (POST /auth/service-account) is
retired in favor of OAuth2 clients (POST /oauth/clients +
POST /oauth/token) with SMART-on-FHIR scopes — see
dev/plans/api-access-layers-2026-07-23.md (Phase 3). The ``is_service_account``
flag and its login-reject check are no longer referenced; this drops the
column. Any leftover service-account rows are inert (NULL password, unable to
log in) and left in place to avoid surprising cascade deletes.

Revision ID: d1r2o3p4s5a6
Revises: o1a2u3t4h5n6
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "d1r2o3p4s5a6"
down_revision = "o1a2u3t4h5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("is_service_account")


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_service_account",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            )
        )
