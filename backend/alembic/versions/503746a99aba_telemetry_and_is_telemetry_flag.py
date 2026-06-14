"""telemetry_and_is_telemetry_flag

Revision ID: 503746a99aba
Revises: eb84ddfb3c4c
Create Date: 2026-06-14 01:05:59.936888

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '503746a99aba'
down_revision: Union[str, Sequence[str], None] = 'eb84ddfb3c4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add is_telemetry to biomarker_definitions
    op.add_column('biomarker_definitions', sa.Column('is_telemetry', sa.Boolean(), server_default='false', nullable=False))
    
    # Rename wearable_data to telemetry_data
    op.rename_table('wearable_data', 'telemetry_data')
    
    # Rename indexes for the table if they exist
    op.execute('ALTER INDEX IF EXISTS ix_wearable_data_device_id RENAME TO ix_telemetry_data_device_id')
    op.execute('ALTER INDEX IF EXISTS ix_wearable_data_timestamp RENAME TO ix_telemetry_data_timestamp')
    op.execute('ALTER INDEX IF EXISTS ix_wearable_data_tenant_id RENAME TO ix_telemetry_data_tenant_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.rename_table('telemetry_data', 'wearable_data')
    
    op.execute('ALTER INDEX IF EXISTS ix_telemetry_data_device_id RENAME TO ix_wearable_data_device_id')
    op.execute('ALTER INDEX IF EXISTS ix_telemetry_data_timestamp RENAME TO ix_wearable_data_timestamp')
    op.execute('ALTER INDEX IF EXISTS ix_telemetry_data_tenant_id RENAME TO ix_wearable_data_tenant_id')
    
    op.drop_column('biomarker_definitions', 'is_telemetry')
