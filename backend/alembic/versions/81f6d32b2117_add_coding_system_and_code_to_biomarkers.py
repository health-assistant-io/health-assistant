"""add_coding_system_and_code_to_biomarkers

Revision ID: 81f6d32b2117
Revises: 9574b2b207f7
Create Date: 2026-06-11 17:17:23.470446

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '81f6d32b2117'
down_revision: Union[str, Sequence[str], None] = '9574b2b207f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    coding_system_enum = sa.Enum('LOINC', 'SNOMED', 'CUSTOM', name='codingsystem')
    coding_system_enum.create(op.get_bind(), checkfirst=True)

    op.add_column('biomarker_definitions', sa.Column('coding_system', coding_system_enum, server_default='LOINC', nullable=False))
    op.add_column('biomarker_definitions', sa.Column('code', sa.String(length=100), nullable=True))

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('biomarker_definitions', 'code')
    op.drop_column('biomarker_definitions', 'coding_system')
    
    coding_system_enum = sa.Enum('LOINC', 'SNOMED', 'CUSTOM', name='codingsystem')
    coding_system_enum.drop(op.get_bind(), checkfirst=True)
