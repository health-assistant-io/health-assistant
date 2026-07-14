"""Tests for the typed ``metadata_schema`` Pydantic descriptor.

Pins the fail-loud contract: a malformed ``ClinicalEventType.metadata_schema``
raises a precise Pydantic error instead of silently rendering nothing in the
dynamic form. Covers valid shapes per field type plus the cross-field
constraints (CATALOG_SELECT needs catalogs; concept_kind needs CONCEPT-only).
"""
import pytest
from pydantic import ValidationError

from app.models.enums import (
    CatalogRelationType,
    CatalogType,
    ConceptKind,
    MetadataFieldType,
)
from app.schemas.clinical_event import MetadataField, MetadataSchema


# ---------------------------------------------------------------------------
# Valid shapes
# ---------------------------------------------------------------------------


def _field(**kw) -> MetadataField:
    base = {"name": "f", "label": "F", "type": MetadataFieldType.TEXT}
    base.update(kw)
    return MetadataField(**base)


def test_text_field_minimal():
    f = _field()
    assert f.type is MetadataFieldType.TEXT
    assert f.required is False
    assert f.catalogs is None


def test_number_field_with_bounds():
    f = _field(
        type=MetadataFieldType.NUMBER,
        name="intensity",
        label="Intensity",
        min=1,
        max=10,
        required=True,
    )
    assert f.min == 1
    assert f.max == 10
    assert f.required is True


def test_date_and_boolean_fields():
    assert _field(type=MetadataFieldType.DATE).type is MetadataFieldType.DATE
    assert _field(type=MetadataFieldType.BOOLEAN).type is MetadataFieldType.BOOLEAN


def test_catalog_select_anatomy_single():
    f = _field(
        type=MetadataFieldType.CATALOG_SELECT,
        name="body_location",
        catalogs=[CatalogType.ANATOMY],
        multi=False,
    )
    assert f.catalogs == [CatalogType.ANATOMY]
    assert f.multi is False


def test_catalog_select_multi_catalog():
    f = _field(
        type=MetadataFieldType.CATALOG_SELECT,
        name="related",
        catalogs=[CatalogType.MEDICATION, CatalogType.ALLERGY],
        multi=True,
    )
    assert f.catalogs == [CatalogType.MEDICATION, CatalogType.ALLERGY]


def test_catalog_select_concept_with_kind():
    f = _field(
        type=MetadataFieldType.CATALOG_SELECT,
        name="category",
        catalogs=[CatalogType.CONCEPT],
        concept_kind=ConceptKind.EXAMINATION_CATEGORY,
    )
    assert f.concept_kind is ConceptKind.EXAMINATION_CATEGORY


def test_catalog_select_with_relation():
    f = _field(
        type=MetadataFieldType.CATALOG_SELECT,
        name="site",
        catalogs=[CatalogType.ANATOMY],
        relation=CatalogRelationType.PRIMARY_SITE,
    )
    assert f.relation is CatalogRelationType.PRIMARY_SITE


def test_metadata_schema_valid():
    schema = MetadataSchema(
        fields=[
            _field(name="a", label="A"),
            _field(
                name="b",
                label="B",
                type=MetadataFieldType.CATALOG_SELECT,
                catalogs=[CatalogType.ANATOMY],
            ),
        ]
    )
    assert len(schema.fields) == 2


def test_metadata_field_accepts_string_enum_values():
    """Wire-format strings (from JSONB / API JSON) coerce into the enums."""
    f = MetadataField(
        name="x",
        label="X",
        type="catalog-select",
        catalogs=["anatomy"],
    )
    assert f.type is MetadataFieldType.CATALOG_SELECT
    assert f.catalogs == [CatalogType.ANATOMY]


def test_placeholder_round_trips():
    """A text field may carry a descriptive placeholder; it round-trips
    through model_dump so the frontend can render it greyed."""
    f = _field(type=MetadataFieldType.TEXT, name="trigger", placeholder="e.g. stress")
    assert f.placeholder == "e.g. stress"
    dumped = MetadataSchema(fields=[f]).model_dump()
    assert dumped["fields"][0]["placeholder"] == "e.g. stress"


# ---------------------------------------------------------------------------
# CATALOG_SELECT constraints (fail-loud)
# ---------------------------------------------------------------------------


def test_catalog_select_without_catalogs_raises():
    with pytest.raises(ValidationError) as ei:
        _field(type=MetadataFieldType.CATALOG_SELECT, catalogs=None)
    assert "non-empty 'catalogs'" in str(ei.value)


def test_catalog_select_with_empty_catalogs_raises():
    with pytest.raises(ValidationError) as ei:
        _field(type=MetadataFieldType.CATALOG_SELECT, catalogs=[])
    assert "non-empty 'catalogs'" in str(ei.value)


def test_concept_kind_without_concept_only_catalogs_raises():
    with pytest.raises(ValidationError) as ei:
        _field(
            type=MetadataFieldType.CATALOG_SELECT,
            catalogs=[CatalogType.ANATOMY],
            concept_kind=ConceptKind.EVENT_CATEGORY,
        )
    assert "concept_kind" in str(ei.value)


def test_concept_kind_with_multi_catalog_raises():
    """concept_kind is meaningless if catalogs includes non-concept types."""
    with pytest.raises(ValidationError):
        _field(
            type=MetadataFieldType.CATALOG_SELECT,
            catalogs=[CatalogType.CONCEPT, CatalogType.BIOMARKER],
            concept_kind=ConceptKind.DISEASE,
        )


def test_concept_kind_with_exact_concept_list_ok():
    f = _field(
        type=MetadataFieldType.CATALOG_SELECT,
        catalogs=[CatalogType.CONCEPT],
        concept_kind=ConceptKind.SPECIALTY,
    )
    assert f.concept_kind is ConceptKind.SPECIALTY


# ---------------------------------------------------------------------------
# MetadataSchema-level constraints
# ---------------------------------------------------------------------------


def test_empty_fields_raises():
    with pytest.raises(ValidationError) as ei:
        MetadataSchema(fields=[])
    assert "at least one field" in str(ei.value)


def test_duplicate_field_names_raise():
    with pytest.raises(ValidationError) as ei:
        MetadataSchema(fields=[_field(name="dup", label="A"), _field(name="dup", label="B")])
    assert "duplicate" in str(ei.value)


def test_unknown_field_type_raises():
    with pytest.raises(ValidationError):
        MetadataField(name="x", label="X", type="not-a-real-type")


# ---------------------------------------------------------------------------
# Round-trip: model_dump is JSONB-compatible (the seed/loader stores dicts)
# ---------------------------------------------------------------------------


def test_model_dump_is_plain_dict_with_string_enum_values():
    """The stored JSONB shape must use the lowercase wire strings so the
    frontend literal union matches verbatim."""
    schema = MetadataSchema(
        fields=[
            _field(
                name="site",
                label="Site",
                type=MetadataFieldType.CATALOG_SELECT,
                catalogs=[CatalogType.ANATOMY],
                relation=CatalogRelationType.PRIMARY_SITE,
            ),
        ]
    )
    dumped = schema.model_dump()
    field = dumped["fields"][0]
    assert field["type"] == "catalog-select"
    assert field["catalogs"] == ["anatomy"]
    assert field["relation"] == "primary_site"
    # No stray Pydantic artifacts — it's a plain JSON-serializable dict.
    import json

    json.dumps(dumped)


def test_roundtrip_validate_after_dump():
    """A dumped schema re-validates cleanly (storage round-trip)."""
    original = MetadataSchema(
        fields=[
            _field(
                name="cat",
                label="Cat",
                type=MetadataFieldType.CATALOG_SELECT,
                catalogs=[CatalogType.CONCEPT],
                concept_kind=ConceptKind.EVENT_CATEGORY,
            )
        ]
    )
    dumped = original.model_dump()
    re_validated = MetadataSchema.model_validate(dumped)
    assert re_validated.fields[0].concept_kind is ConceptKind.EVENT_CATEGORY
