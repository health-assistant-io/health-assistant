"""add ai_models.capabilities (text/vision/audio_input)

Adds a ``capabilities`` JSONB array to ``ai_models`` declaring which
modalities a model supports (its "features"): ``text`` (baseline, every model),
``vision`` (image input — multimodal chat / vision OCR), ``audio_input``
(speech-to-text — the ``transcription`` task). Tasks require specific
capabilities so the task-assignment picker only offers eligible models.

Defaults to ``["text"]`` so every pre-existing model keeps its current meaning
(a plain chat/LLM model). The new ``transcription`` task assignment resolves
to models that advertise ``audio_input`` (e.g. ``whisper-1``).

Revision ID: s1t2m3o4d5e6
Revises: 8ddb7ef7ca4d
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "s1t2m3o4d5e6"
down_revision = "8ddb7ef7ca4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_models",
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"text\"]'"),
        ),
    )
    # GIN index supports capability-containment lookups
    # (``capabilities ? 'vision'`` / ``capabilities @> '["audio_input"]'``).
    op.create_index(
        "ix_ai_models_capabilities",
        "ai_models",
        ["capabilities"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_ai_models_capabilities", table_name="ai_models")
    op.drop_column("ai_models", "capabilities")
