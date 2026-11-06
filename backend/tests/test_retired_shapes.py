"""Retired shapes stay retired — the plans 3-5 deletion pins (plan 9 §3.5).

Cheap floor under the migration chain: every shape the schema program deleted is
asserted absent from **both** ``Base.metadata`` and the migrated head schema, so
a later model edit or a hand-written ``op.add_column`` cannot quietly revive it.

Pinned here:

* ``chat_messages.tasks`` — chat HITL cards are ``proposals`` rows
  (``source='chat'``); the ADR-0015 wire ``tasks`` array is an in-memory
  projection attached by ``ChatSessionService.attach_cards()`` (plan 5).
* ``ai_task_assignments.priority`` and a stored ``is_active`` column — the
  single-slot law (ADR-0037) makes precedence fixed and derives "active" from
  ``revoked_at IS NULL`` (plan 4).
* the hand-maintained task maps (``_SLOT_TASK_TYPES``, the frontend
  ``OTHER_TASKS`` list, any literal ``TASK_REQUIRED_CAPABILITY`` assignment) —
  the one ``TaskDef`` registry generates them (plan 4 W1).
* the JSONB cursor helpers ``get_sync_cursor``/``set_sync_cursor`` anywhere in
  the server (plan 8 W1; ``test_integration_sync_state.py`` pins the row-backed
  replacement and the SDK's class surface).

Line-level naming hygiene (comments, identifiers) is gate D7's job
(``scripts/check-data-model.sh``), not this file's.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.models.ai_provider_model import AITaskAssignment
from app.models.chat_model import ChatMessage

BACKEND = Path(__file__).resolve().parents[1]
CORE = BACKEND.parent
RETIRED_TASK_MAPS = ("_SLOT_TASK_TYPES", "OTHER_TASKS")


def _columns(table: str) -> set[str]:
    """Column names of ``table`` in the migrated test DB (migrated by conftest)."""
    engine = create_engine(settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2"))
    try:
        return {column["name"] for column in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _python_sources(*roots: Path):
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path, path.read_text(encoding="utf-8")


def test_chat_messages_stores_no_tasks_column():
    """Plan 5: cards are ``proposals`` rows; ``tasks`` is a wire projection only."""
    assert "tasks" not in ChatMessage.__table__.columns, (
        "chat_messages.tasks is retired (plan 5) — cards live in proposals"
    )
    assert not hasattr(ChatMessage, "tasks"), (
        "the wire `tasks` array is attached by ChatSessionService.attach_cards(), "
        "never declared on the model"
    )
    assert "tasks" not in _columns("chat_messages"), (
        "the migrated schema still carries chat_messages.tasks — a migration "
        "re-added a shape plan 5 deleted"
    )


def test_assignments_store_no_priority_or_active_flag():
    """Plan 4 / ADR-0037: precedence is fixed; "active" is derived, never stored."""
    columns = set(AITaskAssignment.__table__.columns.keys())
    assert "priority" not in columns, (
        "ai_task_assignments.priority is retired (ADR-0037 single-slot law) — "
        "precedence lives once, in ai/providers/resolution.py"
    )
    assert "is_active" not in columns, (
        "`is_active` is a derived property (revoked_at IS NULL), never a column"
    )
    assert "revoked_at" in columns, "revoking — not deleting — is the assignment law"

    live = _columns("ai_task_assignments")
    assert "priority" not in live, "the migrated schema still carries priority"
    assert "is_active" not in live, "the migrated schema still stores is_active"


def test_task_vocabulary_has_one_registry():
    """Plan 4 W1: the hand-maintained task maps stay generated, not re-authored."""
    offenders: list[str] = []
    for path, text in _python_sources(BACKEND / "app"):
        if "TASK_REQUIRED_CAPABILITY =" in text:
            offenders.append(f"{path.relative_to(CORE)}: TASK_REQUIRED_CAPABILITY =")
    for path, text in _python_sources(CORE / "frontend" / "src"):
        for marker in RETIRED_TASK_MAPS:
            if marker in text:
                offenders.append(f"{path.relative_to(CORE)}: {marker}")
    for path, text in _python_sources(BACKEND / "app"):
        for marker in RETIRED_TASK_MAPS:
            if marker in text:
                offenders.append(f"{path.relative_to(CORE)}: {marker}")

    assert offenders == [], (
        "hand-maintained task maps are retired (plan 4 W1 — the TaskDef registry "
        "generates them):\n" + "\n".join(offenders)
    )

    from app.ai.providers.task_registry import TASK_IDS

    assert TASK_IDS, "the TaskDef registry is the one source of task ids"
    assert len(set(TASK_IDS)) == len(TASK_IDS), "registry task ids must be unique"


def test_jsonb_cursor_helpers_are_gone():
    """Plan 8 W1: cursors are ``integration_sync_state`` rows, not JSONB methods."""
    offenders = [
        str(path.relative_to(CORE))
        for path, text in _python_sources(BACKEND / "app")
        if "get_sync_cursor" in text or "set_sync_cursor" in text
    ]
    assert offenders == [], (
        "the JSONB cursor helpers are deleted, not deprecated (plan 8 W1):\n" + "\n".join(offenders)
    )
