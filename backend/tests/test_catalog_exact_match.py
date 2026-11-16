"""Regression tests for MedicationCatalog / AllergyCatalog uniqueness matching (C9).

Pre-fix contract: catalog "uniqueness" checks at three sites used
``Catalog.name.ilike(name)``. ILIKE without wildcards does exact
case-insensitive matching on its own, BUT treats ``_`` and ``%`` in
``name`` as wildcards. So a catalog name like ``"Vitamin_B12"`` would
match a row literally named ``"Vitamin-B12"`` (underscore matches any
single char) or ``"Vitamin B12"`` — silently deduping distinct entries.
``"HCTZ_50"`` would match ``"HCTZ-50"``. The intent (case-insensitive
exact match) was right; the implementation was wrong.

Post-fix contract pinned here: the remaining creation-dedupe site uses
``func.lower(Catalog.name) == func.lower(name)`` instead of ``ilike``.
This treats ``_`` and ``%`` as literals and produces a strict
case-insensitive exact match.

One name-matching site remains — the AI pipeline's create-dedupe, now
tenant-scoped:
- ``app/ai/pipeline/ontology.py`` — ``process_unknown_medications``

(ADR 0055 removed name-matching from the SEEDERS and then from the sidecar
import: both upsert by deterministic id/seed_key — see
``tests/test_seed_identity.py`` and ``tests/test_catalog_writer_scope.py`` —
so those sites no longer compare names at all.)

Catalog *search* features (``catalog_search_service.py``,
``allergy_service.py``) legitimately use ``ilike('%q%')`` for substring
search and are intentionally NOT changed.
"""

import inspect


def _source_contains(module, fn_name, needle: str) -> bool:
    """Return True if ``needle`` appears in the source of ``module.fn_name``."""
    fn = getattr(module, fn_name)
    return needle in inspect.getsource(fn)


def test_c9_ontology_uses_case_insensitive_exact_match():
    """ontology.process_unknown_medications: no ilike(name), uses func.lower ==."""
    from app.ai.pipeline import ontology

    src = inspect.getsource(ontology.process_unknown_medications)
    assert "func.lower(MedicationCatalog.name)" in src, (
        "process_unknown_medications must use func.lower(...) == func.lower(name) "
        "for case-insensitive EXACT match, not ilike (which treats _ and % as wildcards)."
    )
    # The ilike form must NOT be used as a uniqueness check.
    assert "MedicationCatalog.name.ilike(def_data.name)" not in src


def test_c9_seed_service_medication_catalog_never_matches_by_name():
    """seed_service medication seeding: upserts by identity (id/seed_key) —
    name matching is gone entirely (ADR 0055; supersedes the func.lower fix)."""
    from app.services.seed import medications as seed_medications

    src = inspect.getsource(seed_medications)
    assert "func.lower(MedicationCatalog.name)" not in src, (
        "seed_service medication seeding must not match rows by name (ADR 0055 "
        "identity upsert: id, then seed_key — never name/slug)."
    )
    assert "MedicationCatalog.name.ilike" not in src
    assert "upsert_by_identity" in src, "seeder must go through the identity upsert"


def test_c9_seed_service_allergy_catalog_never_matches_by_name():
    """seed_service allergy seeding: upserts by identity (id/seed_key)."""
    from app.services.seed import allergies as seed_allergies

    src = inspect.getsource(seed_allergies)
    assert "func.lower(AllergyCatalog.name)" not in src
    assert "AllergyCatalog.name.ilike" not in src
    assert "upsert_by_identity" in src


def test_c9_import_service_medication_catalog_never_matches_by_name():
    """import_service._restore_medication_catalog: identity upsert (id →
    ``seed_key``), never a name comparison of any form (ADR 0055)."""
    from app.services.imports.service import ImportService

    src = inspect.getsource(ImportService._restore_medication_catalog)
    assert "_find_catalog_identity" in src
    assert "MedicationCatalog.name ==" not in src
    assert "MedicationCatalog.name.ilike" not in src
    assert "func.lower(MedicationCatalog.name)" not in src


def test_c9_import_service_allergy_catalog_never_matches_by_name():
    """import_service._restore_allergy_catalog: identity upsert, never a name
    comparison (ADR 0055)."""
    from app.services.imports.service import ImportService

    src = inspect.getsource(ImportService._restore_allergy_catalog)
    assert "_find_catalog_identity" in src
    assert "AllergyCatalog.name ==" not in src
    assert "AllergyCatalog.name.ilike" not in src
    assert "func.lower(AllergyCatalog.name)" not in src


def test_c9_catalog_search_uses_hybrid_pipeline():
    """catalog SEARCH was upgraded from plain substring ilike to a hybrid
    pipeline (trigram + FTS + Reciprocal Rank Fusion). Verify the hybrid
    markers are present in the unified search service and the old dedup-hostile
    raw ilike is gone. The per-domain allergy_service delegates entirely to
    ``search_allergies`` from the unified service (no legacy ilike fallback)."""
    from app.services.catalog import catalog_search_service
    from app.services.clinical import allergy_service

    # catalog_search_service now uses trigram + FTS, fused via RRF.
    cs_src = inspect.getsource(catalog_search_service)
    assert "websearch_to_tsquery" in cs_src, "hybrid FTS matcher missing"
    assert "pg_trgm" in cs_src or "similarity" in cs_src, "hybrid trigram matcher missing"
    assert "reciprocal" in cs_src.lower() or "rrf" in cs_src.lower(), "RRF fusion missing"
    # The old raw ilike(f"%{q}%") on Medication/Allergy catalogs is gone from
    # the unified search service (replaced by the hybrid matchers).
    assert 'MedicationCatalog.name.ilike(f"%{q}%")' not in cs_src
    assert 'AllergyCatalog.name.ilike(f"%{q}%")' not in cs_src

    # allergy_service.get_allergy_catalog delegates to the unified hybrid
    # search (no legacy ilike fallback of its own anymore).
    as_src = inspect.getsource(allergy_service)
    assert "search_allergies" in as_src, (
        "allergy_service should delegate catalog search to the unified hybrid pipeline"
    )
    assert "AllergyCatalog.name.ilike" not in as_src, (
        "allergy_service should not carry its own ilike fallback anymore"
    )
