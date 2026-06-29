"""Switch anatomy_figures from inline SVG to raster images (WebP/PNG)

Revision ID: c1d2e3f4a5b6
Revises: b3d4e5f6a7c8
Create Date: 2026-06-28 23:30:00.000000

Replaces the inline SVG + viewBox columns with an on-disk raster image model.
The four default figures are re-seeded from bundled WebP files by seed_service
on startup (which reads their pixel dimensions). Markers on
``anatomy_structures.display.map.markers`` are normalized 0-1 and carry over
unchanged (they were against the viewBox; now against pixel dimensions — same
relative positions).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b3d4e5f6a7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add new image columns.
    op.add_column('anatomy_figures', sa.Column('image_path', sa.String(500), nullable=True))
    op.add_column('anatomy_figures', sa.Column('width', sa.Integer(), nullable=True))
    op.add_column('anatomy_figures', sa.Column('height', sa.Integer(), nullable=True))
    # Drop the SVG / viewBox columns.
    op.drop_column('anatomy_figures', 'svg_content')
    op.drop_column('anatomy_figures', 'vb_x')
    op.drop_column('anatomy_figures', 'vb_y')
    op.drop_column('anatomy_figures', 'vb_w')
    op.drop_column('anatomy_figures', 'vb_h')
    # Existing rows now have NULL image_path — seed_service will populate the
    # four defaults from bundled WebP files on the next startup.


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('anatomy_figures', sa.Column('vb_h', sa.Float(), nullable=True))
    op.add_column('anatomy_figures', sa.Column('vb_w', sa.Float(), nullable=True))
    op.add_column('anatomy_figures', sa.Column('vb_y', sa.Float(), nullable=True))
    op.add_column('anatomy_figures', sa.Column('vb_x', sa.Float(), nullable=True))
    op.add_column('anatomy_figures', sa.Column('svg_content', sa.Text(), nullable=True))
    op.drop_column('anatomy_figures', 'height')
    op.drop_column('anatomy_figures', 'width')
    op.drop_column('anatomy_figures', 'image_path')
