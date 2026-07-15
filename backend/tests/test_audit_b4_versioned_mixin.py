"""Regression tests for audit B4: dead ``VersionedMixin.is_current`` removed.

The audit (B4) flagged ``VersionedMixin`` as dead data. Investigation showed
the claim was only half-true:

- ``is_current`` — genuinely never queried anywhere in the codebase (only
  listed in a mass-assignment readonly guard). Removed via migration
  ``q7a8b9c0d1e2``.
- ``version`` — a LIVE feature. The FHIR R4 facade bumps it on every update
  (``app/facade/crud.update``), reads it for ``If-Match`` optimistic locking
  (HTTP 412), and exposes it in the ``ETag`` header (``W/"<version>"``).
  Removing it would break ``test_fhir_r4_versioning.py``. Kept deliberately.

These tests pin both invariants so a future "clean up VersionedMixin" pass
can't silently drop the live ``version`` column or re-add the dead
``is_current`` one.
"""
from __future__ import annotations

import pytest

from app.models.base import Base, VersionedMixin
import app.models  # noqa: F401  — register every model on Base.metadata


def test_versioned_mixin_defines_only_version():
    """``VersionedMixin`` must expose ``version`` and NOT ``is_current``."""
    assert hasattr(VersionedMixin, "version"), "version column must remain"
    assert not hasattr(VersionedMixin, "is_current"), (
        "is_current was dead data (audit B4) and must not be re-added"
    )


def test_no_table_has_is_current_column():
    """No registered table may carry the dropped ``is_current`` column."""
    offenders = [
        tname
        for tname, table in Base.metadata.tables.items()
        if "is_current" in table.c
    ]
    assert offenders == [], f"unexpected is_current column on: {offenders}"


@pytest.mark.parametrize(
    "table_name",
    [
        # Spot-check a representative cross-section of the 18 versioned tables —
        # the column-level check above is exhaustive; this confirms `version`
        # is still present where it matters.
        "fhir_patients",
        "fhir_observations",
        "examinations",
        "clinical_events",
        "biomarker_definitions",
        "users",
    ],
)
def test_version_column_still_present(table_name: str):
    """``version`` must survive on versioned tables (live ETag primitive)."""
    table = Base.metadata.tables[table_name]
    assert "version" in table.c, f"{table_name} lost its version column"
