"""Temperature becomes optional (ADR-0038 temperature addendum).

The ORM and API defaults that manufactured ``0.7`` for every model row are
retired (schema + model changed in the same commit); the column itself was
already nullable since the consolidated baseline, so this migration only
carries the data fix: rows still carrying exactly ``0.7`` were manufactured
by the old default, never chosen — they are cleared to NULL. An unset
temperature means "send nothing" and the provider's own default (1.0)
applies — the only value reasoning-family models (o-series, gpt-5+)
accept. A deliberately-set value (anything else) is preserved verbatim.
"""

from alembic import op

revision = "t1e2m3p4r5o6"
down_revision = "d1e2c3i4s5i6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE ai_models SET temperature = NULL WHERE temperature = 0.7")


def downgrade() -> None:
    # The old default cannot be reconstructed — previously-clear rows and
    # genuine 0.7 choices are indistinguishable. Restore nothing.
    pass
