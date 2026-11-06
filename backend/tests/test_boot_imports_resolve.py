"""Regression tests for boot-path imports (the seed_service incident).

Pre-fix contract: commit 212f5688 (the seed-service split, 2026-10-25)
renamed the module-level ``seed_service`` singleton into the ``SeedService``
class but updated ``app/main.py``'s import to keep the dead name. The
backend could not boot for four days without a single test noticing:
``conftest`` imports ``app.main`` (module level, fine) but the broken
import sits INSIDE ``lifespan()``, which only executes when uvicorn
actually starts the app — "Application startup failed. Exiting."

Pinned here: every ``from X import Y`` statement in ``app/main.py`` and
the ``scripts/seed_*.py`` operator tools must resolve against the real
modules — module-level AND function-level, since the function-level ones
are exactly the ones no test import ever executes.
"""

import ast
import importlib
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# The boot path plus the operator tools that were broken by the same
# refactor. Parsing (not importing) the scripts keeps their module-level
# code from running.
SCANNED_FILES = [
    BACKEND_ROOT / "app" / "main.py",
    *sorted((BACKEND_ROOT / "scripts").glob("seed_*.py")),
]


def _iter_import_from(path: Path) -> list[ast.ImportFrom]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]


@pytest.mark.parametrize("path", SCANNED_FILES, ids=lambda p: p.name)
def test_every_import_in_boot_path_resolves(path: Path):
    """Every absolute ``from X import Y`` in the file must resolve —
    a name that vanished from its module fails here instead of at the
    next production/dev boot."""
    broken = []
    for node in _iter_import_from(path):
        if node.level != 0 or not node.module:
            continue  # relative imports don't occur in these files
        try:
            module = importlib.import_module(node.module)
        except ImportError as e:
            broken.append(f"{path.name}:{node.lineno} module {node.module}: {e}")
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            if not hasattr(module, alias.name):
                broken.append(
                    f"{path.name}:{node.lineno} "
                    f"from {node.module} import {alias.name} — name does not exist"
                )
    assert not broken, (
        "Boot-path imports reference names/modules that no longer exist "
        "(these fail at app startup, not at test import time):\n  " + "\n  ".join(broken)
    )
