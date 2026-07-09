"""Registry contract tests — Phase 0.

Validates that importing :mod:`app.catalogs` populates the registry with the
expected catalogs and that every descriptor is well-formed. No DB access.
"""

import pytest

from app.catalogs import CatalogRegistry
from app.models.enums import EdgeEndpointType

EXPECTED_TYPES = {"biomarker", "medication", "allergy", "anatomy", "concept", "vaccine"}


def test_registry_has_all_expected_types():
    assert set(CatalogRegistry.types()) == EXPECTED_TYPES


def test_registry_types_count():
    assert len(CatalogRegistry.types()) == 6


@pytest.mark.parametrize("type_name", sorted(EXPECTED_TYPES))
def test_descriptor_well_formed(type_name):
    desc = CatalogRegistry.get(type_name)
    assert desc.type == type_name
    assert desc.model is not None
    assert desc.service is not None
    assert isinstance(desc.search_columns, tuple) and len(desc.search_columns) > 0
    assert isinstance(desc.edge_endpoint_type, EdgeEndpointType)
    assert desc.rbac is not None
    assert desc.ui is not None
    assert desc.ui.label_key
    assert desc.ui.icon
    assert desc.ui.color
    assert desc.ui.admin_route.startswith("/")


def test_get_unknown_type_raises_keyerror():
    with pytest.raises(KeyError):
        CatalogRegistry.get("nonexistent")


def test_is_registered():
    assert CatalogRegistry.is_registered("biomarker")
    assert not CatalogRegistry.is_registered("nonexistent")


def test_each_descriptor_has_matching_edge_endpoint_type():
    expected = {
        "biomarker": EdgeEndpointType.BIOMARKER,
        "medication": EdgeEndpointType.MEDICATION,
        "allergy": EdgeEndpointType.ALLERGY,
        "anatomy": EdgeEndpointType.ANATOMY,
        "concept": EdgeEndpointType.CONCEPT,
    }
    for type_name, etype in expected.items():
        assert CatalogRegistry.get(type_name).edge_endpoint_type == etype


def test_resolvers_registered_where_available():
    assert CatalogRegistry.get("biomarker").resolver is not None
    assert CatalogRegistry.get("anatomy").resolver is not None
    assert CatalogRegistry.get("concept").resolver is not None
    assert CatalogRegistry.get("medication").resolver is not None
    assert CatalogRegistry.get("allergy").resolver is not None


def test_concept_links_declared_for_all_catalogs():
    """Phase 2: every catalog now carries a class_concept_id taxonomy hook."""
    for type_name in EXPECTED_TYPES:
        if type_name == "concept":
            continue  # concepts ARE the taxonomy; no self-link
        assert CatalogRegistry.get(type_name).has_concept_link, type_name
