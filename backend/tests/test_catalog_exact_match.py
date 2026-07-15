"""Regression tests for MedicationCatalog / AllergyCatalog uniqueness matching (C9).

Pre-fix contract: catalog "uniqueness" checks at three sites used
``Catalog.name.ilike(name)``. ILIKE without wildcards does exact
case-insensitive matching on its own, BUT treats ``_`` and ``%`` in
``name`` as wildcards. So a catalog name like ``"Vitamin_B12"`` would
match a row literally named ``"Vitamin-B12"`` (underscore matches any
single char) or ``"Vitamin B12"`` — silently deduping distinct entries.
``"HCTZ_50"`` would match ``"HCTZ-50"``. The intent (case-insensitive
exact match) was right; the implementation was wrong.

Post-fix contract pinned here: all three catalog uniqueness sites use
``func.lower(Catalog.name) == func.lower(name)`` instead of ``ilike``.
This treats ``_`` and ``%`` as literals and produces a strict
case-insensitive exact match.

The fix is applied at three sites (MedicationCatalog + AllergyCatalog):
- ``app/ai/pipeline/ontology.py`` — ``process_unknown_medications``
- ``app/services/seed_service.py`` — ``_process_medications`` and ``_process_allergies``
- ``app/services/import_service.py`` — ``_restore_medication_catalog`` and ``_restore_allergy_catalog``

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


def test_c9_seed_service_medication_catalog_uses_exact_match():
    """seed_service medication seeding: no ilike(item['name']), uses func.lower ==."""
    from app.services import seed_service

    # The medication seeding method lives inline in seed_medications; check
    # the whole module source for the catalog-uniqueness pattern.
    src = inspect.getsource(seed_service)
    assert "func.lower(MedicationCatalog.name) == func.lower(item[\"name\"])" in src, (
        "seed_service medication seeding must use case-insensitive exact match."
    )
    # The ilike form must NOT be used as a uniqueness check.
    assert "MedicationCatalog.name.ilike(item[\"name\"])" not in src


def test_c9_seed_service_allergy_catalog_uses_exact_match():
    """seed_service allergy seeding: no ilike(item['name']), uses func.lower ==."""
    from app.services import seed_service

    src = inspect.getsource(seed_service)
    assert "func.lower(AllergyCatalog.name) == func.lower(item[\"name\"])" in src
    assert "AllergyCatalog.name.ilike(item[\"name\"])" not in src


def test_c9_import_service_medication_catalog_uses_exact_match():
    """import_service._restore_medication_catalog: no ilike(name), uses func.lower ==."""
    from app.services import import_service

    src = inspect.getsource(import_service.ImportService._restore_medication_catalog)
    assert "func.lower(MedicationCatalog.name) == func.lower(name)" in src
    assert "MedicationCatalog.name.ilike(name)" not in src


def test_c9_import_service_allergy_catalog_uses_exact_match():
    """import_service._restore_allergy_catalog: no ilike(name), uses func.lower ==."""
    from app.services import import_service

    src = inspect.getsource(import_service.ImportService._restore_allergy_catalog)
    assert "func.lower(AllergyCatalog.name) == func.lower(name)" in src
    assert "AllergyCatalog.name.ilike(name)" not in src


def test_c9_catalog_search_uses_hybrid_pipeline():
    """catalog SEARCH was upgraded from plain substring ilike to a hybrid
    pipeline (trigram + FTS + Reciprocal Rank Fusion). Verify the hybrid
    markers are present and the old dedup-hostile raw ilike on the unified
    search service is gone. (The per-domain allergy_service keeps an ilike
    fallback for its non-hybrid read path.)"""
    from app.services import catalog_search_service, allergy_service

    # catalog_search_service now uses trigram + FTS, fused via RRF.
    cs_src = inspect.getsource(catalog_search_service)
    assert "websearch_to_tsquery" in cs_src, "hybrid FTS matcher missing"
    assert "pg_trgm" in cs_src or "similarity" in cs_src, (
        "hybrid trigram matcher missing"
    )
    assert "reciprocal" in cs_src.lower() or "rrf" in cs_src.lower(), (
        "RRF fusion missing"
    )
    # The old raw ilike(f"%{q}%") on Medication/Allergy catalogs is gone from
    # the unified search service (replaced by the hybrid matchers).
    assert 'MedicationCatalog.name.ilike(f"%{q}%")' not in cs_src
    assert 'AllergyCatalog.name.ilike(f"%{q}%")' not in cs_src

    # allergy_service still uses ilike as a fallback on its non-hybrid path.
    as_src = inspect.getsource(allergy_service)
    assert 'AllergyCatalog.name.ilike(f"%{search}%")' in as_src
