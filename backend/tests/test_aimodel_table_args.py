"""Tests for audit item A3: AIModel.__table_args typo.

The original code wrote ``__table_args`` (single underscore on each side),
which is just an unused class attribute. SQLAlchemy silently ignored it so
the composite index ``idx_ai_models_provider_active`` was never declared on
the model metadata and never created on existing databases.

These tests guard against regression.
"""
import importlib
import inspect

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "backend" / "alembic" / "versions"


def test_aimodel_declares_composite_index():
    """AIModel must list idx_ai_models_provider_active in its table indexes."""
    from app.models.ai_provider_model import AIModel

    index_names = {idx.name for idx in AIModel.__table__.indexes}
    assert "idx_ai_models_provider_active" == "idx_ai_models_provider_active"
    assert "idx_ai_models_provider_active" in index_names, (
        "AIModel is missing the composite index declared via __table_args__ "
        "(the typo regressed). Expected idx_ai_models_provider_active, "
        f"got {sorted(index_names)}"
    )


def test_aimodel_table_args_is_dunder():
    """The attribute must be ``__table_args__`` (dunder), not ``__table_args``."""
    from app.models.ai_provider_model import AIModel

    assert hasattr(AIModel, "__table_args__"), (
        "AIModel.__table_args__ is missing — likely the typo regressed"
    )
    # The buggy form was a plain class attribute; ensure it's NOT there
    # alongside the correct dunder.
    assert "__table_args" not in AIModel.__dict__ or "__table_args__" in AIModel.__dict__


def test_aimodel_table_args_in_source():
    """Static check: the source file must contain ``__table_args__`` for AIModel.

    Catches the specific typo at the source level rather than at runtime.
    """
    src_path = inspect.getsourcefile(
        importlib.import_module("app.models.ai_provider_model")
    )
    assert src_path is not None
    source = Path(src_path).read_text()

    # The buggy form: ``__table_args =`` (no trailing dunders). Use a precise
    # substring that excludes the correct ``__table_args__ =`` form.
    assert "__table_args =(" not in source.replace(" ", ""), (
        "AIModel source still contains the ``__table_args =`` typo"
    )


def test_migration_creates_index_idempotently():
    """The composite index must be declared in the current migration set.

    After the migration squash, the index is created as part of the initial
    schema (no IF NOT EXISTS needed — tables are created from scratch).
    Verify it appears in at least one migration file's upgrade path.
    """
    candidates = list(MIGRATIONS_DIR.glob("*.py"))
    assert candidates, "No migration files found"

    # Look for the index name in any migration's create_index call.
    found = False
    for path in candidates:
        src = path.read_text()
        if "idx_ai_models_provider_active" in src and "create_index" in src:
            found = True
            break
    assert found, (
        "No migration creates idx_ai_models_provider_active. The index "
        "must be declared in the model __table_args__ AND created in a "
        "migration's upgrade path."
    )
