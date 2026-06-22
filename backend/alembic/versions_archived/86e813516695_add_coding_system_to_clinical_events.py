"""add coding system to clinical events

Revision ID: 86e813516695
Revises: 9a31f88a2146
Create Date: 2026-06-12 13:28:32.298769

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '86e813516695'
down_revision: Union[str, Sequence[str], None] = '9a31f88a2146'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add coding_system column
    op.add_column('clinical_events', sa.Column('coding_system', sa.Enum('LOINC', 'SNOMED', 'CUSTOM', name='codingsystem'), nullable=True))
    
    # 2. Migrate data from JSONB code to coding_system and code String
    # Note: We do this before altering the column type of 'code'
    op.execute("""
        UPDATE clinical_events 
        SET coding_system = CASE 
            WHEN code->>'system' ILIKE '%loinc.org%' OR code->>'system' = 'loinc' THEN 'LOINC'::codingsystem
            WHEN code->>'system' ILIKE '%snomed.info%' OR code->>'system' = 'snomed' THEN 'SNOMED'::codingsystem
            ELSE 'CUSTOM'::codingsystem
        END
        WHERE code IS NOT NULL AND code ? 'system'
    """)

    # 3. Alter code column type
    op.execute("ALTER TABLE clinical_events ALTER COLUMN code TYPE VARCHAR(100) USING code->>'code'")


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Convert code back to JSONB
    op.execute("ALTER TABLE clinical_events ALTER COLUMN code TYPE JSONB USING jsonb_build_object('code', code, 'system', coding_system::text)")
    
    # 2. Drop coding_system column
    op.drop_column('clinical_events', 'coding_system')
