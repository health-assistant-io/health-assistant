"""Catalog identity — the shared sentinel scope and seed-key rules (ADR 0055).

One home for the two constants and the two functions the schema, migrations
and (Phase 3) seeders share:

* :data:`NULL_TENANT_SENTINEL` — the all-zero UUID that stands in for the
  system scope inside ``COALESCE(tenant_id, sentinel)`` expression indexes.
  It exists so NULL-tenant (system) rows share one namespace instead of the
  NULL-distinct behavior Postgres applies to plain unique constraints; a
  tenant overlay may reuse a system ``seed_key`` while two rows in the same
  scope may not. The value mirrors ``_NULL_TENANT`` in
  ``alembic/versions/0001_baseline.py`` (a literal there — migrations keep
  app imports minimal; both stay in sync by review).
* :data:`SEED_KEY_NAMESPACE` — the fixed UUIDv5 namespace curated catalog
  identity is derived in (``uuid5(SEED_KEY_NAMESPACE, "<type>:<seed_key>")``),
  so reseeds are id-stable and independent of insertion order.
* :func:`validate_seed_key` — the immutable qualified key form
  ``<type>:<kebab-key>``; the key is written once and never renamed
  (a rename would fork rows and change the derived UUID).
* :func:`seed_key_uuid` — the deterministic identity for a curated row.

Dependency-free on purpose: importable by the seeders, the pack tools and
the migrations without pulling app services.
"""

from __future__ import annotations

import re
import uuid
from typing import Final

NULL_TENANT_SENTINEL: Final[str] = "00000000-0000-0000-0000-000000000000"
"""The system-scope stand-in for ``COALESCE(tenant_id, sentinel)`` indexes.

The all-zero UUID: it is never a real tenant id (PKs are ``gen_random_uuid()``),
sorts first, and renders the same in every environment.
"""

NULL_TENANT_SENTINEL_UUID: Final[uuid.UUID] = uuid.UUID(NULL_TENANT_SENTINEL)

SEED_KEY_NAMESPACE: Final[uuid.UUID] = uuid.UUID("0a0f2617-5d80-5bbd-baad-2d737aff458b")
"""The UUIDv5 namespace for curated catalog identity (frozen — never change).

Changing it would silently re-identity every curated row on the next reseed.
Derived once from ``uuid5(NAMESPACE_URL, 'https://neuronection.dev/'
'health-assistant/catalog-seed-keys')`` and frozen here.
"""

_CATALOG_TYPE_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}\Z")
_SEED_KEY_BODY_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,119}\Z")
_QUALIFIED_SEED_KEY_RE = re.compile(
    r"^(?P<catalog_type>[a-z][a-z0-9-]{1,63}):(?P<seed_key>[a-z0-9][a-z0-9._/-]{0,119})\Z"
)
"""The qualified seed-key form: ``<type>:<kebab-key>`` (kebab-or-slash body).

The body charset matches the pack contract's seed-key rule
(``seed_pack._SEED_KEY_RE``) so a pack ``replaces`` entry and a catalog row
describe the same key.
"""


def validate_seed_key(qualified: str) -> tuple[str, str]:
    """Validate the qualified seed-key form and split it into its parts.

    The qualified form is ``<catalog_type>:<seed_key>`` — kebab-case type,
    kebab-or-slash key, e.g. ``medication:amoxicillin-500mg``. A key is
    **immutable once written**: seeds upsert by it and the derived UUID comes
    from it, so renaming a key is a new row (with ``replaces`` in the pack),
    never an edit.

    Returns the ``(catalog_type, seed_key)`` pair.

    Raises:
        ValueError: when the input is not a string of the qualified form.
    """
    if not isinstance(qualified, str):
        raise ValueError(f"seed key must be a string, got {type(qualified).__name__}")
    match = _QUALIFIED_SEED_KEY_RE.match(qualified)
    if match is None:
        raise ValueError(
            f"invalid seed key {qualified!r}: expected '<type>:<key>' with a "
            "kebab-case type and a kebab-or-slash key (lowercase)"
        )
    return match.group("catalog_type"), match.group("seed_key")


def seed_key_uuid(catalog_type: str, seed_key: str) -> uuid.UUID:
    """The deterministic identity of a curated catalog row.

    ``uuid5(SEED_KEY_NAMESPACE, "<catalog_type>:<seed_key>")`` — stable
    across reseeds and machines; derived from the immutable key, never from
    the name or code (a code correction cannot churn ids, ADR 0055).
    """
    if not isinstance(catalog_type, str) or not _CATALOG_TYPE_RE.match(catalog_type):
        raise ValueError(f"invalid catalog type {catalog_type!r}")
    if not isinstance(seed_key, str) or not _SEED_KEY_BODY_RE.match(seed_key):
        raise ValueError(f"invalid seed key {seed_key!r}")
    return uuid.uuid5(SEED_KEY_NAMESPACE, f"{catalog_type}:{seed_key}")
