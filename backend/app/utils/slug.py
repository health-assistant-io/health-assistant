"""Shared slugify utility.

Thin wrapper around the ``python-slugify`` dependency so every caller
(tenant services, create-system-admin CLI) gets identical behaviour.
Returns ``fallback`` for empty/None input because ``tenants.slug`` is
NOT NULL.
"""

from __future__ import annotations

from slugify import slugify as _slugify


def slugify(name: str, fallback: str = "tenant") -> str:
    """Convert a name to a URL-safe slug (lowercase, '-' separators)."""
    return _slugify(name or "", separator="-") or fallback
