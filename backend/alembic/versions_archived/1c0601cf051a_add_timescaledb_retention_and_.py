"""add timescaledb retention and compression policies

Revision ID: 1c0601cf051a
Revises: e3d6ed664956
Create Date: 2026-06-15 16:48:10.209848

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c0601cf051a'
down_revision: Union[str, Sequence[str], None] = 'e3d6ed664956'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add TimescaleDB compression to the telemetry_data hypertable
    # Partitioning by device_id and timestamp
    op.execute("""
        ALTER TABLE telemetry_data SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'device_id',
            timescaledb.compress_orderby = 'timestamp DESC'
        );
    """)

    # Add a compression policy to automatically compress chunks older than 7 days
    op.execute("""
        SELECT add_compression_policy('telemetry_data', INTERVAL '7 days', if_not_exists => true);
    """)

    # Add a retention policy to automatically drop data older than 2 years (optional but highly recommended for high-frequency IoT data)
    op.execute("""
        SELECT add_retention_policy('telemetry_data', INTERVAL '2 years', if_not_exists => true);
    """)

def downgrade() -> None:
    """Downgrade schema."""
    # Remove retention policy
    op.execute("SELECT remove_retention_policy('telemetry_data', if_exists => true);")
    
    # Remove compression policy
    op.execute("SELECT remove_compression_policy('telemetry_data', if_exists => true);")
    
    # Disable compression on the table
    op.execute("ALTER TABLE telemetry_data SET (timescaledb.compress = false);")
