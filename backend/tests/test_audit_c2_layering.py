"""Regression tests for audit C2: service layer must not import from the router.

The audit (C2) found a layer inversion: ``clinical_event_service`` imported the
``check_*_access`` helpers from ``app.api.v1.endpoints.utils``, coupling the
service layer to the router (and to ``HTTPException``), which made the service
unusable from Celery / import / facade contexts.

The fix moved all seven helpers to ``app.services.access`` (raising domain
exceptions instead of ``HTTPException``) and deleted the router-layer
``endpoints/utils.py``. These tests pin the invariant so the inversion can't
silently return: no module under ``app/services/`` (or ``app/ai/``,
``app/workers/``, ``app/facade/``) may import from ``app.api.v1.endpoints``.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import app.services.access as access_module

_SERVICES_ROOT = Path(access_module.__file__).resolve().parent
# Layer-inversion boundary: these packages are BELOW the router and must never
# reach up into ``app.api.v1.endpoints``.
_FORBIDDEN_PREFIXES = ("app.api.v1.endpoints", "app.api.v1.endpoints.")
_LOWER_PACKAGES = [
    _SERVICES_ROOT,  # app/services
    _SERVICES_ROOT.parent / "ai",  # app/ai
    _SERVICES_ROOT.parent / "workers",  # app/workers
    _SERVICES_ROOT.parent / "facade",  # app/facade
]


def _imports_from_endpoints(source: str, module_file: Path) -> list[str]:
    """Return any ``app.api.v1.endpoints`` import names found in ``source``."""
    try:
        tree = ast.parse(source, filename=str(module_file))
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.api.v1.endpoints" or alias.name.startswith(
                    "app.api.v1.endpoints."
                ):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "app.api.v1.endpoints"
                or node.module.startswith("app.api.v1.endpoints.")
            ):
                hits.append(node.module)
    return hits


def _iter_python_files(roots: list[Path]):
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            yield path


def test_access_module_defines_all_seven_helpers():
    """``app.services.access`` must own every canonical access helper."""
    expected = {
        "check_patient_access",
        "check_examination_access",
        "check_medication_access",
        "check_immunization_access",
        "check_event_access",
        "check_allergy_access",
        "check_observation_access",
    }
    present = {
        name
        for name in expected
        if hasattr(access_module, name) and callable(getattr(access_module, name))
    }
    assert present == expected, f"missing helpers: {expected - present}"


def test_endpoints_utils_module_removed():
    """The router-layer ``endpoints/utils.py`` must stay deleted."""
    import app.api.v1.endpoints as endpoints_pkg

    utils_path = Path(endpoints_pkg.__file__).parent / "utils.py"
    assert not utils_path.exists(), (
        "app/api/v1/endpoints/utils.py was resurrected — the access helpers "
        "must live in app/services/access.py (audit C2)"
    )


@pytest.mark.parametrize("file", sorted(_iter_python_files(_LOWER_PACKAGES)))
def test_no_lower_layer_imports_from_router(file: Path):
    """No service/ai/worker/facade module may import from app.api.v1.endpoints."""
    source = file.read_text(encoding="utf-8")
    hits = _imports_from_endpoints(source, file)
    # The access module's own docstring mentions the old path for historical
    # context — that's a string literal, not an import, so ast won't match it.
    assert hits == [], f"{file.relative_to(_SERVICES_ROOT.parent.parent)} imports from router layer: {hits}"


def test_clinical_event_service_uses_access_module():
    """The original offender must now resolve its access helpers from services."""
    import app.services.clinical_event_service as svc

    # The four helpers it needs must resolve to the access-module objects
    # (same identity), proving it no longer pulls them from the router.
    for name in (
        "check_event_access",
        "check_examination_access",
        "check_observation_access",
        "check_patient_access",
    ):
        assert getattr(svc, name) is getattr(access_module, name), (
            f"clinical_event_service.{name} is not app.services.access.{name}"
        )


def test_endpoints_still_resolve_helpers():
    """Endpoint modules must resolve the helpers via app.services.access."""
    import importlib

    examinations = importlib.import_module("app.api.v1.endpoints.examinations")
    assert examinations.check_patient_access is access_module.check_patient_access
    assert examinations.check_examination_access is access_module.check_examination_access
