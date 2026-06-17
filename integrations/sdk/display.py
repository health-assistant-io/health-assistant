"""Structured display-block builders for custom action results.

Custom actions may return ``{"message": "...", "results": [...]}``. The
``message`` is shown as a toast (backwards compatible). The optional
``results`` array is a list of typed display blocks rendered by the
frontend's :mod:`ActionResultModal`.

This module documents the contract in one place and gives providers readable
builder functions. Block types:

- ``kv``    — key/value pairs (connection status, webhook URL, API ID).
- ``list``  — list of strings (tool names).
- ``table`` — tabular data (columns + rows).
- ``json``  — pretty-printed JSON (raw config dump).
- ``text``  — plain text (free-form notes).
- ``code``  — monospace block (commands, code snippets).

Unknown block types fall back to JSON rendering on the frontend, so adding
new types is safe.

Example::

    from integrations.sdk import kv_block, list_block

    return {
        "message": "Discovered 3 tool(s).",
        "results": [
            kv_block("Connection", {"transport": "stdio", "tools": 3}),
            list_block("Available Tools", ["echo", "add", "search"]),
        ],
    }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


_BLOCK_TYPE_KV = "kv"
_BLOCK_TYPE_LIST = "list"
_BLOCK_TYPE_TABLE = "table"
_BLOCK_TYPE_JSON = "json"
_BLOCK_TYPE_TEXT = "text"
_BLOCK_TYPE_CODE = "code"


def kv_block(title: str, items: Dict[str, Any]) -> Dict[str, Any]:
    """Key-value pairs. Renders as a two-column grid (key | value)."""
    return {"type": _BLOCK_TYPE_KV, "title": title, "items": dict(items)}


def list_block(title: str, items: Sequence[str]) -> Dict[str, Any]:
    """A simple list of strings. Renders as bullet rows."""
    return {"type": _BLOCK_TYPE_LIST, "title": title, "items": list(items)}


def table_block(
    title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> Dict[str, Any]:
    """Tabular data. ``rows[i]`` must have the same length as ``columns``."""
    return {
        "type": _BLOCK_TYPE_TABLE,
        "title": title,
        "columns": list(columns),
        "rows": [list(r) for r in rows],
    }


def json_block(title: str, data: Any) -> Dict[str, Any]:
    """Pretty-printed JSON. Use for raw config dumps or nested objects."""
    return {"type": _BLOCK_TYPE_JSON, "title": title, "data": data}


def text_block(title: Optional[str], content: str) -> Dict[str, Any]:
    """Plain text (may contain simple markdown)."""
    return {"type": _BLOCK_TYPE_TEXT, "title": title, "content": content}


def code_block(
    title: Optional[str], content: str, language: Optional[str] = None
) -> Dict[str, Any]:
    """Monospace block (commands, code). ``language`` is informational."""
    block: Dict[str, Any] = {"type": _BLOCK_TYPE_CODE, "title": title, "content": content}
    if language:
        block["language"] = language
    return block


def action_result(
    message: Optional[str] = None,
    results: Optional[List[Dict[str, Any]]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Assemble a custom-action response.

    ``message`` is shown as a toast. ``results`` is rendered in the result
    modal. ``**extra`` is merged in (e.g. for backwards-compat fields like
    the MCP provider's legacy ``tools`` key).
    """
    out: Dict[str, Any] = {}
    if message:
        out["message"] = message
    if results:
        out["results"] = results
    out.update(extra)
    return out
