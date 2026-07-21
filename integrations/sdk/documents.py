"""Document-pull authoring helpers for integration providers (workstream C).

Providers that can deliver document bytes from upstream (a hospital
integration that pulls scanned lab reports, a wearable companion app
that syncs ECG printouts, a fax-to-email gateway that forwards PDFs)
opt into the document-pull path by:

1. Overriding :meth:`BaseHealthProvider.supports_documents` to return
   ``True``.
2. Implementing :meth:`BaseHealthProvider.pull_documents` to return a
   list of :class:`DocumentPull` objects — each carrying the filename,
   raw bytes, and optional metadata for linking.

The platform's ``run_sync`` pipeline calls ``pull_documents`` after the
examinations + catalog-proposals + HITL-proposals steps, persists each
document via :func:`app.services.document_service.ingest_document_bytes`
(the same write path the UI upload endpoint uses), and fires the OCR
Celery task when ``include_in_extraction=True``.

This module mirrors :mod:`integrations.sdk.catalog` (Pydantic spec +
``ConfigDict(extra="forbid")``) — the parent plan's ``@dataclass`` advice
predates F's Pydantic convention.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentPull(BaseModel):
    """One document the provider has fetched from upstream and wants the
    platform to ingest.

    The provider is responsible for fetching the bytes (HTTP download,
    webhook payload extraction, base64 decode, etc.) — the platform
    ingests whatever bytes are returned. Per-sync caps
    (``INTEGRATION_MAX_DOCS_PER_SYNC`` + ``INTEGRATION_MAX_DOC_BYTES_PER_SYNC``)
    protect against runaway providers; over-cap items are dropped with a
    warning.

    Per-document idempotency is the provider's responsibility — the
    platform has no ``source_integration_id`` + ``external_id`` columns
    on ``DocumentModel`` today (deferred per parent plan §D.2 — dedup
    piggybacks on the examination link or the provider's own cursor via
    ``set_sync_cursor``).
    """

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(
        ...,
        description=(
            "Original filename. The extension gates the on-disk save (the "
            "service's ``ALLOWED_UPLOAD_EXTENSIONS`` allowlist applies). "
            "Medical-document types only: PDF, PNG/JPG/BMP/WebP/TIFF/GIF, "
            "DICOM (``.dcm``), plain text (``.txt`` / ``.md``)."
        ),
    )
    content: bytes = Field(
        ...,
        description=(
            "Raw document bytes — the provider fetches them from upstream "
            "before returning the spec. The platform's per-sync byte cap "
            "(default 50 MiB) is enforced against the running total."
        ),
    )
    content_type: Optional[str] = Field(
        default=None,
        description=(
            "Optional MIME type. Informational — the extension gate is what "
            "actually matters. Auto-detected from the filename extension "
            "by some downstream paths when left unset."
        ),
    )
    examination_external_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional upstream encounter/visit id from ``pull_examinations``. "
            "The engine resolves this against the exams just pulled (via "
            "their ``external_id``) and links the resulting document row. "
            "Misses are non-fatal — the document is created unlinked."
        ),
    )
    category_concept_slug: Optional[str] = Field(
        default=None,
        description=(
            "Optional catalog concept slug for the document category "
            "(e.g. ``lab-report``, ``imaging``, ``clinical-note``). The "
            "engine resolves this via ``resolve_concept_by_slug`` and "
            "stamps ``DocumentModel.category_concept_id``. Misses are "
            "non-fatal — the document is created with no category."
        ),
    )
    include_in_extraction: bool = Field(
        default=True,
        description=(
            "When True (the default), the OCR + LLM extraction Celery task "
            "fires after the document is persisted. Set to False for "
            "documents that don't need OCR (e.g. plain-text uploads, "
            "already-extracted records)."
        ),
    )


__all__ = [
    "DocumentPull",
]
