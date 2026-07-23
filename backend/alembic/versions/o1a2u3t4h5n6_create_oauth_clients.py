"""create oauth_clients table

Registers external API consumers for the FHIR R4 facade. Each client is
bound to one tenant and carries SMART-on-FHIR scopes; access tokens are
stateless JWTs (no oauth_access_tokens table). See docs/API_LAYERS.md and
dev/plans/api-access-layers-2026-07-23.md.

Revision ID: o1a2u3t4h5n6
Revises: v1a2c3c4i5n6
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "o1a2u3t4h5n6"
down_revision = "v1a2c3c4i5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('oauth_clients',
        sa.Column('id', sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('client_secret_hash', sa.String(length=255), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('scopes', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('bound_patient_id', sa.UUID(), nullable=True),
        sa.Column('is_confidential', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index("ix_oauth_clients_client_id", "oauth_clients", ["client_id"], unique=True)
    op.create_index("ix_oauth_clients_tenant_id", "oauth_clients", ["tenant_id"], unique=False)
    op.create_index(
        "ix_oauth_clients_bound_patient_id", "oauth_clients", ["bound_patient_id"], unique=False
    )
    op.create_index("ix_oauth_clients_is_active", "oauth_clients", ["is_active"], unique=False)
    op.create_index("ix_oauth_clients_created_at", "oauth_clients", ["created_at"], unique=False)
    op.create_index("ix_oauth_clients_updated_at", "oauth_clients", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_oauth_clients_updated_at", table_name="oauth_clients")
    op.drop_index("ix_oauth_clients_created_at", table_name="oauth_clients")
    op.drop_index("ix_oauth_clients_is_active", table_name="oauth_clients")
    op.drop_index("ix_oauth_clients_bound_patient_id", table_name="oauth_clients")
    op.drop_index("ix_oauth_clients_tenant_id", table_name="oauth_clients")
    op.drop_index("ix_oauth_clients_client_id", table_name="oauth_clients")
    op.drop_table("oauth_clients")
