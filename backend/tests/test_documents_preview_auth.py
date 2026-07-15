"""Regression tests for audit item B5 — document preview auth hole.

Pre-fix contract: ``GET /documents/{id}/preview`` had the auth logic
``if not token: pass`` — omitting the ``?token=`` query param served any
document's image content with **no auth at all**. Critical PHI exfil vector.

Post-fix contract pinned here:
1. A request with neither ``?token=`` nor ``Authorization: Bearer`` → 401.
2. A request with a presigned ``?token=`` is honored (frontend <img> flow).
3. A request with a valid Bearer JWT for the document's tenant is honored.
4. A request with a valid Bearer JWT for a *different* tenant → 404 (no
   information leak).
5. ``SYSTEM_ADMIN`` Bearer JWT is honored regardless of tenant.
"""
import io
import uuid
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from app.api.v1.endpoints import documents as docs_endpoint
from app.models.enums import Role
from app.schemas.user import TokenData


_FAKE_IMG_BYTES = b"PNGDATA"


def _patched_open():
    """A ``builtins.open`` patcher that returns PNG bytes for any read.

    The endpoint uses ``open(file_path, "rb")`` to slurp the image; we want
    that call to succeed without touching the filesystem. Using
    ``mock_open`` with a static read keeps third-party imports (pandas
    etc.) that also probe ``open`` working — they read what they need.
    """
    m = mock_open(read_data=_FAKE_IMG_BYTES)
    # ``mock_open`` returns the mock itself from ``__enter__``; its
    # ``.read()`` returns the configured bytes.
    return m


def _user(tenant_id=None, role=Role.USER.value) -> TokenData:
    return TokenData(
        sub="test@local",
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=role,
    )


def _request_with(headers: dict | None = None):
    """Build a Starlette-style Request mock with given headers."""
    request = MagicMock()
    h = {}
    for k, v in (headers or {}).items():
        h[k.lower()] = v
    request.headers.get = lambda key, default=None: h.get(key.lower(), default)
    request.headers.__contains__ = lambda key: key.lower() in h
    request.headers.__getitem__ = lambda key: h[key.lower()]
    return request


def _doc(tenant_id, filename="report.pdf", file_path="/tmp/report.pdf"):
    fake = MagicMock()
    fake.id = uuid.uuid4()
    fake.tenant_id = tenant_id
    fake.filename = filename
    fake.file_path = file_path
    return fake


# ---------------------------------------------------------------------------
# B5 core: omitting both credentials → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_without_token_or_bearer_returns_401():
    """The core B5 hole: omitting both credentials must not serve the doc."""
    from fastapi import HTTPException

    request = _request_with({})  # no Authorization header
    db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        await docs_endpoint.get_document_preview_endpoint(
            request=request,
            document_id=str(uuid.uuid4()),
            page=0,
            token=None,
            db=db,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_preview_with_bad_bearer_returns_401():
    """An expired/invalid Bearer token is rejected."""
    from fastapi import HTTPException

    request = _request_with({"Authorization": "Bearer not.a.real.token"})
    db = MagicMock()

    with patch(
        "app.core.security.decode_access_token", return_value=None
    ):
        with pytest.raises(HTTPException) as exc:
            await docs_endpoint.get_document_preview_endpoint(
                request=request,
                document_id=str(uuid.uuid4()),
                page=0,
                token=None,
                db=db,
            )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Presigned-token path (frontend <img src> flow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_with_valid_presigned_token_succeeds():
    """A valid presigned token serves the doc — no Bearer needed."""
    tenant_a = uuid.uuid4()
    doc = _doc(tenant_a, filename="img.png")
    request = _request_with({})  # no Authorization header
    db = MagicMock()

    with patch(
        "app.core.security.verify_presigned_token", return_value=True
    ), patch.object(
        docs_endpoint, "get_document", new=AsyncMock(return_value=doc)
    ), patch("pathlib.Path.exists", lambda self: True), patch(
        "builtins.open", _patched_open()
    ):
        result = await docs_endpoint.get_document_preview_endpoint(
            request=request,
            document_id=str(doc.id),
            page=0,
            token="presigned-token",
            db=db,
        )
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_preview_with_invalid_presigned_token_returns_401():
    """An invalid/expired presigned token is rejected."""
    from fastapi import HTTPException

    request = _request_with({})
    db = MagicMock()

    with patch(
        "app.core.security.verify_presigned_token", return_value=False
    ):
        with pytest.raises(HTTPException) as exc:
            await docs_endpoint.get_document_preview_endpoint(
                request=request,
                document_id=str(uuid.uuid4()),
                page=0,
                token="bad-token",
                db=db,
            )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Bearer path: tenant enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_bearer_same_tenant_succeeds():
    tenant_a = uuid.uuid4()
    user = _user(tenant_a)
    doc = _doc(tenant_a, filename="img.png")

    request = _request_with({"Authorization": "Bearer some-jwt"})
    db = MagicMock()

    payload = {
        "sub": "test@local",
        "user_id": str(user.user_id),
        "tenant_id": str(tenant_a),
        "role": Role.USER.value,
    }
    def _fake_open(*a, **kw):
        m = MagicMock()
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: None
        m.read = lambda: b"PNGDATA"
        return m

    with patch("app.core.security.decode_access_token", return_value=payload), patch.object(
        docs_endpoint, "get_document", new=AsyncMock(return_value=doc)
    ), patch("pathlib.Path.exists", lambda self: True), patch(
        "builtins.open", _patched_open()
    ):
        result = await docs_endpoint.get_document_preview_endpoint(
            request=request,
            document_id=str(doc.id),
            page=0,
            token=None,
            db=db,
        )
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_preview_bearer_cross_tenant_returns_404():
    """A USER Bearer JWT for a different tenant → 404 (no info leak)."""
    from fastapi import HTTPException

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user = _user(tenant_a)
    doc = _doc(tenant_b, filename="img.png")

    request = _request_with({"Authorization": "Bearer some-jwt"})
    db = MagicMock()

    payload = {
        "sub": "test@local",
        "user_id": str(user.user_id),
        "tenant_id": str(tenant_a),
        "role": Role.USER.value,
    }
    with patch("app.core.security.decode_access_token", return_value=payload), patch.object(
        docs_endpoint, "get_document", new=AsyncMock(return_value=doc)
    ):
        with pytest.raises(HTTPException) as exc:
            await docs_endpoint.get_document_preview_endpoint(
                request=request,
                document_id=str(doc.id),
                page=0,
                token=None,
                db=db,
            )
    assert exc.value.status_code == 404, (
        "Cross-tenant preview must return 404 (not 403) so we don't leak "
        "the existence of the document in another tenant."
    )


@pytest.mark.asyncio
async def test_preview_bearer_system_admin_cross_tenant_succeeds():
    """SYSTEM_ADMIN can preview any tenant's documents (operator role)."""
    tenant_a = uuid.uuid4()
    admin = _user(tenant_a, role=Role.SYSTEM_ADMIN.value)
    doc = _doc(uuid.uuid4(), filename="img.png")  # different tenant

    request = _request_with({"Authorization": "Bearer admin-jwt"})
    db = MagicMock()

    payload = {
        "sub": "admin@local",
        "user_id": str(admin.user_id),
        "tenant_id": str(tenant_a),
        "role": Role.SYSTEM_ADMIN.value,
    }
    def _fake_open(*a, **kw):
        m = MagicMock()
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: None
        m.read = lambda: b"PNGDATA"
        return m

    with patch("app.core.security.decode_access_token", return_value=payload), patch.object(
        docs_endpoint, "get_document", new=AsyncMock(return_value=doc)
    ), patch("pathlib.Path.exists", lambda self: True), patch(
        "builtins.open", _patched_open()
    ):
        result = await docs_endpoint.get_document_preview_endpoint(
            request=request,
            document_id=str(doc.id),
            page=0,
            token=None,
            db=db,
        )
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_preview_document_not_found_returns_404():
    """Even with valid auth, a missing doc → 404."""
    from fastapi import HTTPException

    request = _request_with({})
    db = MagicMock()

    with patch(
        "app.core.security.verify_presigned_token", return_value=True
    ), patch.object(
        docs_endpoint, "get_document", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(HTTPException) as exc:
            await docs_endpoint.get_document_preview_endpoint(
                request=request,
                document_id=str(uuid.uuid4()),
                page=0,
                token="presigned",
                db=db,
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_preview_malformed_bearer_prefix_returns_401():
    """A non-Bearer Authorization header is rejected."""
    from fastapi import HTTPException

    request = _request_with({"Authorization": "Basic abc123"})
    db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        await docs_endpoint.get_document_preview_endpoint(
            request=request,
            document_id=str(uuid.uuid4()),
            page=0,
            token=None,
            db=db,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_preview_dead_code_branch_removed():
    """Static check: the unreachable ``return Response(...)`` after the
    ``raise HTTPException`` (audit K9) is gone from the source."""
    import inspect

    src = inspect.getsource(docs_endpoint.get_document_preview_endpoint)
    # Audit K9 removed the unreachable dead code; audit A6 requires that no
    # broad except leaks the raw exception to the client via detail=str(e).
    assert "detail=str(e)" not in src, (
        "Preview endpoint must not leak raw exception detail (audit A6)."
    )
    assert "detail=f\"Failed" not in src, (
        "Preview endpoint must not embed str(e) in detail (audit A6)."
    )
    # The function-level generic handler re-raises (lets the global handler
    # produce a correlation id) instead of returning a hand-crafted 500.
    assert "raise HTTPException(\n            status_code=500" not in src, (
        "Preview endpoint should re-raise, not craft its own 500 (audit A6)."
    )
