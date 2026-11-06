"""The TaskDef registry completeness wall + generation parity (plan 4 W1).

This is the test that would have caught ``define_anatomy_graph``: a task that
existed in the enum but had no label, no settings row, no capability default to
fall back to, and no generated frontend entry — i.e. it silently resolved through
``default`` while being invisible and unassignable.

It pins:
* every registry task has a UI label key that resolves in EVERY shipped locale;
* every task's capability is in the §15 vocabulary and has exactly one declared
  capability-default task (so resolution always terminates at a provider or an
  honest "unavailable");
* the generated projections (``task_types.py``, ``frontend/src/generated/
  taskRegistry.ts``) carry every task — and, when node is available, are
  byte-identical to what the generator emits (drift gate, non-vacuous);
* the migration's frozen task-def rows equal the registry;
* the seeded ``ai_task_defs`` table equals the registry (rule 7b: seed ≡
  registry);
* non-LLM engine tasks live in their own taxonomy, never in the LLM registry.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.ai.providers.task_registry import (
    DECISION_TASK_IDS,
    TASK_DEFS,
    TASK_IDS,
    TASKS,
    TaskPhiClass,
    TaskTrigger,
    capability_default_task,
)
from app.core.database import AsyncSessionLocal
from app.models.ai_task_model import AITaskDef
from app.models.enums import AICallRoute, AIModelCapability
from app.services.engine.engine_task_registry import ENGINE_TASKS

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "frontend" / "src" / "locales"
GENERATED_TS = ROOT / "frontend" / "src" / "generated" / "taskRegistry.ts"
GENERATED_PY = Path(__file__).resolve().parents[1] / "app" / "ai" / "providers" / "task_types.py"
MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0003_ai_task_registry.py"
)

#: The one capability default per capability (§15 defaults: text/vision/stt/
#: embeddings/decision). Any other shape means a task's fallback chain is
#: ambiguous.
EXPECTED_CAPABILITY_DEFAULTS = {
    "text": "default",
    "vision": "ocr",
    "stt": "transcription",
    "embeddings": "embed",
    "decision": "decision",
}


def _locale_keys(locale: str) -> set[str]:
    data = json.loads((LOCALES / locale / "common.json").read_text(encoding="utf-8"))
    keys: set[str] = set()

    def walk(prefix: str, node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(f"{prefix}.{key}" if prefix else key, value)
        else:
            keys.add(prefix)

    walk("", data)
    return keys


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0003", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# registry shape
# ---------------------------------------------------------------------------


def test_registry_ids_are_unique_and_ordered():
    assert TASK_IDS[0] == "default", "the text capability default must lead the registry"
    assert len(set(TASK_IDS)) == len(TASK_IDS)
    assert list(TASK_DEFS) == list(TASK_IDS)


def test_every_task_has_a_capability_in_the_family_vocabulary():
    vocabulary = set(AIModelCapability.all_values())
    for task in TASKS:
        assert task.capability in vocabulary, f"{task.id}: unknown capability {task.capability}"


def test_every_task_has_exactly_one_capability_default():
    defaults = {task.capability: task.id for task in TASKS if task.capability_default}
    assert defaults == EXPECTED_CAPABILITY_DEFAULTS
    for task in TASKS:
        resolved = capability_default_task(task.capability)
        assert resolved is not None, (
            f"{task.id}: capability '{task.capability}' has no declared default — "
            "resolution would dead-end instead of reporting a provider path"
        )


def test_every_specialist_task_resolves_through_a_declared_default():
    """The completeness wall: no task may only resolve via an implicit hop."""
    for task in TASKS:
        default = capability_default_task(task.capability)
        assert default is not None
        if not task.capability_default:
            assert default.id != task.id


def test_every_task_label_key_resolves_in_every_locale():
    shipped = sorted(p.name for p in LOCALES.iterdir() if (p / "common.json").exists())
    assert set(shipped) >= {"en", "el"}, "both shipped locales must exist"
    for locale in shipped:
        keys = _locale_keys(locale)
        for task in TASKS:
            assert task.label_key in keys, f"{task.id}: missing {task.label_key} in {locale}"
            assert task.label_key == f"settings.ai.task_{task.id}", (
                f"{task.id}: label keys follow one convention"
            )


def test_registry_fields_use_closed_vocabularies():
    routes = set(AICallRoute)
    for task in TASKS:
        assert task.trigger in set(TaskTrigger)
        assert task.phi_class in set(TaskPhiClass)
        assert AICallRoute(task.route.value) in routes
        assert task.accepts_priority is False, (
            "the single-slot law deleted priority (ADR-0037) — no task may re-introduce it"
        )


def test_decision_tasks_cover_the_decision_capabilities():
    from app.ai.providers.enums import DecisionCapability

    kinds = {
        kind
        for task in TASKS
        if task.capability == "decision" and task.decision_kinds
        for kind in task.decision_kinds
    }
    expected = {cap.value for cap in DecisionCapability if cap.value != "open-args"}
    assert kinds == expected

    assert set(DECISION_TASK_IDS) == {f"decision_{kind.replace('-', '_')}" for kind in expected}
    decision_default = TASK_DEFS["decision"]
    assert decision_default.capability_default
    assert set(decision_default.decision_kinds) == expected


def test_engine_tasks_are_not_llm_tasks_and_their_entrypoints_exist():
    from app.ai.providers.enums import TaskType

    for engine_task in ENGINE_TASKS:
        assert engine_task.id not in TaskType.all_values()
        module_name, _, attr = engine_task.entrypoint.partition(":")
        module = importlib.import_module(module_name)
        target: object = module
        for part in attr.split("."):
            target = getattr(target, part)
        assert target is not None, f"{engine_task.id}: {engine_task.entrypoint} does not resolve"


# ---------------------------------------------------------------------------
# generated projections (drift gates)
# ---------------------------------------------------------------------------


def test_generated_python_enum_matches_the_registry():
    from app.ai.providers.enums import TaskType

    assert TaskType.all_values() == list(TASK_IDS)


def test_generated_typescript_carries_every_task():
    source = GENERATED_TS.read_text(encoding="utf-8")
    assert "GENERATED by sync-task-registry.mjs" in source.splitlines()[0]
    for task in TASKS:
        assert f'id: "{task.id}"' in source, f"{task.id} missing from the generated TS registry"
        assert f'labelKey: "{task.label_key}"' in source
    assert "AI_TASKS" in source and "AI_TASK_DEFS" in source


def test_task_def_ids_are_pinned_by_the_namespace_contract():
    """The uuid5 namespace is a frozen contract (FK values are already stored).

    Deriving it from ``NAMESPACE_URL`` + the registry URL makes it
    reproducible; this pins two representative ids so changing the name string,
    the namespace or the ``ai_task_defs/<code>`` name format is a deliberate,
    reviewed edit (and a red test) rather than a silent re-keying of every
    assignment and audit row.
    """
    from app.ai.providers.task_defs import TASK_DEF_NAMESPACE, task_def_id

    assert str(TASK_DEF_NAMESPACE) == "0cda4228-d4c7-5a94-9486-c6e9c13cb094"
    assert str(task_def_id("chat")) == "5acf26db-579a-5d40-926d-6c36c9210be3"
    assert str(task_def_id("define_anatomy_graph")) == "ce8f88c9-2172-5348-ba4b-6a06f0e75d4a"


def test_migration_task_defs_match_the_registry():
    migration = _load_migration()
    frozen = {code: (task_id, label_key) for task_id, code, label_key in migration._TASK_DEFS}
    from app.ai.providers.task_defs import task_def_rows

    live = {row["code"]: (str(row["id"]), row["label_key"]) for row in task_def_rows()}
    assert frozen == live, (
        "0003_ai_task_registry's frozen task-def rows drifted from the registry — "
        "a new task belongs in the seed + a later data migration, never a silent edit"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_generation_gate_detects_no_drift():
    """Non-vacuous: the gate compares the committed files to a fresh emission."""
    for mode, target in (("--check", GENERATED_TS), ("--check-py", GENERATED_PY)):
        result = subprocess.run(
            ["node", str(ROOT / "scripts" / "sync-task-registry.mjs"), mode, str(target)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"{mode} failed — generated file is stale or hand-edited:\n"
            f"{result.stdout}\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# the seeded lookup (constitution rule 7b: seed ≡ registry)
# ---------------------------------------------------------------------------


async def test_seeded_task_defs_match_the_registry():
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(AITaskDef))).scalars().all()
    seeded = {row.code: row for row in rows}
    assert set(seeded) == set(TASK_IDS), "ai_task_defs must carry exactly the registry"
    for task in TASKS:
        row = seeded[task.id]
        assert row.label_key == task.label_key
        assert row.enabled == task.enabled
    # `default` is the text capability default every text task falls back to.
    assert seeded["default"].code == "default"


async def test_boot_seeder_is_idempotent_and_reconciles_labels():
    """The boot stage upserts: a second run never duplicates and refreshes
    label keys (renaming a label is a registry edit, not a migration)."""
    from sqlalchemy import update

    from app.ai.providers.task_defs import seed_task_defs

    async with AsyncSessionLocal() as session:
        await seed_task_defs(session, commit=True)
        await seed_task_defs(session, commit=True)
        total = (await session.execute(select(AITaskDef))).scalars().all()

        await session.execute(
            update(AITaskDef).where(AITaskDef.code == "chat").values(label_key="drifted.label")
        )
        await session.commit()

        await seed_task_defs(session, commit=True)

    async with AsyncSessionLocal() as session:
        chat = (
            (await session.execute(select(AITaskDef).where(AITaskDef.code == "chat")))
            .scalars()
            .one()
        )

    assert len(total) == len(TASK_IDS)
    assert chat.label_key == TASK_DEFS["chat"].label_key
