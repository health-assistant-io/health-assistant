"""Enum ⇄ database vocabulary parity gate (guidelines/data-model.md §6).

Every closed behavioral vocabulary is a Python ``StrEnum`` mirrored at the DB
layer. The mirror has two shapes:

* a **Postgres native enum type** (``sa.Enum(...)`` — the shipped shape for
  every enum column today), whose labels are the DB-side membership rule, or
* a **CHECK constraint** listing the allowed values (used where a column is a
  plain varchar / when a vocabulary needs CHECK semantics instead of a type).

This test asserts, for every enum-typed model column:

1. the PG type's labels equal the labels the model declares (no migration
   drifted from the model, no model drifted from the migration);
2. the labels still match the Python enum members (member names by default,
   ``values_callable`` values where the column stores ``.value``);
3. any CHECK constraint that spells out a vocabulary (``col IN (...)`` /
   ``col = ANY (ARRAY[...])``) agrees with the enum members for that column —
   the constitution's "CHECK ≡ enum" rule, and the guard against a CHECK that
   outlives or runs ahead of its enum.

Adding a member to an enum without widening its mirror fails here. The gate is
non-vacuous: it asserts how many vocabularies it actually examined.
"""

import re

import sqlalchemy as sa
from sqlalchemy import create_engine, text

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.models.base import Base

#: Minimum number of mirrored vocabularies the scan must find. Guards against
#: the gate going vacuous if the introspection query or the model import
#: silently breaks (the suite has ~60 enum columns).
MIN_MIRRORED_ENUM_COLUMNS = 40


def _sync_engine():
    return create_engine(settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2"))


def _pg_enum_labels(conn) -> dict[str, set[str]]:
    rows = conn.execute(
        text(
            "SELECT t.typname, e.enumlabel "
            "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
            "WHERE t.typnamespace = 'public'::regnamespace"
        )
    ).all()
    labels: dict[str, set[str]] = {}
    for typname, label in rows:
        labels.setdefault(typname, set()).add(label)
    return labels


def _vocabulary_checks(conn) -> dict[str, tuple[str, set[str]]]:
    """Map ``table.column`` → (check name, literal values).

    Only CHECKs whose body enumerates a vocabulary are considered; range and
    ordering CHECKs (``progress BETWEEN 0 AND 100``) are not vocabularies.
    Postgres renders an ``IN`` list as ``col = ANY (ARRAY[...])``.
    """
    rows = conn.execute(
        text(
            "SELECT c.conrelid::regclass::text AS table_name, c.conname, "
            "       pg_get_constraintdef(c.oid) AS definition "
            "FROM pg_constraint c "
            "WHERE c.contype = 'c' AND c.connamespace = 'public'::regnamespace"
        )
    ).all()

    found: dict[str, tuple[str, set[str]]] = {}
    for table_name, conname, definition in rows:
        if "ARRAY[" not in definition:
            continue
        table = table_name.replace("public.", "").strip('"')
        match = re.search(r'\(?"?(\w+)"?\)?(?:::[\w\s\[\]]+)? = ANY', definition)
        if match is None:
            continue
        column = match.group(1)
        values = set(re.findall(r"'([^']*)'", definition[match.end() :]))
        if values:
            found[f"{table}.{column}"] = (conname, values)
    return found


def _expected_from_python(column_type: sa.Enum) -> set[str]:
    enum_cls = column_type.enum_class
    if enum_cls is None:
        return set(column_type.enums)
    if column_type.values_callable is not None:
        return {str(member.value) for member in enum_cls}
    return {member.name for member in enum_cls}


def test_every_native_enum_column_matches_the_database():
    """PG enum labels ≡ model labels ≡ Python enum members, per column."""
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            labels_by_type = _pg_enum_labels(conn)
    finally:
        engine.dispose()

    checked = 0
    failures: list[str] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            column_type = column.type
            if not isinstance(column_type, sa.Enum) or not column_type.native_enum:
                continue
            checked += 1
            type_name = column_type.name
            db_labels = labels_by_type.get(type_name)
            if db_labels is None:
                failures.append(f"{table.name}.{column.name}: PG type {type_name!r} missing")
                continue
            model_labels = set(column_type.enums)
            if db_labels != model_labels:
                failures.append(
                    f"{table.name}.{column.name}: DB labels {sorted(db_labels)} != "
                    f"model labels {sorted(model_labels)}"
                )
            python_labels = _expected_from_python(column_type)
            if python_labels != model_labels:
                failures.append(
                    f"{table.name}.{column.name}: model labels {sorted(model_labels)} != "
                    f"Python enum members {sorted(python_labels)}"
                )

    assert not failures, "enum ⇄ DB vocabulary drift:\n" + "\n".join(failures)
    assert checked >= MIN_MIRRORED_ENUM_COLUMNS, (
        f"only {checked} enum columns examined — the parity gate went vacuous"
    )


def test_vocabulary_checks_match_their_enums():
    """A CHECK that enumerates values must agree with the column's enum."""
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            checks = _vocabulary_checks(conn)
    finally:
        engine.dispose()

    failures: list[str] = []
    for key, (conname, check_values) in sorted(checks.items()):
        table_name, column_name = key.split(".", 1)
        table = Base.metadata.tables.get(table_name)
        if table is None or column_name not in table.columns:
            failures.append(f"{key}: CHECK {conname} names no mapped column")
            continue
        column_type = table.columns[column_name].type
        if not isinstance(column_type, sa.Enum):
            failures.append(
                f"{key}: CHECK {conname} enumerates values but the column is "
                f"{column_type!r}, not an enum — vocabulary without a source of truth"
            )
            continue
        python_labels = _expected_from_python(column_type)
        if check_values != python_labels:
            failures.append(
                f"{key}: CHECK {conname} allows {sorted(check_values)} but the enum "
                f"declares {sorted(python_labels)}"
            )

    assert not failures, "CHECK ⇄ enum vocabulary drift:\n" + "\n".join(failures)


def test_check_vocabulary_scanner_is_not_silently_blind():
    """Sanity-check the CHECK parser against the textual shapes PG renders.

    The shipped schema currently mirrors vocabularies as PG native type
    labels, so the CHECK half of the gate has nothing to compare; without this
    the parser could regress to matching nothing and the gate would be
    silently vacuous. Two shapes matter: an unquoted column and a reserved
    word quoted literally (family-migration §5b).
    """
    import types

    rows = [
        (
            "public.widgets",
            "ck_widgets_kind_allowed",
            "CHECK (((kind)::text = ANY ((ARRAY['a'::character varying, "
            "'b'::character varying])::text[])))",
        ),
        (
            "public.schedules",
            "ck_schedules_group_allowed",
            "CHECK (((\"group\")::text = ANY ((ARRAY['week'::character varying])::text[])))",
        ),
    ]
    fake_conn = types.SimpleNamespace(
        execute=lambda *a, **k: types.SimpleNamespace(all=lambda: rows)
    )

    parsed = _vocabulary_checks(fake_conn)
    assert parsed["widgets.kind"][1] == {"a", "b"}, f"unquoted vocabulary missed: {parsed}"
    assert parsed["schedules.group"][1] == {"week"}, f"quoted vocabulary missed: {parsed}"
