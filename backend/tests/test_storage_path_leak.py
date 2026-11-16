"""No durable filesystem path reaches a document API response."""

from __future__ import annotations

import json
from uuid import uuid4

from app.models.document_model import DocumentModel
from app.schemas.document import DocumentResponse


def _model() -> DocumentModel:
    return DocumentModel(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_id=uuid4(),
        filename="lab report.pdf",
        file_path="/var/healthassistant/uploads/tenant/secret.pdf",
        object_key="tenants/t/documents/secret.pdf",
        status="uploaded",
        progress=0,
    )


def test_model_to_dict_never_exposes_the_durable_path() -> None:
    payload = _model().to_dict()
    assert payload["file_path"] == ""
    serialized = json.dumps(payload)
    assert "/var/healthassistant" not in serialized
    assert "uploads" not in serialized


def test_document_response_defaults_to_no_path() -> None:
    response = DocumentResponse(
        id=uuid4(),
        owner_id=uuid4(),
        status="uploaded",
        progress=0,
        include_in_extraction=False,
        filename="x.pdf",
    )
    assert response.file_path == ""


def test_model_to_fhir_has_no_filesystem_location() -> None:
    model = _model()
    fhir = model.to_fhir_dict()
    attachment = fhir["content"][0]["attachment"]
    assert attachment["url"] == f"urn:ha-document:{model.id}"
    assert "/var/" not in json.dumps(fhir)


def test_upload_error_message_has_no_path() -> None:
    from fastapi import HTTPException

    from app.services.documents.document_service import _validate_upload_extension

    try:
        _validate_upload_extension("bad.html")
    except HTTPException as exc:
        assert "/" not in str(exc.detail)
    else:  # pragma: no cover
        raise AssertionError("active-content extension must be rejected")
