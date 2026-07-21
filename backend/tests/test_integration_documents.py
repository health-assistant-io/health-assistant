"""Tests for the documents opt-in hook in the integrations SDK (C.2).

Source-level wiring guards + a behavioural test for the engine path.
Mirror of the contract tests already covering clinical events /
examinations / catalog-proposals / HITL-proposals.
"""
import inspect
import uuid

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def tenant_user_patient_integration():
    """Create an isolated tenant + user + patient + integration row.

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
                name="Doc Sync T.",
                slug=f"docsync-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"docsync-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Sync", "given": ["Doc"]},
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
                provider="test_docs",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()

    return tenant_id, user_id, patient_id, integration_id


# ---------------------------------------------------------------------------
# SDK hook defaults + re-export (mirror test_integration_registry.py style)
# ---------------------------------------------------------------------------


def test_sdk_base_declares_documents_opt_in_hook_with_safe_defaults():
    """``BaseHealthProvider`` must declare ``supports_documents`` and
    ``pull_documents`` with safe defaults (False / []) so existing
    providers that don't opt in are unaffected. Mirrors the contract tests
    for clinical events / examinations / catalog / HITL proposals."""
    from integrations.sdk.base import BaseHealthProvider as SDKBaseProvider

    assert hasattr(SDKBaseProvider, "supports_documents")
    assert hasattr(SDKBaseProvider, "pull_documents")

    supports_src = inspect.getsource(SDKBaseProvider.supports_documents)
    assert "return False" in supports_src, (
        "supports_documents must default to False — flipping it to True "
        "would opt every existing integration into the documents pull "
        "path"
    )

    pull_src = inspect.getsource(SDKBaseProvider.pull_documents)
    assert "return []" in pull_src, (
        "pull_documents must default to [] — providers that haven't "
        "implemented it must return an empty list, not raise"
    )


def test_document_pull_is_reexported_from_sdk():
    """``from integrations.sdk import DocumentPull`` must work."""
    from integrations.sdk import DocumentPull
    from integrations.sdk.documents import DocumentPull as Source

    assert DocumentPull is Source, (
        "SDK re-export must alias the spec, not duplicate it"
    )


# ---------------------------------------------------------------------------
# Engine wiring — source-level guard
# ---------------------------------------------------------------------------


def test_run_sync_wires_documents_opt_in_hook():
    """``run_sync`` must call ``provider.pull_documents`` gated on
    ``provider.supports_documents()``, then route each returned spec
    through ``document_service.ingest_document_bytes``. Mirrors the
    earlier workstream guards."""
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc.run_sync)
    assert "supports_documents" in src, (
        "run_sync must probe supports_documents — the opt-in gate for "
        "the documents pull hook"
    )
    assert "pull_documents" in src, (
        "run_sync must call pull_documents on providers that opt in"
    )
    assert "ingest_document_bytes" in src, (
        "run_sync must route pulled specs through "
        "document_service.ingest_document_bytes (the canonical write path)"
    )


def test_run_sync_enforces_documents_caps():
    """``run_sync`` must honor BOTH the per-sync item-count cap and the
    byte-total cap so a runaway provider can't exhaust catalog/disk/RAM."""
    from app.services import integration_sync_service as svc

    assert svc.INTEGRATION_MAX_DOCS_PER_SYNC > 0
    assert svc.INTEGRATION_MAX_DOC_BYTES_PER_SYNC > 0

    src = inspect.getsource(svc.run_sync)
    assert "INTEGRATION_MAX_DOCS_PER_SYNC" in src
    assert "INTEGRATION_MAX_DOC_BYTES_PER_SYNC" in src


def test_run_sync_resolves_examination_external_id_via_hoisted_map():
    """``run_sync`` must build the ``exam_by_external_id`` map during the
    examinations block and surface it to the documents block so
    ``DocumentPull.examination_external_id`` can be resolved against an
    exam just pulled."""
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc.run_sync)
    assert "exam_by_external_id" in src, (
        "run_sync must build + consult the exam_by_external_id map so "
        "DocumentPull.examination_external_id can be linked"
    )


def test_sync_result_carries_document_counts():
    """``SyncResult`` must surface ``documents_pulled`` /
    ``documents_written`` for sync-log reporting."""
    from app.services.integration_sync_service import SyncResult

    result = SyncResult()
    assert hasattr(result, "documents_pulled")
    assert hasattr(result, "documents_written")


# ---------------------------------------------------------------------------
# Behavioural — caps are actually enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_caps_are_enforced(tenant_user_patient_integration):
    """A provider that returns more documents than the per-sync cap (or
    whose total bytes exceed the byte cap) must have the over-cap items
    dropped — the under-cap items still land."""
    from unittest.mock import patch

    from app.core.database import AsyncSessionLocal
    from app.models.document_model import DocumentModel
    from app.services import integration_sync_service as svc
    from integrations.sdk.documents import DocumentPull

    tenant_id, user_id, patient_id, integration_id = (
        tenant_user_patient_integration
    )

    # Build >20 small docs — the per-sync count cap (20) should drop the
    # excess. Each doc is tiny so the byte cap never engages.
    over_cap_specs = [
        DocumentPull(
            filename=f"doc-{i:02d}.txt",
            content=f"doc {i} body".encode(),
            content_type="text/plain",
            include_in_extraction=False,
        )
        for i in range(25)
    ]

    class _Provider:
        domain = "test_docs"

        def supports_documents(self) -> bool:
            return True

        async def pull_documents(self, integration):
            return list(over_cap_specs)

        async def pull_data(self, integration):
            return []

    async with AsyncSessionLocal() as db:
        # Load the integration row.
        from app.models.user_integration import UserIntegration

        integration = (
            await db.execute(
                __import__("sqlalchemy").select(UserIntegration).where(
                    UserIntegration.id == integration_id
                )
            )
        ).scalar_one()

        # Mock the OCR dispatch so the test doesn't need a broker.
        with patch("app.workers.ai_tasks.ocr_document.apply_async"):
            result = await svc.run_sync(
                db, integration, _Provider(), source="test"
            )

    assert result.documents_pulled == 25
    assert result.documents_written == svc.INTEGRATION_MAX_DOCS_PER_SYNC, (
        "Only the first 20 docs should land — the count cap must drop the "
        "remaining 5"
    )
    # The documents actually landed.
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                __import__("sqlalchemy").select(DocumentModel).where(
                    DocumentModel.tenant_id == tenant_id
                )
            )
        ).scalars().all()
    filenames = {r.filename for r in rows}
    # First 20 by enumeration order: doc-00.txt through doc-19.txt.
    assert {f"doc-{i:02d}.txt" for i in range(20)} <= filenames
    assert "doc-24.txt" not in filenames


# ---------------------------------------------------------------------------
# (fixtures live at the top of the file)
# ---------------------------------------------------------------------------
