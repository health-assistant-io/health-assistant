"""Add source_image_path to anatomy_figures (keep original uncropped upload)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-29 00:45:00.000000

Lets the Atlas Manager keep the original uploaded image alongside the cropped
view, so admins can re-crop from the uncropped source when editing a figure.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('anatomy_figures', sa.Column('source_image_path', sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column('anatomy_figures', 'source_image_path')
