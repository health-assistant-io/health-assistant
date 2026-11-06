"""Seeding and id mapping for the ``ai_task_defs`` lookup (plan 4 W2).

The task vocabulary is a *seeded lookup* (constitution rule 7b): rows come from
the one TaskDef registry, so adding a task is a row + code, never a migration.
Ids are deterministic (uuid5 over the task code) so:

* seeding is idempotent (``ON CONFLICT (code) DO UPDATE``),
* an audit row's FK target is derivable without a lookup, and
* the registry-parity gate can compare ids, codes, label keys and flags
  exactly (``tests/test_ai_task_registry.py``).
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.task_registry import TASKS, get_task_def
from app.models.ai_task_model import AITaskDef

#: Namespace for task-definition ids: uuid5(NAMESPACE_URL, <this project's
#: registry URL>), so the namespace is reproducible from a readable name instead
#: of an opaque literal. It is a **frozen schema contract** (every
#: ``ai_task_assignments.task_type_id`` / ``ai_call_logs.task_type_id`` stores
#: these ids, and ``0003_ai_task_registry`` embeds them) — never change it.
#:
#: Two consequences of deterministic ids worth knowing: renaming a task ``code``
#: mints a NEW id (a new vocabulary row; old assignments keep pointing at the
#: old definition), and uuid5 ids are a hash of a public name, so they are
#: predictable — do not reuse this pattern for security-sensitive ids (tokens,
#: capability grants).
TASK_DEF_NAMESPACE = uuid5(NAMESPACE_URL, "https://neuronection.com/ai/task-defs")


def task_def_id(code: str) -> UUID:
    """The deterministic id for a registry task code."""
    return uuid5(TASK_DEF_NAMESPACE, f"ai_task_defs/{code}")


def task_def_rows() -> list[dict]:
    """The registry as ``ai_task_defs`` rows (id, code, label_key, enabled)."""
    return [
        {
            "id": task_def_id(task.id),
            "code": task.id,
            "label_key": task.label_key,
            "enabled": task.enabled,
        }
        for task in TASKS
    ]


async def seed_task_defs(db: AsyncSession, *, commit: bool = False) -> dict[str, int]:
    """Upsert every registry task into ``ai_task_defs`` (idempotent).

    Boot seeding is data, not DDL: a new task appears here on the next start
    without a migration. Updates ``label_key``/``enabled`` so a renamed label or
    a disabled task reaches existing deployments.

    Returns the seed-stage stats contract (``added``/``updated``/``skipped``/
    ``errors``) like every other seeding stage.
    """
    rows = task_def_rows()
    existing_codes = set((await db.execute(select(AITaskDef.code))).scalars().all())
    added = sum(1 for row in rows if row["code"] not in existing_codes)
    if rows:
        statement = pg_insert(AITaskDef).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[AITaskDef.code],
            set_={
                "label_key": statement.excluded.label_key,
                "enabled": statement.excluded.enabled,
            },
        )
        await db.execute(statement)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return {
        "added": added,
        "updated": len(rows) - added,
        "skipped": 0,
        "errors": 0,
    }


async def ensure_task_def(db: AsyncSession, code: str) -> UUID | None:
    """Guarantee the definition row for ``code`` exists; return its id.

    Cheap by design: the id is derived, so the only work is one idempotent
    ``INSERT ... ON CONFLICT DO NOTHING`` per process for rows a boot seeder has
    not created (a worker that started before the web app). Returns ``None`` for
    a code the registry does not declare — callers then skip the write rather
    than invent a vocabulary value.
    """
    task = get_task_def(code)
    if task is None:
        return None
    statement = (
        pg_insert(AITaskDef)
        .values(
            id=task_def_id(task.id),
            code=task.id,
            label_key=task.label_key,
            enabled=task.enabled,
        )
        .on_conflict_do_nothing(index_elements=[AITaskDef.code])
    )
    await db.execute(statement)
    return task_def_id(task.id)


async def task_defs_by_code(db: AsyncSession) -> dict[str, AITaskDef]:
    """Every seeded definition keyed by code (diagnostics/parity tests)."""
    result = await db.execute(select(AITaskDef))
    return {row.code: row for row in result.scalars().all()}
