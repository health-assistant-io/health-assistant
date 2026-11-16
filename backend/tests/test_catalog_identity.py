"""Unit tests for the catalog identity helper (ADR 0055)."""

import uuid

import pytest

from app.core.catalog_identity import (
    NULL_TENANT_SENTINEL,
    SEED_KEY_NAMESPACE,
    seed_key_uuid,
    validate_seed_key,
)


def test_sentinel_is_the_shared_all_zero_uuid():
    """The same literal 0001_baseline uses for the per-tenant slug/MRN
    uniques — one sentinel everywhere (models, migrations, seeders)."""
    assert NULL_TENANT_SENTINEL == "00000000-0000-0000-0000-000000000000"
    assert uuid.UUID(NULL_TENANT_SENTINEL).int == 0


def test_seed_key_namespace_is_frozen():
    """Changing the namespace would silently re-identity every curated row."""
    assert uuid.UUID("0a0f2617-5d80-5bbd-baad-2d737aff458b") == SEED_KEY_NAMESPACE
    assert SEED_KEY_NAMESPACE.version == 5


def test_validate_seed_key_splits_the_qualified_form():
    assert validate_seed_key("medication:amoxicillin-500mg") == (
        "medication",
        "amoxicillin-500mg",
    )
    assert validate_seed_key("concept:atc-c/alpha") == ("concept", "atc-c/alpha")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no-colon",
        ":no-type",
        "medication:",
        "Medication:lowercase-type",
        "medication:Uppercase-Key",
        "medication: spaces",
        "med:kebab\n",
        "medication:" + "x" * 121,
        42,
        None,
    ],
)
def test_validate_seed_key_rejects_bad_shapes(bad):
    with pytest.raises(ValueError):
        validate_seed_key(bad)


def test_seed_key_uuid_is_deterministic_and_type_scoped():
    first = seed_key_uuid("medication", "aspirin")
    second = seed_key_uuid("medication", "aspirin")
    assert first == second
    assert first.version == 5
    assert first == uuid.uuid5(SEED_KEY_NAMESPACE, "medication:aspirin")
    # the type participates in the identity: the same key in another catalog
    # derives a different UUID
    assert seed_key_uuid("allergy", "aspirin") != first


def test_seed_key_uuid_rejects_bad_parts():
    with pytest.raises(ValueError):
        seed_key_uuid("Medication", "aspirin")
    with pytest.raises(ValueError):
        seed_key_uuid("medication", "Not Kebab")


def test_seed_key_uuid_matches_the_qualified_validation():
    """The pair the validator splits is the pair the identity derives from."""
    for qualified in ("medication:amoxicillin-500mg", "vaccine:mmr", "concept:atc-c"):
        catalog_type, seed_key = validate_seed_key(qualified)
        assert seed_key_uuid(catalog_type, seed_key) == uuid.uuid5(SEED_KEY_NAMESPACE, qualified)
