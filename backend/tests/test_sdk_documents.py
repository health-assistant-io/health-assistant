"""Tests for ``integrations.sdk.documents`` — DocumentPull spec.

Phase 3.2 hardening: ``filename`` is sanitized at the SDK boundary (rejects
path traversal, absolute paths, NUL/control chars, overlong names) and the
identifier fields carry ``max_length`` caps.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from integrations.sdk.documents import DocumentPull


def _valid() -> dict:
    return {"filename": "report.pdf", "content": b"%PDF-1.4 ..."}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_document_pull_minimal():
    d = DocumentPull(**_valid())
    assert d.filename == "report.pdf"
    assert d.content == b"%PDF-1.4 ..."
    assert d.include_in_extraction is True  # default


def test_document_pull_full():
    d = DocumentPull(
        filename="ecg.png",
        content=b"\x89PNG",
        content_type="image/png",
        examination_external_id="enc-123",
        external_id="doc-456",
        category_concept_slug="imaging",
        include_in_extraction=False,
    )
    assert d.external_id == "doc-456"
    assert d.include_in_extraction is False


# ---------------------------------------------------------------------------
# filename sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "../etc/passwd",
        "../../secret",
        "a/../b.pdf",
        "/etc/passwd",
        "/absolute/path/report.pdf",
        "C:\\Windows\\system32\\evil.dll",
        "report\x00.pdf",          # NUL byte
        "report\n.pdf",            # control char (newline)
        "report\x1b[31m.pdf",      # ESC (control)
        "",                        # empty
        "   ",                     # whitespace only
    ],
)
def test_filename_rejects_unsafe(bad_name):
    with pytest.raises((ValidationError, ValueError)):
        DocumentPull(filename=bad_name, content=b"x")


@pytest.mark.parametrize(
    "good_name",
    [
        "report.pdf",
        "lab_report_2026-07-23.PDF",
        "sub dir/ecg.png",     # a subdirectory component is fine, just no '..'
        "scan (1).tiff",
        "café-results.dcm",     # non-ASCII filename is fine
    ],
)
def test_filename_accepts_safe(good_name):
    d = DocumentPull(filename=good_name, content=b"x")
    assert d.filename == good_name


def test_filename_rejects_too_long():
    with pytest.raises((ValidationError, ValueError)):
        DocumentPull(filename="a" * 300 + ".pdf", content=b"x")


def test_external_id_and_slug_capped():
    """Long ids are rejected at the SDK boundary (defense-in-depth)."""
    with pytest.raises(ValidationError):
        DocumentPull(filename="x.pdf", content=b"x", external_id="a" * 200)
    with pytest.raises(ValidationError):
        DocumentPull(filename="x.pdf", content=b"x", category_concept_slug="a" * 200)


def test_extra_fields_rejected():
    """ConfigDict(extra='forbid') — typos surface at construction."""
    with pytest.raises(ValidationError):
        DocumentPull(filename="x.pdf", content=b"x", typooo="oops")
