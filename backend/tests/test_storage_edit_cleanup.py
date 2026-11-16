"""Edit-version cleanup: a failed row write leaves no orphan object."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.documents import document_service
from app.storage.bootstrap import ENTRYPOINT_SERVER, StorageConfig
from app.storage.documents import DocumentStorage
from app.storage.object_storage import build_store


@pytest.mark.asyncio
async def test_edit_failure_deletes_the_new_object(tmp_path, monkeypatch) -> None:
    store = build_store(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )
    monkeypatch.setattr("app.storage.documents.current_store", lambda settings=None: store)
    # The source image for the edit (legacy local path).
    source = tmp_path / "original.png"
    source.write_bytes(b"source png")
    original = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_id=uuid4(),
        patient_id=None,
        examination_id=None,
        filename="original.png",
        file_path=str(source),
        object_key=None,
        include_in_extraction=False,
        practitioner_id=None,
    )

    def fake_edit(_src, dest, **_kwargs):
        with open(dest, "wb") as handle:
            handle.write(b"edited png")

    monkeypatch.setattr(document_service, "edit_image", fake_edit)
    monkeypatch.setattr(
        document_service, "get_document", AsyncMock(return_value=original)
    )
    monkeypatch.setattr(
        document_service,
        "assert_valid_fhir",
        lambda _doc: (_ for _ in ()).throw(ValueError("invalid FHIR")),
    )

    with pytest.raises(ValueError, match="invalid FHIR"):
        await document_service.edit_document_service(str(original.id), {}, AsyncMock())

    assert list(store.list("tenants")) == []
    assert DocumentStorage(store).read(original) == b"source png"
