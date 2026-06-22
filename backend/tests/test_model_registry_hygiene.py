"""Tests for audit items A5 and A7 (model hygiene).

A5: ``backend/app/processors/fhir_mapper.py`` was dead code with a broken
    import (``from app.models.fhir.observation import Observation`` — that
    module does not exist; Observation lives in ``models/fhir/patient.py``).
    The class ``FHIRMapper`` was not referenced anywhere in the codebase.
    Importing the module would have raised ``ModuleNotFoundError``.

A7: ``backend/app/models/__init__.py`` listed ``"WearableDataModel"`` in
    ``__all__`` but never imported it (the class was renamed to
    ``TelemetryDataModel``). ``from app.models import *`` therefore raised
    ``AttributeError``.
"""
import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"


def test_fhir_mapper_module_removed():
    """A5: the dead ``fhir_mapper.py`` file must no longer exist."""
    assert not (BACKEND / "app" / "processors" / "fhir_mapper.py").exists(), (
        "fhir_mapper.py should have been deleted"
    )


def test_fhir_mapper_not_importable():
    """A5: ``app.processors.fhir_mapper`` must not be importable."""
    sys.modules.pop("app.processors.fhir_mapper", None)
    sys.modules.pop("app.processors", None)
    # Re-import the package to clear any cached state
    import app.processors  # noqa: F401

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.processors.fhir_mapper")


def test_models_star_import_succeeds():
    """A7: ``from app.models import *`` must not raise."""
    code = "from app.models import *  # noqa"
    namespace: dict = {}
    exec(code, namespace)
    assert "TelemetryDataModel" in namespace, (
        "TelemetryDataModel (the renamed WearableDataModel) must be exported"
    )


def test_wearabledata_alias_absent():
    """A7: the stale ``WearableDataModel`` alias must not appear in __all__."""
    import app.models as models_pkg

    assert "WearableDataModel" not in models_pkg.__all__, (
        "models.__all__ still references the renamed WearableDataModel"
    )


def test_telemetrydatamodel_is_exported():
    """A7 regression guard: the renamed class must remain importable."""
    import app.models as models_pkg

    assert "TelemetryDataModel" in models_pkg.__all__
    assert hasattr(models_pkg, "TelemetryDataModel")


def test_no_other_references_to_wearabledata():
    """A7: grep the source tree for any lingering WearableDataModel usage.

    The test file itself (this one) is allowed to mention the name in its
    own docstrings/assertions, so we exclude it from the scan.
    """
    ignore_dirs = {".git", ".ruff_cache", "__pycache__", "node_modules", "venv"}
    ignore_files = {"test_model_registry_hygiene.py"}
    hits: list[str] = []

    for path in BACKEND.rglob("*.py"):
        if any(part in ignore_dirs for part in path.parts):
            continue
        if path.name in ignore_files:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "WearableDataModel" in text:
            hits.append(str(path.relative_to(BACKEND)))

    assert not hits, (
        "WearableDataModel is still referenced in: " + ", ".join(hits)
    )
