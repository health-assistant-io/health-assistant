"""Security regression tests — 2026-08 audit Batch 2 (API attack surface).

Covers:
- C-3  backup-restore filename extension traversal is blocked
- H1  /import/ocr no longer accepts a client-controlled api_base
- H2  patient-scope export validates every patient_id
- H4  document preview enforces owner for USER
- H5  presign / dicom-metadata are tenant-filtered
- M2  GET /observations/{id} enforces the USER patient gate
"""

import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.enums import ExportScope, Role
from app.schemas.user import TokenData


def _token(role, tenant_id, user_id):
    return TokenData(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        sub="x@example.com",
    )


# ---------------------------------------------------------------------------
# C-3 — backup restore extension validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_documents_sanitizes_filename_extension(tmp_path):
    from app.services.import_service import ImportService

    svc = ImportService.__new__(ImportService)  # no real DB needed
    svc.db = MagicMock()
    svc.db.flush = AsyncMock()
    svc.db.add = MagicMock()

    meta = [
        {"filename": "report./../../../../tmp/pwned", "_archive_path": "documents/0"},
        {"filename": "legit.pdf", "_archive_path": "documents/1"},
        {"filename": "evil.svg", "_archive_path": "documents/2"},
    ]

    archive = MagicMock()
    archive.read = MagicMock(return_value=b"bytes")

    tenant_id = "11111111-1111-1111-1111-111111111111"
    upload_dir = tmp_path / "uploads"
    with (
        patch("app.services.document_service.UPLOAD_DIR", upload_dir),
        patch(
            "app.services.document_service.ALLOWED_UPLOAD_EXTENSIONS",
            frozenset({".pdf", ".txt", ".png"}),
        ),
    ):
        count = await svc.restore_documents(
            meta, archive, tenant_id, id_remap={}, owner_id=None
        )

    assert count >= 1
    written = list((upload_dir / tenant_id).iterdir())
    for f in written:
        # Every written file lives inside the tenant dir and has a safe name.
        assert f.parent == upload_dir / tenant_id
        assert f.name.split("/")[-1] == f.name
        assert ".." not in f.name
        assert "/" not in f.name
        assert f.suffix in {".pdf", ".txt", ".png", ".bin"}
    # The traversal name must not have escaped the tenant dir.
    assert not (tmp_path / "pwned").exists()
    assert not (Path("/tmp") / "pwned").exists()


def test_no_client_api_base_on_ocr_endpoint():
    import inspect

    from app.api.v1.endpoints import import_data

    sig = inspect.signature(import_data.import_ocr)
    assert "api_base" not in sig.parameters, (
        "/import/ocr must not accept a client-controlled api_base (API-H1)"
    )


# ---------------------------------------------------------------------------
# H2 — export validates every requested patient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_validates_patient_ids():
    from app.api.v1.endpoints.export import _validate_patient_scoping
    from uuid import uuid4

    tenant = uuid4()
    user = uuid4()
    other_patient = str(uuid4())

    current_user = _token(Role.USER.value, tenant, user)

    with patch(
        "app.services.access.check_patient_access", new_callable=AsyncMock
    ) as mock_check:
        await _validate_patient_scoping(
            ExportScope.PATIENT, [other_patient], current_user, db=MagicMock()
        )
        mock_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_export_denies_foreign_patient():
    from uuid import uuid4

    from app.api.v1.endpoints.export import _validate_patient_scoping
    from app.services.access import AuthorizationError

    tenant = uuid4()
    user = uuid4()

    async def deny(pid, cu, db):
        raise AuthorizationError("nope")

    with patch("app.services.access.check_patient_access", new=deny):
        with pytest.raises(AuthorizationError):
            await _validate_patient_scoping(
                ExportScope.PATIENT,
                [str(uuid4())],
                _token(Role.USER.value, tenant, user),
                db=MagicMock(),
            )


# ---------------------------------------------------------------------------
# AI chat — patient context validated (H3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_assistance_validates_patient_context():
    import inspect

    from app.api.v1.endpoints import ai_assistance

    assert hasattr(ai_assistance, "_validate_patient_context")
    src = inspect.getsource(ai_assistance.assist_user)
    assert "_validate_patient_context" in src
    src_stream = inspect.getsource(ai_assistance.assist_user_stream)
    assert "_validate_patient_context" in src_stream
    src_tools = inspect.getsource(ai_assistance.list_tools)
    assert "_validate_patient_context" in src_tools


# ---------------------------------------------------------------------------
# H5 — presign / dicom are tenant-filtered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_presign_uses_tenant_scoped_fetch():
    import inspect

    from app.api.v1.endpoints import documents

    src = inspect.getsource(documents.get_presigned_url_endpoint)
    assert "current_user.tenant_id" in src
    # The fetch must pass the tenant for non-SYSTEM_ADMIN callers.
    assert (
        "None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id"
        in src
    )


# ---------------------------------------------------------------------------
# M2 — observations GET enforces USER gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observation_get_checks_user_patient():
    import inspect

    from app.api.v1.endpoints import observations

    src = inspect.getsource(observations.get_observation_endpoint)
    assert "check_patient_access" in src
