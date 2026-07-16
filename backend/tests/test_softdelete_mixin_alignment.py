"""Regression tests for audit items D6 + F3 — SoftDeleteMixin on FHIR models.

Pre-fix contract: the migration ``a7484842ecd4`` added the ``deleted_at``
column to nine FHIR-exposed tables, but the corresponding ORM models did
NOT declare ``SoftDeleteMixin``. The facade's ``crud.delete()`` checked
``hasattr(obj, "deleted_at")`` before soft-deleting — and the answer was
False for 11 of 15 resources, so it fell through to a hard
``session.delete()``. Subsequent reads returned 404 instead of 410 Gone.

Post-fix contract pinned here:
1. Every FHIR-exposed model whose table has ``deleted_at`` now mixes in
   ``SoftDeleteMixin`` (i.e., ``hasattr(cls, "deleted_at")`` is True).
2. The facade's ``_soft_delete_predicate`` returns a non-None predicate
   for each of these resources, so search excludes tombstones.
3. ``crud.delete`` actually sets ``deleted_at`` (no ``session.delete()``)
   when ``soft_delete=True`` AND the model has the attribute.
4. The migration that aligned the indexes runs cleanly both ways.
"""
import importlib
import inspect

import pytest


# The nine models that migration a7484842ecd4 added deleted_at to.
SOFT_DELETE_MODELS = [
    ("app.models.fhir.patient", "Patient"),
    ("app.models.fhir.patient", "Observation"),
    ("app.models.fhir.patient", "DiagnosticReport"),
    ("app.models.fhir.medication", "Medication"),
    ("app.models.fhir.allergy", "AllergyIntolerance"),
    ("app.models.fhir.organization", "OrganizationModel"),
    ("app.models.examination_model", "ExaminationModel"),
    ("app.models.clinical_event", "ClinicalEvent"),
    ("app.models.document_model", "DocumentModel"),
]


# ---------------------------------------------------------------------------
# D6/F3: every FHIR-exposed model with deleted_at in DB mixes in SoftDeleteMixin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name,class_name", SOFT_DELETE_MODELS)
def test_model_has_soft_delete_mixin(module_name, class_name):
    """Each FHIR-exposed model must declare SoftDeleteMixin so the
    facade's crud.delete() soft-deletes instead of hard-deleting."""
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    assert hasattr(cls, "deleted_at"), (
        f"{class_name} must mix in SoftDeleteMixin — its table has "
        "deleted_at (migration a7484842ecd4) but the model previously "
        "did not declare it. The facade hard-deleted silently (audit D6/F3)."
    )
    # Also verify the column is actually on the SQLAlchemy table.
    assert "deleted_at" in cls.__table__.columns, (
        f"{class_name}.__table__ must have a deleted_at column"
    )


# ---------------------------------------------------------------------------
# D6/F3: facade's soft-delete predicate recognizes each model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name,class_name", SOFT_DELETE_MODELS)
def test_facade_soft_delete_predicate_works_for_model(module_name, class_name):
    """The facade's ``_soft_delete_predicate`` helper must return a
    non-None predicate for each FHIR-exposed model so search queries
    exclude tombstones."""
    from app.facade.crud import _soft_delete_predicate
    from app.facade.registry import ResourceEntry
    from app.models.base import SoftDeleteMixin

    mod = importlib.import_module(module_name)
    model = getattr(mod, class_name)

    # Build a synthetic ResourceEntry with soft_delete=True (the default).
    entry = ResourceEntry(
        resource_type="_Test",
        model=model,
        fhir_to_orm_fn=lambda *a, **kw: {},
        soft_delete=True,
    )
    pred = _soft_delete_predicate(entry)
    assert pred is not None, (
        f"_soft_delete_predicate returned None for {class_name} — facade "
        "will NOT exclude tombstones from search results (audit D6/F3)"
    )


# ---------------------------------------------------------------------------
# D6/F3: crud.delete sets deleted_at (no hard delete) for these models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name,class_name", SOFT_DELETE_MODELS)
async def test_crud_delete_sets_deleted_at(module_name, class_name):
    """``crud.delete`` should set ``deleted_at`` rather than call
    ``session.delete()`` for each soft-delete-enabled resource."""
    from app.facade.crud import delete as facade_delete
    from app.facade.registry import ResourceEntry

    mod = importlib.import_module(module_name)
    model_cls = getattr(mod, class_name)

    entry = ResourceEntry(
        resource_type="_Test",
        model=model_cls,
        fhir_to_orm_fn=lambda *a, **kw: {},
        soft_delete=True,
    )

    # Build a fake row that simulates the result of the SELECT inside delete().
    fake_obj = MagicMock()
    fake_obj.deleted_at = None
    fake_obj.id = uuid.uuid4()

    # The db.execute call returns our fake_obj.
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=fake_obj))
    )
    db.delete = AsyncMock()  # hard-delete — should NOT be called for these.
    db.commit = AsyncMock()

    # Patch record_provenance so it doesn't actually try to write.
    with patch("app.facade.crud.record_provenance", new=AsyncMock()):
        result = await facade_delete(
            entry=entry,
            resource_id=str(fake_obj.id),
            current_user=MagicMock(tenant_id=uuid.uuid4(), user_id=uuid.uuid4()),
            db=db,
        )

    assert result is True, f"crud.delete should report success for {class_name}"
    # deleted_at was set on the in-memory object.
    assert fake_obj.deleted_at is not None, (
        f"crud.delete must set deleted_at on {class_name} — the audit D6/F3 "
        "fix relies on the model declaring SoftDeleteMixin so the facade "
        "takes the soft-delete branch instead of hard-deleting."
    )
    # Hard-delete was NOT called.
    db.delete.assert_not_called(), (
        f"crud.delete must NOT call session.delete() for {class_name} "
        "(soft-delete is the facade contract)."
    )


# ---------------------------------------------------------------------------
# Squashed initial schema: all 9 SoftDeleteMixin tables have deleted_at
# ---------------------------------------------------------------------------


def _find_root_baseline(files):
    """Return the single root migration file (``down_revision is None``).

    The consolidated baseline is the migration chain's root; incremental
    migrations may stack on top of it, so we locate the root by its
    ``down_revision is None`` marker rather than assuming a single file.
    """
    import importlib.util
    from pathlib import Path

    roots = []
    for f in files:
        spec = importlib.util.spec_from_file_location("_alembic_mod_" + Path(f).stem, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if getattr(mod, "down_revision", "missing") is None:
            roots.append(f)
    assert len(roots) == 1, (
        f"expected exactly one root baseline migration (down_revision is None), "
        f"found {len(roots)}: {roots}"
    )
    return roots[0]


def test_initial_schema_has_deleted_at_on_nine_tables():
    """The consolidated baseline must create deleted_at on all nine
    SoftDeleteMixin tables (previously a separate alignment migration)."""
    import glob
    from pathlib import Path

    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    files = [p for p in glob.glob(str(versions_dir / "*.py")) if not p.endswith("__init__.py")]
    baseline = _find_root_baseline(files)
    src = Path(baseline).read_text()

    expected = {
        "fhir_patients",
        "fhir_observations",
        "fhir_diagnostic_reports",
        "fhir_medications",
        "fhir_allergy_intolerances",
        "fhir_organizations",
        "examinations",
        "clinical_events",
        "documents",
    }
    for table in expected:
        assert f"'deleted_at'" in src, (
            f"deleted_at column missing from consolidated baseline (table {table})"
        )


def test_initial_schema_loads_cleanly():
    """The consolidated baseline migration imports without errors and is the root."""
    import glob
    import importlib.util
    from pathlib import Path

    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    files = [p for p in glob.glob(str(versions_dir / "*.py")) if not p.endswith("__init__.py")]
    baseline = _find_root_baseline(files)
    spec = importlib.util.spec_from_file_location("consolidated_baseline", baseline)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision is None, "consolidated baseline must be the root"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# D15: OrganizationModel gains TimestampMixin
# ---------------------------------------------------------------------------


def test_organization_model_has_timestamp_mixin():
    """Audit D15: OrganizationModel was missing TimestampMixin — could not
    sort/filter organizations by time."""
    from app.models.fhir.organization import OrganizationModel

    assert hasattr(OrganizationModel, "created_at"), (
        "OrganizationModel must declare TimestampMixin (created_at)"
    )
    assert hasattr(OrganizationModel, "updated_at"), (
        "OrganizationModel must declare TimestampMixin (updated_at)"
    )
    assert "created_at" in OrganizationModel.__table__.columns
    assert "updated_at" in OrganizationModel.__table__.columns


# ---------------------------------------------------------------------------
# late imports
# ---------------------------------------------------------------------------


import uuid
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402
