"""BYOK setup: preset_key stamp + §15 capability vocabulary (audio_input → stt)

Family plan 17 Phase 3 (ai-features §15, frozen 2026-09-19):

1. ``ai_providers.preset_key`` — nullable stamp recording the preset a
   provider row was created/re-setup from (NULL = manual row, adoptable).
2. ``ai_models.capabilities`` vocabulary rename: the legacy health value
   ``audio_input`` (verified transcription-only usage — no chat-with-audio
   consumer) becomes the §15 family value ``stt``. Pure value-level rewrite
   across all rows (the vocabulary is global; scope semantics of provider /
   assignment rows are untouched).

Template: study 0064_byok_setup_stt_tts (semantic reference — this repo
chains its own hash-style revision ids).
"""

import json
from collections.abc import Mapping, Sequence

import sqlalchemy as sa

from alembic import op

revision = "b1y2o3k4s5e6"
down_revision = "r1e2a3l4t5i6"
branch_labels = None
depends_on = None

_CAPS_FORWARD: Mapping[str, Sequence[str]] = {
    "audio_input": ("stt",),
}
_CAPS_LEGACY: Mapping[str, Sequence[str]] = {
    "stt": ("audio_input",),
}


def _load_caps(raw: object) -> list[str] | None:
    if isinstance(raw, list):
        return [cap for cap in raw if isinstance(cap, str)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        if isinstance(parsed, list):
            return [cap for cap in parsed if isinstance(cap, str)]
    return None


def _rewrite_caps(mapping: Mapping[str, Sequence[str]]) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, capabilities FROM ai_models")).fetchall()
    for row_id, raw_caps in rows:
        caps = _load_caps(raw_caps)
        if caps is None:
            continue
        updated: list[str] = []
        for cap in caps:
            updated.extend(mapping.get(cap, (cap,)))
        deduped: list[str] = []
        for cap in updated:
            if cap not in deduped:
                deduped.append(cap)
        if deduped != caps:
            bind.execute(
                sa.text(
                    "UPDATE ai_models SET capabilities = CAST(:caps AS jsonb) "
                    "WHERE id = :id"
                ),
                {"caps": json.dumps(deduped), "id": row_id},
            )


def upgrade() -> None:
    op.add_column(
        "ai_providers",
        sa.Column("preset_key", sa.String(length=40), nullable=True),
    )
    _rewrite_caps(_CAPS_FORWARD)


def downgrade() -> None:
    _rewrite_caps(_CAPS_LEGACY)
    op.drop_column("ai_providers", "preset_key")
