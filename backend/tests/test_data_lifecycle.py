"""Lifecycle-registry gates (plan 6 W1/W4).

Pins the constitution's rule 10 decisions:

* every mapped table has exactly one lifecycle class, in code **and** in
  ``contracts/data-model.lifecycle.txt`` (gate D6);
* the tombstone set is exactly the declared one and nothing else mixes in
  ``SoftDeleteMixin``;
* the erasure walker's patient order is children-before-parents;
* every operational/transient table resolves to a retention policy whose
  timestamp column exists (or a deliberately disabled row).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.data_lifecycle import (
    LIFECYCLE_CLASSES,
    LifecycleClass,
    UnclassifiedTable,
    class_for,
    operational_tables,
    transient_tables,
    validate_lifecycle,
)
from app.models.base import Base, SoftDeleteMixin
from tests.test_schema_parameters import TOMBSTONE_TABLES

INVENTORY = Path(__file__).resolve().parents[2] / "contracts" / "data-model.lifecycle.txt"


def _inventory_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for raw in INVENTORY.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        table, _, rest = line.partition("::")
        rows[table.strip()] = rest.strip().split()[0] if rest.strip() else ""
    return rows


def _mapped_tables() -> set[str]:
    return {mapper.class_.__tablename__ for mapper in Base.registry.mappers}


def test_every_mapped_table_has_a_lifecycle_class():
    mapped = _mapped_tables()
    assert set(LIFECYCLE_CLASSES) == mapped, (
        "lifecycle registry drifted from the mapped metadata — "
        f"missing {sorted(mapped - set(LIFECYCLE_CLASSES))}, "
        f"stale {sorted(set(LIFECYCLE_CLASSES) - mapped)}"
    )


def test_registry_matches_the_lifecycle_inventory_file():
    rows = _inventory_rows()
    assert set(rows) == set(LIFECYCLE_CLASSES), (
        "contracts/data-model.lifecycle.txt and app/core/data_lifecycle.py disagree — "
        f"only in file {sorted(set(rows) - set(LIFECYCLE_CLASSES))}, "
        f"only in code {sorted(set(LIFECYCLE_CLASSES) - set(rows))}"
    )
    for table, declared in LIFECYCLE_CLASSES.items():
        assert rows[table] == declared.value, (
            f"{table}: file says {rows[table]!r}, code says {declared.value!r}"
        )


def test_lifecycle_classes_are_the_closed_three_value_vocabulary():
    assert {member.value for member in LifecycleClass} == {
        "record",
        "operational",
        "transient",
    }
    assert operational_tables().isdisjoint(transient_tables())
    assert operational_tables().isdisjoint(
        {table for table, cls in LIFECYCLE_CLASSES.items() if cls is LifecycleClass.RECORD}
    )


def test_unknown_table_raises_the_typed_lifecycle_error():
    with pytest.raises(UnclassifiedTable):
        class_for("not_a_mapped_table")


def test_validate_lifecycle_turns_a_new_model_red():
    """The gate-shaped check is non-vacuous: an unclassified table is a finding."""
    findings = validate_lifecycle(["users", "erasure_requests", "brand_new_model"])
    assert len(findings) == 1
    assert "brand_new_model" in findings[0] and "FAIL D6" in findings[0]


def test_tombstone_doctrine_only_declares_the_fhir_set():
    """``SoftDeleteMixin`` is exactly the declared tombstone set (plan 6 W4)."""
    with_mixin: set[str] = set()
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, SoftDeleteMixin):
            with_mixin.add(cls.__tablename__)
    assert with_mixin == TOMBSTONE_TABLES, (
        "SoftDeleteMixin usage drifted from the declared tombstone set — "
        f"extra {sorted(with_mixin - TOMBSTONE_TABLES)}, "
        f"missing {sorted(TOMBSTONE_TABLES - with_mixin)}"
    )
    with_deleted_at = {
        table.name for table in Base.metadata.tables.values() if "deleted_at" in table.columns
    }
    assert with_deleted_at == TOMBSTONE_TABLES


def test_erasure_patient_walk_is_children_before_parents():
    from app.services.data_lifecycle.erasure import validate_patient_order

    assert validate_patient_order() == []


def test_tenant_walk_is_children_before_parents():
    from app.services.data_lifecycle.erasure import _tenant_order

    order = _tenant_order()
    position = {name: index for index, name in enumerate(order)}
    violations: list[str] = []
    for name, index in position.items():
        table = Base.metadata.tables[name]
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if parent != name and parent in position and position[parent] < index:
                violations.append(f"{name} before parent {parent}")
    assert violations == []


@pytest.mark.asyncio
async def test_every_operational_and_transient_table_has_a_usable_policy():
    """No unbounded table escapes: each class member resolves to a rule.

    An enabled rule must point at a real timestamp column on the model; a
    disabled table-level row is a deliberate "bounded by registry" decision.
    """
    from app.core.database import AsyncSessionLocal
    from app.services.data_lifecycle.retention_service import (
        CHECKPOINT_TABLE,
        effective_policy,
        table_has_timestamp,
    )

    async with AsyncSessionLocal() as db:
        for table in sorted(operational_tables() | transient_tables()):
            lifecycle_class = (
                LifecycleClass.OPERATIONAL
                if table in operational_tables()
                else LifecycleClass.TRANSIENT
            )
            policy = await effective_policy(db, table, lifecycle_class)
            if policy.enabled:
                assert table_has_timestamp(table, policy.timestamp_column), (
                    f"{table} policy compares {policy.timestamp_column!r}, "
                    "which is not a model column"
                )
        checkpoint = await effective_policy(db, CHECKPOINT_TABLE, LifecycleClass.OPERATIONAL)
        assert checkpoint.enabled and checkpoint.days == 30
