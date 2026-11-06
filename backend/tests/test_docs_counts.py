"""Documented counts are facts, not prose — pin them to the code.

The 2026-10-21 audit found the README claiming 19 FHIR resources while the
facade registry exposed 20, and ``docs/API.md`` advertising a router/handler
count that had drifted. ``backend/scripts/check_docs_counts.py`` derives the
real numbers from the source tree (the same check the no-legacy gate runs);
this test keeps it in the suite.

Generated API types (per the master plan's quick-win follow-up) would retire
the hand-maintained tables; until then these assertions are the floor.
"""

from pathlib import Path

from scripts.check_docs_counts import check, fhir_resource_types, handler_count, router_modules

REPO = Path(__file__).resolve().parents[2]


def test_fhir_registry_has_the_registered_resources():
    types = fhir_resource_types(REPO)
    assert len(types) == len(set(types)), "the facade registry registers a duplicate"
    assert len(types) == 20, f"facade registry exposes {len(types)} resource types: {types}"


def test_documented_counts_match_the_code():
    assert check(REPO) == [], "\n".join(check(REPO))


def test_count_derivation_is_plausible():
    assert handler_count(REPO) > 300
    assert len(router_modules(REPO)) >= 40
