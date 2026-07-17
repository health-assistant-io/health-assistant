"""Shared helpers for per-entity instance search functions.

Kept in one place so the seven entity modules stay concise and the ILIKE/label
logic is consistent (no copy-paste drift). Security-relevant note: these
helpers NEVER decide scope — the caller passes already-scoped queries. Tenant
and patient filters are applied by each entity module's ``search`` function.
"""
from __future__ import annotations

from typing import Any, Optional


def ilike_pattern(q: str) -> str:
    """Build an ILIKE pattern from a user query (case-insensitive substring).

    Mirrors the existing ``search.py`` / catalog convention (plain ``%{q}%``,
    no special-char escaping) for consistency with the rest of the codebase.
    """
    return f"%{q}%"


def code_text(code_col_value: Any) -> str:
    """Extract the human ``text`` from a FHIR-shaped JSONB ``code`` column.

    FHIR ``code`` is ``{"text": "Glucose", "coding": [...]}`` (or just a
    string in legacy rows). Returns ``""`` when absent so callers can build a
    fallback label without try/except.
    """
    if not code_col_value:
        return ""
    if isinstance(code_col_value, str):
        return code_col_value
    if isinstance(code_col_value, dict):
        text = code_col_value.get("text")
        if isinstance(text, str) and text:
            return text
        coding = code_col_value.get("coding")
        if isinstance(coding, list) and coding:
            first = coding[0]
            if isinstance(first, dict):
                return str(first.get("display") or first.get("code") or "")
        return ""
    return str(code_col_value)


def iso(value: Any) -> Optional[str]:
    """Best-effort ISO date string from a date/datetime column value."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            return str(value)
    return str(value)
