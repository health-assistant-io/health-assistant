"""Regression tests for F11: DocumentReference.author must resolve.

Audit F11: ``DocumentReference.author`` emitted ``Practitioner/<owner_id>``
but ``owner_id`` is ``ForeignKey("users.id")`` — a User id, not a Doctor
id. External clients resolving the reference got 404.

The fix adds a nullable ``practitioner_id`` column to ``documents`` (FK
to ``doctors.id``, backfilled from owner→doctor at migration time) plus
a resolver ``_resolve_practitioner_id`` used by upload paths.

``DocumentModel.to_fhir_dict`` now emits ``Practitioner/<practitioner_id>``
when set, and omits ``author`` entirely otherwise (rather than emit a
wrong reference).

These tests verify:
- The model emits the correct reference (or omits) based on practitioner_id.
- The resolver resolves owner→practitioner via DoctorModel.user_id.
- The resolver returns None for owners without a Doctor row.
- The upload path calls the resolver and sets practitioner_id.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_model import DocumentModel
from app.services.document_service_db import _resolve_practitioner_id


# ---------------------------------------------------------------------------
# to_fhir_dict — author reference shape
# ---------------------------------------------------------------------------

def test_to_fhir_dict_omits_author_when_practitioner_id_unset():
    """When no Practitioner is linked (admin/manager uploads), `author` is
    omitted entirely — never emit Practitioner/<owner_id> (would 404)."""
    doc = DocumentModel(
        id=str(uuid.uuid4()),
        filename="test.pdf",
        file_path="/uploads/test.pdf",
        owner_id=str(uuid.uuid4()),  # User id — must NOT appear in author
        tenant_id=str(uuid.uuid4()),
        practitioner_id=None,
    )
    fhir = doc.to_fhir_dict()
    assert "author" not in fhir


def test_to_fhir_dict_emits_practitioner_reference_when_set():
    """When practitioner_id is set, author is Practitioner/<practitioner_id>."""
    pid = str(uuid.uuid4())
    doc = DocumentModel(
        id=str(uuid.uuid4()),
        filename="test.pdf",
        file_path="/uploads/test.pdf",
        owner_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        practitioner_id=pid,
    )
    fhir = doc.to_fhir_dict()
    assert fhir["author"][0]["reference"] == f"Practitioner/{pid}"


def test_to_fhir_dict_does_not_emit_owner_id_as_practitioner():
    """Regression: the bug was Practitioner/<owner_id>. Verify the owner_id
    value never appears in any Practitioner/ reference."""
    oid = str(uuid.uuid4())
    doc = DocumentModel(
        id=str(uuid.uuid4()),
        filename="test.pdf",
        file_path="/uploads/test.pdf",
        owner_id=oid,
        tenant_id=str(uuid.uuid4()),
        practitioner_id=None,
    )
    fhir = doc.to_fhir_dict()
    # No Practitioner reference at all (author omitted).
    if "author" in fhir:
        for author in fhir["author"]:
            assert oid not in author.get("reference", "")


# ---------------------------------------------------------------------------
# _resolve_practitioner_id — owner → Practitioner lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_practitioner_id_returns_doctor_id_when_user_linked():
    """When the owner has a DoctorModel row linked via user_id, the resolver
    returns the doctor's id."""
    db = AsyncMock(spec=AsyncSession)
    doctor_id = uuid.uuid4()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = doctor_id
    db.execute = AsyncMock(return_value=result_mock)

    resolved = await _resolve_practitioner_id(
        db, owner_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4())
    )
    assert resolved == doctor_id


@pytest.mark.asyncio
async def test_resolve_practitioner_id_returns_none_when_no_doctor():
    """When the owner has no DoctorModel row (admin/manager), the resolver
    returns None — author will be omitted from the FHIR resource."""
    db = AsyncMock(spec=AsyncSession)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)

    resolved = await _resolve_practitioner_id(
        db, owner_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4())
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_practitioner_id_invalid_uuid_returns_none():
    """Garbage input doesn't raise; returns None (no resolution)."""
    db = AsyncMock(spec=AsyncSession)
    resolved = await _resolve_practitioner_id(db, owner_id="not-a-uuid", tenant_id=str(uuid.uuid4()))
    assert resolved is None


# ---------------------------------------------------------------------------
# Upload path integration — practitioner_id populated from owner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_document_sets_practitioner_id_from_resolver(monkeypatch):
    """upload_document must call _resolve_practitioner_id and copy the result
    onto DocumentModel.practitioner_id before persisting."""
    from app.services import document_service_db

    db = AsyncMock(spec=AsyncSession)
    upload = MagicMock()
    upload.filename = "test.pdf"
    # EOF-correct read: content once, then empty (mirrors a real UploadFile).
    upload.read = AsyncMock(side_effect=[b"fake", b""])

    monkeypatch.setattr("os.makedirs", lambda *a, **kw: None)
    monkeypatch.setattr("os.path.isdir", lambda *a, **kw: True)
    async def _fake_write(path, content):
        return None
    monkeypatch.setattr(
        "app.services.document_service_db.write_file_if_not_exists",
        _fake_write,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.document_service_db.uuid4",
        lambda: uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
    )

    doctor_id = uuid.uuid4()
    with patch.object(
        document_service_db, "_resolve_practitioner_id", new=AsyncMock(return_value=doctor_id)
    ) as mock_resolve:
        # The gate must pass (mock it — full integration is exercised by
        # test_fhir_validation_gate_coverage.py).
        with patch.object(document_service_db, "assert_valid_fhir"):
            await document_service_db.upload_document(
                file=upload,
                patient_id=None,
                owner_id=str(uuid.uuid4()),
                tenant_id=str(uuid.uuid4()),
                db=db,
            )

        # Resolver was called with the owner.
        mock_resolve.assert_called_once()
        # db.add received a DocumentModel with practitioner_id set.
        added_doc = db.add.call_args[0][0]
        assert added_doc.practitioner_id == doctor_id


@pytest.mark.asyncio
async def test_upload_document_leaves_practitioner_id_unset_when_no_doctor(monkeypatch):
    """When the resolver returns None (admin/manager uploads), practitioner_id
    stays NULL — author will be omitted from the FHIR resource rather than
    emit a wrong reference."""
    from app.services import document_service_db

    db = AsyncMock(spec=AsyncSession)
    upload = MagicMock()
    upload.filename = "test.pdf"
    # EOF-correct read: content once, then empty (mirrors a real UploadFile).
    upload.read = AsyncMock(side_effect=[b"fake", b""])

    monkeypatch.setattr("os.makedirs", lambda *a, **kw: None)
    monkeypatch.setattr("os.path.isdir", lambda *a, **kw: True)
    async def _fake_write(path, content):
        return None
    monkeypatch.setattr(
        "app.services.document_service_db.write_file_if_not_exists",
        _fake_write,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.document_service_db.uuid4",
        lambda: uuid.UUID("00000000-0000-0000-0000-0000000000bb"),
    )

    with patch.object(
        document_service_db, "_resolve_practitioner_id", new=AsyncMock(return_value=None)
    ):
        with patch.object(document_service_db, "assert_valid_fhir"):
            await document_service_db.upload_document(
                file=upload,
                patient_id=None,
                owner_id=str(uuid.uuid4()),
                tenant_id=str(uuid.uuid4()),
                db=db,
            )

        added_doc = db.add.call_args[0][0]
        assert added_doc.practitioner_id is None


# ---------------------------------------------------------------------------
# to_dict — practitioner_id exposed for the frontend
# ---------------------------------------------------------------------------

def test_to_dict_exposes_practitioner_id_when_set():
    pid = str(uuid.uuid4())
    doc = DocumentModel(
        id=str(uuid.uuid4()),
        filename="x.pdf",
        file_path="/x.pdf",
        owner_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        practitioner_id=pid,
    )
    d = doc.to_dict()
    assert d["practitioner_id"] == pid


def test_to_dict_practitioner_id_none_when_unset():
    doc = DocumentModel(
        id=str(uuid.uuid4()),
        filename="x.pdf",
        file_path="/x.pdf",
        owner_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
    )
    d = doc.to_dict()
    assert d["practitioner_id"] is None
