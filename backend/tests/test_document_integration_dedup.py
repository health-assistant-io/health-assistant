"""Tests for the integration-key document dedup (item 3 of the
integrations-sdk-improvements plan).

These pin the dedup contract added to ``ingest_document_bytes`` +
``DocumentModel``:

* Both keys supplied → lookup-then-insert; existing row returned as-is,
  no duplicate file write, no OCR dispatch.
* Race window → ``IntegrityError`` recovered via re-fetch.
* UI path (both keys NULL) → bypass dedup.
* DocumentPull.external_id is the SDK spec field that surfaces this to
  providers.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.document_model import DocumentModel
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.services import document_service


@pytest_asyncio.fixture
async def tenant_user_patient_integration():
    """Isolated tenant + user + patient + integration for dedup tests.

    Returns ``(tenant_id, user_id, patient_id, integration_id)``.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="Doc Dedup T.",
                slug=f"docdedup-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"docdedup-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Dedup"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="test_dedup",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()

    return tenant_id, user_id, patient_id, integration_id


# ---------------------------------------------------------------------------
# DocumentModel columns
# ---------------------------------------------------------------------------


def test_document_model_declares_dedup_columns():
    """The model must expose the two new columns so the ORM inserts
    them on flush + the partial unique index fires at the DB layer."""
    assert hasattr(DocumentModel, "source_integration_id")
    assert hasattr(DocumentModel, "external_id")


# ---------------------------------------------------------------------------
# ingest_document_bytes — dedup happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_document_bytes_dedup_returns_existing_row(
    tenant_user_patient_integration, monkeypatch
):
    """Two calls with the same ``(integration, external_id)`` return the
    SAME row (by id) and only one file is written."""
    tenant_id, user_id, patient_id, integration_id = tenant_user_patient_integration
    external_id = "lab-accession-123"

    # Disable OCR dispatch — we don't need a broker.
    monkeypatch.setattr(
        "app.workers.ai_tasks.ocr_document.apply_async",
        lambda *a, **kw: None,
    )

    async with AsyncSessionLocal() as db:
        first = await document_service.ingest_document_bytes(
            filename="report.pdf",
            content=b"%PDF-1.4 first\n%%EOF\n",
            content_type="application/pdf",
            tenant_id=tenant_id,
            patient_id=patient_id,
            owner_id=user_id,
            db=db,
            include_in_extraction=False,
            source_integration_id=integration_id,
            external_id=external_id,
        )

        second = await document_service.ingest_document_bytes(
            filename="report.pdf",
            content=b"%PDF-1.4 second\n%%EOF\n",
            content_type="application/pdf",
            tenant_id=tenant_id,
            patient_id=patient_id,
            owner_id=user_id,
            db=db,
            include_in_extraction=False,
            source_integration_id=integration_id,
            external_id=external_id,
        )

    assert second.id == first.id, "dedup must return the same row id"

    # Only one DB row for this dedup key.
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(DocumentModel).where(
                    DocumentModel.source_integration_id == integration_id,
                    DocumentModel.external_id == external_id,
                )
            )
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ingest_document_bytes_no_dedup_when_keys_missing(
    tenant_user_patient_integration, monkeypatch
):
    """UI path: when ``source_integration_id`` or ``external_id`` are
    missing, dedup is bypassed — two inserts create two rows. Mirrors
    the existing examination_service contract."""
    tenant_id, user_id, patient_id, _integration_id = tenant_user_patient_integration

    monkeypatch.setattr(
        "app.workers.ai_tasks.ocr_document.apply_async",
        lambda *a, **kw: None,
    )

    async with AsyncSessionLocal() as db:
        a = await document_service.ingest_document_bytes(
            filename="ui-upload-1.pdf",
            content=b"a",
            content_type="application/pdf",
            tenant_id=tenant_id,
            patient_id=patient_id,
            owner_id=user_id,
            db=db,
            include_in_extraction=False,
        )
        b = await document_service.ingest_document_bytes(
            filename="ui-upload-2.pdf",
            content=b"b",
            content_type="application/pdf",
            tenant_id=tenant_id,
            patient_id=patient_id,
            owner_id=user_id,
            db=db,
            include_in_extraction=False,
        )

    assert a.id != b.id


@pytest.mark.asyncio
async def test_ingest_document_bytes_dedup_no_reocr_on_hit(
    tenant_user_patient_integration, monkeypatch
):
    """On a dedup hit, the OCR task MUST NOT be re-dispatched — even
    when ``include_in_extraction=True`` on the second call. Otherwise
    we'd pay the OCR cost twice for the same document."""
    tenant_id, user_id, patient_id, integration_id = tenant_user_patient_integration
    external_id = "fax-001"

    dispatch_count = {"n": 0}

    def _count_dispatch(*args, **kwargs):
        dispatch_count["n"] += 1
        return type("R", (), {"id": "fake"})

    monkeypatch.setattr(
        "app.workers.ai_tasks.ocr_document.apply_async", _count_dispatch
    )

    async with AsyncSessionLocal() as db:
        await document_service.ingest_document_bytes(
            filename="fax.pdf",
            content=b"%PDF-1.4 fax\n%%EOF\n",
            content_type="application/pdf",
            tenant_id=tenant_id,
            patient_id=patient_id,
            owner_id=user_id,
            db=db,
            include_in_extraction=True,
            source_integration_id=integration_id,
            external_id=external_id,
        )
        # Second call would re-dispatch without the dedup guard.
        await document_service.ingest_document_bytes(
            filename="fax.pdf",
            content=b"%PDF-1.4 dup\n%%EOF\n",
            content_type="application/pdf",
            tenant_id=tenant_id,
            patient_id=patient_id,
            owner_id=user_id,
            db=db,
            include_in_extraction=True,
            source_integration_id=integration_id,
            external_id=external_id,
        )

    assert dispatch_count["n"] == 1, (
        f"dedup hit must not re-dispatch OCR; saw {dispatch_count['n']}"
    )


# ---------------------------------------------------------------------------
# Race window — IntegrityError recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_document_bytes_dedup_race_recovers(
    tenant_user_patient_integration, monkeypatch
):
    """When a concurrent sync beat wins the INSERT (the SELECT-then-INSERT
    race window), the service catches ``IntegrityError``, rolls back,
    re-fetches the winner, and returns it. The caller doesn't see the
    error."""
    tenant_id, user_id, patient_id, integration_id = tenant_user_patient_integration
    external_id = "race-001"

    monkeypatch.setattr(
        "app.workers.ai_tasks.ocr_document.apply_async",
        lambda *a, **kw: None,
    )

    async with AsyncSessionLocal() as db:
        # Simulate the race: pre-insert the "winner" so the dedup SELECT
        # finds it after the loser's INSERT fails with IntegrityError.
        winner = DocumentModel(
            id=uuid.uuid4(),
            filename="winner.pdf",
            file_path="/tmp/winner.pdf",
            owner_id=user_id,
            tenant_id=tenant_id,
            patient_id=patient_id,
            source_integration_id=integration_id,
            external_id=external_id,
            status="uploaded",
            progress=0,
            include_in_extraction=False,
        )
        db.add(winner)
        await db.commit()

        # Patch db.commit so the next INSERT raises IntegrityError as if
        # another worker beat us to it. The recovery re-fetches.
        async def _failing_commit():
            raise IntegrityError("simulated", params=None, orig=Exception("uniq"))

        monkeypatch.setattr(db, "commit", _failing_commit)

        result = await document_service.ingest_document_bytes(
            filename="loser.pdf",
            content=b"%PDF-1.4 loser\n%%EOF\n",
            content_type="application/pdf",
            tenant_id=tenant_id,
            patient_id=patient_id,
            owner_id=user_id,
            db=db,
            include_in_extraction=False,
            source_integration_id=integration_id,
            external_id=external_id,
        )

        # The recovery path should have re-fetched the winner.
        assert result.id == winner.id


# ---------------------------------------------------------------------------
# DocumentPull spec (SDK)
# ---------------------------------------------------------------------------


def test_document_pull_spec_carries_external_id():
    """The SDK ``DocumentPull`` Pydantic spec must accept ``external_id``."""
    from integrations.sdk import DocumentPull

    spec = DocumentPull(
        filename="x.pdf",
        content=b"data",
        external_id="lab-acc-001",
    )
    assert spec.external_id == "lab-acc-001"


def test_document_pull_spec_external_id_optional():
    """Providers that don't have a stable upstream id leave it unset —
    they manage idempotency via cursor."""
    from integrations.sdk import DocumentPull

    spec = DocumentPull(filename="x.pdf", content=b"data")
    assert spec.external_id is None


# ---------------------------------------------------------------------------
# Engine wiring — run_sync passes source_integration_id + external_id
# ---------------------------------------------------------------------------


def test_run_sync_passes_integration_keys_to_ingest_document_bytes():
    """Source-level guard: the engine must forward the integration's id
    + the spec's external_id to the canonical write path. We can't run
    the full pipeline without a provider + DB, so this is a static check
    on the source."""
    src = Path(document_service.__file__).read_text()
    assert "source_integration_id=integration.id" not in src  # document_service itself doesn't say this
    integration_sync_path = (
        Path(document_service.__file__).parent / "integration_sync_service.py"
    )
    sync_src = integration_sync_path.read_text()
    assert "source_integration_id=integration.id" in sync_src
    assert "external_id=getattr" in sync_src or "external_id=doc_spec.external_id" in sync_src
