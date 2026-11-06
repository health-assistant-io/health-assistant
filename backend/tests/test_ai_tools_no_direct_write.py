"""The chatbot write law: tools propose, they never commit.

Every write-shaped chat action goes through a ``propose_*`` HITL card whose
approval commits via the canonical REST endpoint. A tool-decorated function
that calls ``session.commit()`` bypasses that gate (and the approval card),
so this structural guard fails the build if one appears.
"""

from __future__ import annotations

import ast
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "app" / "ai" / "tools"


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


def _tool_functions(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            _decorator_name(dec) == "tool" for dec in node.decorator_list
        ):
            yield node


def test_no_chat_tool_commits_directly():
    offenders = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in _tool_functions(tree):
            for call in ast.walk(function):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "commit"
                ):
                    offenders.append(f"{path.name}:{function.name}")
    assert not offenders, (
        "chat tools must return a HITL proposal, never commit the session: "
        + ", ".join(sorted(set(offenders)))
    )
