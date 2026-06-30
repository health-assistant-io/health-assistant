"""Regression test for Patient schema cardinality (I7).

Pre-fix contract: ``PatientBase.name`` / ``PatientCreate.address`` /
``PatientCreate.telecom`` / their ``PatientUpdate`` counterparts were all
typed as ``Optional[Dict[str, Any]]`` — single dicts. But FHIR R4
cardinality for these fields is ``0..*`` (lists). Posting canonical FHIR
JSON like ``{"name": [{"family": "Doe"}]}`` produced a Pydantic 422.

Post-fix contract pinned here: all four fields accept lists.
``PatientResponse`` inherits the list shape from ``PatientBase``.
"""
import pytest
from pydantic import ValidationError

from app.models.enums import Gender
from app.schemas.fhir.patient import (
    PatientBase,
    PatientCreate,
    PatientUpdate,
)


def test_i7_patient_base_accepts_list_of_names():
    """Canonical FHIR JSON ``"name": [{"family": "Doe"}]`` must be accepted."""
    p = PatientBase(
        name=[{"family": "Doe", "given": ["John"]}],
        gender=Gender.MALE,
    )
    assert p.name == [{"family": "Doe", "given": ["John"]}]


def test_i7_patient_base_accepts_multiple_names():
    """FHIR R4 HumanName is 0..*: a patient can have multiple names
    (e.g. official + maiden). Pre-fix, only one was allowed."""
    p = PatientBase(
        name=[
            {"family": "Smith", "given": ["Jane"], "use": "official"},
            {"family": "Jones", "given": ["Jane"], "use": "maiden"},
        ],
        gender=Gender.FEMALE,
    )
    assert len(p.name) == 2


def test_i7_patient_create_accepts_list_address_and_telecom():
    """PatientCreate.address and .telecom must accept lists (FHIR 0..*)."""
    p = PatientCreate(
        name=[{"family": "Doe"}],
        gender=Gender.MALE,
        tenant_id="00000000-0000-0000-0000-000000000001",
        address=[{"line": ["123 Main St"], "city": "Anytown"}],
        telecom=[{"system": "phone", "value": "555-1234"}],
    )
    assert isinstance(p.address, list)
    assert isinstance(p.telecom, list)


def test_i7_patient_update_accepts_list_fields():
    """PatientUpdate: list-typed fields must be Optional[List[...]] not Dict."""
    u = PatientUpdate(
        name=[{"family": "Roe"}],
        address=[{"line": ["456 Oak Ave"]}],
        telecom=[{"system": "email", "value": "x@y.com"}],
    )
    assert isinstance(u.name, list)
    assert isinstance(u.address, list)
    assert isinstance(u.telecom, list)


def test_i7_patient_base_rejects_single_dict_name():
    """Single-dict shape (the legacy non-canonical form) must be rejected.

    Per the audit guiding principle: never silently coerce FHIR. The ORM
    still tolerates legacy single-dict rows via ``_coerce_*`` helpers for
    read-side defensiveness, but the REST schema must be strict so callers
    learn they're posting non-canonical FHIR.
    """
    with pytest.raises(ValidationError):
        PatientBase(name={"family": "Doe"}, gender=Gender.MALE)


def test_i7_patient_base_field_annotation_is_list():
    """Static contract: the ``name`` field annotation must be List-based."""
    import typing

    # Pydantic stores field annotations on __annotations__.
    annotations = PatientBase.__annotations__
    name_origin = typing.get_origin(annotations["name"])
    # List[X] has origin list; dict-only would have origin dict.
    assert name_origin is list, (
        f"PatientBase.name must be List[...] (FHIR 0..*); got origin {name_origin}."
    )
