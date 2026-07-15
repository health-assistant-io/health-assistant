"""Tests for audit items A3 + A4 — upload validation + download XSS + size cap.

A3: the upload path must reject types that can carry active content (svg/html/
xml/js) so a stored-XSS can't run at the app origin via the inline download.
The download path must force ``attachment`` for those types regardless and set
``X-Content-Type-Options: nosniff`` on every served file.

A4: ``MAX_UPLOAD_SIZE`` must actually be enforced — an oversized upload is
rejected with 413 before it exhausts server RAM.
"""
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.document_service import (
    _read_capped,
    _validate_upload_extension,
    should_serve_inline,
)


class TestUploadAllowlist:
    @pytest.mark.parametrize("ext", [".pdf", ".png", ".jpg", ".jpeg", ".dcm", ".md", ".txt", ".tiff", ".gif"])
    def test_accepts_safe_types(self, ext):
        assert _validate_upload_extension(f"report{ext}") == ext

    @pytest.mark.parametrize(
        "ext", [".svg", ".svgz", ".html", ".htm", ".xml", ".xhtml", ".js", ".exe", ".sh", ""]
    )
    def test_rejects_active_or_unknown_types(self, ext):
        with pytest.raises(HTTPException) as exc:
            _validate_upload_extension(f"payload{ext}")
        assert exc.value.status_code == 400

    def test_rejection_message_is_user_safe(self):
        """The detail may echo the user's own extension but must not leak
        server internals (paths, DB details, tracebacks)."""
        with pytest.raises(HTTPException) as exc:
            _validate_upload_extension("evil.svg")
        detail = exc.value.detail
        assert exc.value.status_code == 400
        # Echoing the user-supplied extension is fine; leaking internals is not.
        assert "/" not in detail  # no filesystem paths
        assert "Traceback" not in detail
        assert "Allowed" in detail  # helpful guidance present


class TestInlineServingGuard:
    @pytest.mark.parametrize("ext", [".svg", ".svgz", ".html", ".htm", ".xml", ".xhtml", ".js"])
    def test_active_types_not_served_inline(self, ext):
        assert should_serve_inline(f"file{ext}") is False

    @pytest.mark.parametrize("ext", [".pdf", ".png", ".jpg", ".jpeg", ".dcm", ".txt"])
    def test_safe_types_served_inline(self, ext):
        assert should_serve_inline(f"file{ext}") is True

    def test_unknown_extension_served_inline(self):
        # Unknown extensions are caught by the upload allowlist; the inline
        # guard only cares about the known active-content blocklist.
        assert should_serve_inline("file.unknownext") is True


class FakeUploadFile:
    """Minimal async-readable stand-in for starlette UploadFile."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    async def read(self, n: int = -1):
        return self._buf.read(n)


class TestSizeCap:
    @pytest.mark.asyncio
    async def test_under_limit_succeeds(self):
        data = b"x" * (2 * 1024 * 1024)  # 2 MiB
        out = await _read_capped(FakeUploadFile(data), max_bytes=3 * 1024 * 1024)
        assert out == data

    @pytest.mark.asyncio
    async def test_over_limit_rejected_413(self):
        # Stream more than the cap in 1 MiB chunks.
        cap = 1024 * 1024  # 1 MiB cap
        big = b"x" * (2 * 1024 * 1024)  # 2 MiB body
        with pytest.raises(HTTPException) as exc:
            await _read_capped(FakeUploadFile(big), max_bytes=cap)
        assert exc.value.status_code == 413

    @pytest.mark.asyncio
    async def test_empty_file_ok(self):
        out = await _read_capped(FakeUploadFile(b""), max_bytes=1024)
        assert out == b""

    @pytest.mark.asyncio
    async def test_exact_limit_ok(self):
        cap = 1024 * 1024
        data = b"x" * cap
        out = await _read_capped(FakeUploadFile(data), max_bytes=cap)
        assert len(out) == cap


class TestDownloadHeaders:
    """Static guard: the download endpoint sets nosniff + conditional inline."""

    def test_download_sets_nosniff_and_inline_logic(self):
        import inspect
        from app.api.v1.endpoints import documents

        src = inspect.getsource(documents.download_document_endpoint)
        assert "X-Content-Type-Options" in src
        assert "nosniff" in src
        assert "should_serve_inline" in src
        # No longer unconditionally inline.
        assert 'content_disposition_type="inline"  # Crucial' not in src
