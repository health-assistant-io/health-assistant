"""Tests for DocumentReference projection from DocumentModel.

Covers:
- DocumentModel.to_fhir_dict() emits valid FHIR R4 DocumentReference
- status mapping (uploaded → current, archived → superseded, etc.)
- docStatus mapping (extracted → final, processing → preliminary, etc.)
- content[].attachment built from filename + file_path
- subject, author, context.encounter, context.period populated
- fhir_to_document_reference_orm() reverse conversion
- Round-trip preserves key fields
"""
import datetime as _dt
from uuid import uuid4

import pytest

from app.models.document_model import DocumentModel
from app.services.fhir_converter import fhir_to_document_reference_orm, validate_resource
from app.services.fhir_helpers import FhirSerializationError, parse_fhir_resource


def _make_doc(**overrides) -> DocumentModel:
    defaults = dict(
        id=str(uuid4()),
        filename="test.pdf",
        file_path="/uploads/test.pdf",
        owner_id=str(uuid4()),
        tenant_id=str(uuid4()),
        patient_id=None,
        examination_id=None,
        status="uploaded",
        progress=0,
        created_at=_dt.datetime(2024, 3, 15, 10, 0, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2024, 3, 15, 10, 0, tzinfo=_dt.timezone.utc),
    )
    defaults.update(overrides)
    return DocumentModel(**defaults)


# ---------------------------------------------------------------------------
# to_fhir_dict — basic projection
# ---------------------------------------------------------------------------

def test_doc_ref_minimal_to_fhir_dict():
    doc = _make_doc()
    fhir = doc.to_fhir_dict()
    assert fhir["resourceType"] == "DocumentReference"
    assert fhir["status"] == "current"
    assert "content" in fhir
    assert len(fhir["content"]) == 1
    assert fhir["content"][0]["attachment"]["title"] == "test.pdf"


def test_doc_ref_validates_against_fhir_resources():
    doc = _make_doc()
    fhir = doc.to_fhir_dict()
    parsed = parse_fhir_resource("DocumentReference", fhir)
    assert parsed.__resource_type__ == "DocumentReference"


def test_doc_ref_status_mapping():
    cases = [
        ("uploaded", "current"),
        ("processing", "current"),
        ("extracted", "current"),
        ("completed", "current"),
        ("failed", "current"),
        ("archived", "superseded"),
        ("deleted", "entered-in-error"),
    ]
    for app_status, fhir_status in cases:
        doc = _make_doc(status=app_status)
        fhir = doc.to_fhir_dict()
        assert fhir["status"] == fhir_status, f"app status {app_status} should map to {fhir_status}"


def test_doc_ref_doc_status_mapping():
    cases = [
        ("uploaded", "preliminary"),
        ("processing", "preliminary"),
        ("extracted", "final"),
        ("completed", "final"),
        ("failed", "entered-in-error"),
    ]
    for app_status, doc_status in cases:
        doc = _make_doc(status=app_status)
        fhir = doc.to_fhir_dict()
        assert fhir["docStatus"] == doc_status, f"app status {app_status} → docStatus {doc_status}"


def test_doc_ref_attachment_url_uses_urn_scheme():
    doc = _make_doc()
    fhir = doc.to_fhir_dict()
    url = fhir["content"][0]["attachment"]["url"]
    assert url.startswith("urn:ha-document:")


def test_doc_ref_subject_reference():
    pid = str(uuid4())
    doc = _make_doc(patient_id=pid)
    fhir = doc.to_fhir_dict()
    assert fhir["subject"]["reference"] == f"Patient/{pid}"


def test_doc_ref_subject_absent_when_no_patient():
    doc = _make_doc(patient_id=None)
    fhir = doc.to_fhir_dict()
    assert "subject" not in fhir


def test_doc_ref_author_omitted_when_practitioner_id_unset():
    """F11: DocumentReference.author must NOT be emitted as Practitioner/<owner_id>
    (owner_id is a User FK, not a Doctor FK — would 404 on resolution). When
    practitioner_id is unset, author is omitted entirely rather than wrong."""
    oid = str(uuid4())
    doc = _make_doc(owner_id=oid)
    fhir = doc.to_fhir_dict()
    assert "author" not in fhir


def test_doc_ref_author_from_resolved_practitioner_id():
    """F11: when practitioner_id is set (resolved owner→Practitioner at upload
    time), DocumentReference.author emits Practitioner/<practitioner_id> —
    a reference external clients can actually resolve."""
    pid = str(uuid4())
    doc = _make_doc(owner_id=str(uuid4()), practitioner_id=pid)
    fhir = doc.to_fhir_dict()
    assert fhir["author"][0]["reference"] == f"Practitioner/{pid}"


def test_doc_ref_context_encounter():
    eid = str(uuid4())
    doc = _make_doc(examination_id=eid)
    fhir = doc.to_fhir_dict()
    assert fhir["context"]["encounter"][0]["reference"] == f"Encounter/{eid}"


def test_doc_ref_context_period_from_created_at():
    doc = _make_doc(created_at=_dt.datetime(2024, 6, 1, 12, 30, tzinfo=_dt.timezone.utc))
    fhir = doc.to_fhir_dict()
    assert fhir["context"]["period"]["start"].startswith("2024-06-01")


# ---------------------------------------------------------------------------
# Reverse: fhir_to_document_reference_orm
# ---------------------------------------------------------------------------

def _canonical_doc_ref(**overrides) -> dict:
    base = {
        "resourceType": "DocumentReference",
        "id": str(uuid4()),
        "status": "current",
        "docStatus": "final",
        "subject": {"reference": "Patient/p1"},
        "author": [{"reference": "Practitioner/u1"}],
        "content": [
            {
                "attachment": {
                    "title": "report.pdf",
                    "url": "urn:ha-document:abc",
                    "contentType": "application/pdf",
                }
            }
        ],
        "context": {
            "encounter": [{"reference": "Encounter/e1"}],
            "period": {"start": "2024-03-15"},
        },
    }
    base.update(overrides)
    return base


def test_fhir_to_doc_ref_orm_basic():
    fhir = _canonical_doc_ref()
    orm = fhir_to_document_reference_orm(fhir)

    assert orm["filename"] == "report.pdf"
    assert orm["file_path"] == "urn:ha-document:abc"
    # F11: author reference is preserved as practitioner_id (not owner_id,
    # which is an internal User FK set by the upload path).
    assert orm["practitioner_id"] == "u1"
    assert "owner_id" not in orm
    assert orm["patient_id"] == "p1"
    assert orm["examination_id"] == "e1"
    assert orm["status"] == "uploaded"  # current → uploaded


def test_fhir_to_doc_ref_orm_superseded_status():
    fhir = _canonical_doc_ref(status="superseded")
    orm = fhir_to_document_reference_orm(fhir)
    assert orm["status"] == "archived"


def test_fhir_to_doc_ref_orm_entered_in_error_status():
    fhir = _canonical_doc_ref(status="entered-in-error")
    orm = fhir_to_document_reference_orm(fhir)
    assert orm["status"] == "failed"


def test_fhir_to_doc_ref_orm_drops_doc_status():
    fhir = _canonical_doc_ref()
    orm = fhir_to_document_reference_orm(fhir)
    assert "docStatus" not in orm
    assert "doc_status" not in orm


def test_fhir_to_doc_ref_orm_no_content_uses_defaults():
    fhir = _canonical_doc_ref(content=[])
    orm = fhir_to_document_reference_orm(fhir)
    assert orm["filename"] == "untitled"
    assert orm["file_path"] == ""


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_round_trip_orm_to_fhir_to_orm():
    """F11: round-trip ORM → FHIR → ORM preserves practitioner_id (the FHIR
    `author` reference target). owner_id is an internal-only User FK and is
    not preserved by the FHIR round-trip (it's set separately by the upload
    path via owner→practitioner lookup)."""
    pid = str(uuid4())
    pid2 = str(uuid4())
    eid = str(uuid4())
    doc = _make_doc(
        filename="my.pdf",
        patient_id=pid,
        owner_id=str(uuid4()),  # owner_id doesn't survive FHIR round-trip
        practitioner_id=pid2,   # this is the FHIR-canonical Practitioner ref
        examination_id=eid,
        status="extracted",
    )
    fhir = doc.to_fhir_dict()
    orm = fhir_to_document_reference_orm(fhir)

    assert orm["filename"] == "my.pdf"
    assert orm["patient_id"] == pid
    assert orm["practitioner_id"] == pid2  # preserved via FHIR author
    # owner_id is not in the ORM dict (it's not a FHIR concept — set by
    # the upload path from the user, not from the FHIR resource).
    assert "owner_id" not in orm
    assert orm["examination_id"] == eid


def test_round_trip_fhir_to_orm_to_fhir():
    """F11: FHIR → ORM → FHIR preserves the author reference. The owner_id
    (User FK) is set to a synthetic value for the DocumentModel constructor
    since it's required by the column but not part of the FHIR resource."""
    fhir_in = _canonical_doc_ref()
    orm = fhir_to_document_reference_orm(fhir_in)

    doc = DocumentModel(
        id=orm.get("id"),
        filename=orm["filename"],
        file_path=orm["file_path"],
        owner_id=str(uuid4()),  # owner_id is not part of FHIR — synthetic here
        tenant_id=str(uuid4()),
        practitioner_id=orm.get("practitioner_id"),  # preserved from FHIR author
        patient_id=orm.get("patient_id"),
        examination_id=orm.get("examination_id"),
        status=orm["status"],
    )
    fhir_out = doc.to_fhir_dict()

    assert fhir_out["subject"]["reference"] == fhir_in["subject"]["reference"]
    assert fhir_out["author"][0]["reference"] == fhir_in["author"][0]["reference"]
    assert fhir_out["content"][0]["attachment"]["title"] == fhir_in["content"][0]["attachment"]["title"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_canonical_doc_ref_validates():
    fhir = _canonical_doc_ref()
    ok, errs = validate_resource(fhir)
    assert ok, f"Expected valid DocumentReference: {errs}"


def test_invalid_doc_ref_rejected():
    """DocumentReference requires status + content. Drop content to fail."""
    from app.services.fhir_helpers import build_fhir_resource

    with pytest.raises(FhirSerializationError):
        build_fhir_resource(
            "DocumentReference",
            {
                "resourceType": "DocumentReference",
                "id": "x",
                "status": "current",
                # missing content (1..* required)
            },
        )
