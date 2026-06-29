"""Add anatomy_figures table (DB-driven body atlas) and migrate markers

Revision ID: b3d4e5f6a7c8
Revises: 39fffbc136ce
Create Date: 2026-06-28 19:00:00.000000

Replaces the hardcoded frontend atlas (atlas.ts) with a DB-driven one. Each
row in ``anatomy_figures`` is one view of one figure (e.g. ``man-front``) with
its own coordinate space (viewBox) and inline SVG markup.

Existing per-gender markers on ``anatomy_structures.display.map.markers`` were
normalized 0-1 against the COMBINED front+back canvas. They are rewritten to be
keyed by figure slug (``man-front`` / ``man-back`` / ``woman-front`` /
``woman-back``) and normalized 0-1 against that single figure's viewBox, using
the crop regions the old atlas.ts used to isolate each view.
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3d4e5f6a7c8'
down_revision: Union[str, Sequence[str], None] = '39fffbc136ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Coordinate spaces of the original combined-canvas surface diagrams, and the
# crop regions that isolated each view. Used to remap legacy per-gender markers
# (combined-canvas normalized) to per-figure normalized coordinates.
_LEGACY_CANVAS = {
    "man": {
        "w": 463.47098, "h": 703.55267,
        "views": {
            "front": {"x": 108, "y": 108, "w": 195, "h": 272},
            "back": {"x": 28, "y": 108, "w": 96, "h": 272},
        },
    },
    "woman": {
        "w": 442.32355, "h": 665.33276,
        "views": {
            "front": {"x": 108, "y": 118, "w": 90, "h": 264},
            "back": {"x": 188, "y": 118, "w": 112, "h": 264},
        },
    },
}


def _remap_markers(markers: dict) -> dict:
    """Convert legacy ``{gender: {view, nx, ny, nr}}`` to per-figure slugs."""
    out: dict = {}
    for gender, m in (markers or {}).items():
        if not isinstance(m, dict):
            continue
        canvas = _LEGACY_CANVAS.get(gender)
        if not canvas:
            continue
        view = m.get("view", "front")
        crop = canvas["views"].get(view) or canvas["views"].get("front")
        if not crop:
            continue
        nx = float(m.get("nx", 0.5))
        ny = float(m.get("ny", 0.3))
        nr = float(m.get("nr", 0.02))
        # combined-canvas normalized -> absolute canvas px -> figure-normalized
        new_nx = (nx * canvas["w"] - crop["x"]) / crop["w"]
        new_ny = (ny * canvas["h"] - crop["y"]) / crop["h"]
        # keep the same visual radius: old px radius was nr * canvas_h
        new_nr = nr * canvas["h"] / crop["h"]
        out[f"{gender}-{view}"] = {
            "nx": round(max(0.0, min(1.0, new_nx)), 5),
            "ny": round(max(0.0, min(1.0, new_ny)), 5),
            "nr": round(new_nr, 5),
        }
    return out


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'anatomy_figures',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('figure_key', sa.String(50), nullable=False),
        sa.Column('view_key', sa.String(50), nullable=False),
        sa.Column('svg_content', sa.Text(), nullable=False),
        sa.Column('vb_x', sa.Float(), nullable=False, server_default='0'),
        sa.Column('vb_y', sa.Float(), nullable=False, server_default='0'),
        sa.Column('vb_w', sa.Float(), nullable=False),
        sa.Column('vb_h', sa.Float(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_anatomy_figures_slug'),
    )
    op.create_index('ix_anatomy_figures_slug', 'anatomy_figures', ['slug'])
    op.create_index('ix_anatomy_figures_figure_key', 'anatomy_figures', ['figure_key'])
    op.create_index('idx_anatomy_figure_group', 'anatomy_figures', ['figure_key', 'view_key'])

    # Rewrite existing per-gender markers to per-figure-slug markers.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, display FROM anatomy_structures WHERE display IS NOT NULL")
    ).fetchall()
    for row in rows:
        display = row[1]
        if not isinstance(display, dict):
            continue
        mp = display.get("map")
        if not isinstance(mp, dict):
            continue
        markers = mp.get("markers")
        if not isinstance(markers, dict) or not markers:
            continue
        # Skip rows already migrated (new-scheme keys contain a hyphen).
        if any("-" in k for k in markers.keys()):
            continue
        new_markers = _remap_markers(markers)
        if not new_markers:
            continue
        mp["markers"] = new_markers
        bind.execute(
            sa.text("UPDATE anatomy_structures SET display = CAST(:d AS JSONB) WHERE id = :id"),
            {"d": json.dumps(display), "id": str(row[0])},
        )


def downgrade() -> None:
    """Downgrade schema. Marker data transform is not reversed (lossy)."""
    op.drop_index('idx_anatomy_figure_group', table_name='anatomy_figures')
    op.drop_index('ix_anatomy_figures_figure_key', table_name='anatomy_figures')
    op.drop_index('ix_anatomy_figures_slug', table_name='anatomy_figures')
    op.drop_table('anatomy_figures')
