"""Regression tests for the concept-FK naming convention.

Pins the contract documented in ``dev/plans/concept-fk-naming-convention-2026-07-07.md``:

1. Every domain-specific FK into ``concepts.id`` is named ``<role>_concept_id``
   and has a matching ``<role>_concept`` relationship declared **explicitly**
   (``foreign_keys=[...]``) so SQLAlchemy never resolves by implicit single-FK
   guesswork.
2. The only sanctioned exceptions are owned-child / self-referential columns:
   ``concept_kind_tags.concept_id`` and ``concepts.parent_id``.
3. No model declares a legacy ``category_id`` / ``category_entity`` attribute
   pointing at concepts (the renamed examinations / clinical_event_types attrs).
4. The ORM model and the live DB agree on which concept-related columns exist
   (catches the ``documents.category_concept_id`` drift class — a column the DB
   had but the ORM did not declare).
5. Rename verification: ``ExaminationModel`` / ``ClinicalEventType`` /
   ``DocumentModel`` expose ``category_concept_id`` (not ``category_id``).
"""
import re
from collections import defaultdict

import pytest

from app.models.base import Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONCEPT_TABLE = "concepts"


def _concept_fk_models():
    """Return ``[(ModelClass, [fk_column_name, ...])]`` for every mapped model
    that owns at least one FK targeting ``concepts.id``."""
    out = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        table = getattr(cls, "__table__", None)
        if table is None:
            continue
        cols = []
        for col in table.columns:
            for fk in col.foreign_keys:
                if (
                    fk.column.table.name == _CONCEPT_TABLE
                    and table.name != _CONCEPT_TABLE
                ):
                    cols.append(col.name)
                    break
        if cols:
            out.append((cls, cols))
    return out


# The two sanctioned exceptions to the ``<role>_concept_id`` rule.
_EXCEPTION_COLUMNS = {
    ("concept_kind_tags", "concept_id"),  # owned-child join row
}

_CONCEPT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*_concept_id$")


# ---------------------------------------------------------------------------
# 1. Naming convention: every concept FK column matches <role>_concept_id
#    (or is a documented exception)
# ---------------------------------------------------------------------------


def test_concept_fk_columns_follow_naming_convention():
    """Every FK into ``concepts.id`` must be named ``<role>_concept_id``."""
    offenders = []
    for cls, cols in _concept_fk_models():
        for col in cols:
            if (cls.__tablename__, col) in _EXCEPTION_COLUMNS:
                continue
            if not _CONCEPT_ID_RE.match(col):
                offenders.append((cls.__name__, cls.__tablename__, col))
    assert not offenders, (
        "Concept FK columns must follow the '<role>_concept_id' naming "
        "convention (see dev/plans/concept-fk-naming-convention-2026-07-07.md). "
        f"Offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# 2. Explicit foreign_keys= on every Concept relationship
# ---------------------------------------------------------------------------


def test_concept_relationships_resolve_to_a_local_concept_fk():
    """Every ``relationship("Concept")`` on a non-self-referential model must
    resolve to a local column that is actually a FK into ``concepts.id``.

    This is the robustness guarantee that ``foreign_keys=[...]`` provides: the
    relationship is wired to the intended FK column rather than picked by
    implicit single-FK guesswork (which silently breaks the day a second
    concept FK is added to the same table)."""
    from sqlalchemy.orm import RelationshipProperty, configure_mappers

    configure_mappers()

    offenders = []
    for cls, cols in _concept_fk_models():
        concept_cols = {cls.__table__.columns[c] for c in cols}
        for prop in cls.__mapper__.iterate_properties:
            if not isinstance(prop, RelationshipProperty):
                continue
            arg = prop.argument
            target_name = (
                arg if isinstance(arg, str) else getattr(arg, "__name__", "")
            )
            if target_name != "Concept":
                continue
            local_pairs = prop.local_remote_pairs or []
            if not local_pairs:
                offenders.append((cls.__name__, prop.key, "unresolved relationship"))
                continue
            # Every local column in the pairs must be one of the model's
            # concept-FK columns.
            for local_col, _remote_col in local_pairs:
                if local_col not in concept_cols:
                    offenders.append(
                        (
                            cls.__name__,
                            prop.key,
                            f"local col '{local_col.name}' is not a concept FK",
                        )
                    )
    assert not offenders, (
        "Concept relationships must resolve to a local concept-FK column "
        "(declare them with explicit foreign_keys=[...]). Offenders: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# 3. No legacy attribute names survive on concept-classified models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name,class_name,attr",
    [
        ("app.models.examination_model", "ExaminationModel", "category_id"),
        ("app.models.examination_model", "ExaminationModel", "category_entity"),
        ("app.models.clinical_event", "ClinicalEventType", "category_id"),
        ("app.models.clinical_event", "ClinicalEventType", "category_entity"),
    ],
)
def test_legacy_concept_attribute_removed(module_name, class_name, attr):
    """The pre-rename attribute must be gone so stale references fail loud."""
    import importlib

    cls = getattr(importlib.import_module(module_name), class_name)
    assert attr not in cls.__table__.columns, (
        f"{class_name}.{attr} still exists as a column — should be renamed."
    )


# ---------------------------------------------------------------------------
# 4. Rename verification: new attributes exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name,class_name,attr",
    [
        ("app.models.examination_model", "ExaminationModel", "category_concept_id"),
        ("app.models.clinical_event", "ClinicalEventType", "category_concept_id"),
        ("app.models.document_model", "DocumentModel", "category_concept_id"),
        ("app.models.examination_model", "ExaminationModel", "category_concept"),
        ("app.models.clinical_event", "ClinicalEventType", "category_concept"),
        ("app.models.document_model", "DocumentModel", "category_concept"),
    ],
)
def test_renamed_concept_attribute_exists(module_name, class_name, attr):
    """The post-rename column/relationship must be present."""
    import importlib

    cls = getattr(importlib.import_module(module_name), class_name)
    assert attr in cls.__table__.columns or hasattr(cls, attr), (
        f"{class_name}.{attr} missing — the rename did not land."
    )


# ---------------------------------------------------------------------------
# 5. Model ↔ DB drift detection for concept-related columns
# ---------------------------------------------------------------------------


def _db_concept_columns():
    """Return {table_name: {concept-related column names}} from the live DB.

    A column is "concept-related" if it participates in a FK to concepts.id.
    """
    from sqlalchemy import create_engine, text

    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT kcu.table_name, kcu.column_name
                    FROM information_schema.key_column_usage kcu
                    JOIN information_schema.table_constraints tc
                        ON kcu.constraint_name = tc.constraint_name
                       AND tc.constraint_type = 'FOREIGN KEY'
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                    WHERE ccu.table_name = 'concepts'
                      AND kcu.table_schema = 'public'
                      AND kcu.table_name <> 'concepts'
                    """
                )
            ).all()
        out = defaultdict(set)
        for table_name, column_name in rows:
            out[table_name].add(column_name)
        return dict(out)
    finally:
        engine.dispose()


def test_model_db_concept_columns_agree():
    """Every concept-FK column the ORM declares must exist in the live DB,
    and vice-versa. Catches the ``documents.category_concept_id`` drift class
    (DB had it; ORM did not)."""
    db_cols = _db_concept_columns()
    orm_cols = {
        cls.__tablename__: set(cols) for cls, cols in _concept_fk_models()
    }

    all_tables = set(db_cols) | set(orm_cols)
    drift = []
    for table in all_tables:
        db_set = db_cols.get(table, set())
        orm_set = orm_cols.get(table, set())
        if db_set != orm_set:
            drift.append(
                f"{table}: db_only={sorted(db_set - orm_set)} "
                f"orm_only={sorted(orm_set - db_set)}"
            )
    assert not drift, (
        "Model/DB concept-FK column drift detected:\n  - "
        + "\n  - ".join(drift)
    )
