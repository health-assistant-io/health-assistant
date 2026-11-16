# ruff: noqa: B904,E501 -- long immutable strings; reflow when touched
import contextlib
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import anyio
from fastapi import HTTPException
from sqlalchemy import false, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document_model import DocumentModel
from app.schemas.user import TokenData
from app.services.fhir.fhir_helpers import assert_valid_fhir
from app.storage.bootstrap import (
    current_config,
    effective_max_upload_bytes,
)
from app.storage.documents import (
    DocumentStorage,
    astorage_key_or_legacy,
    document_key,
    document_object_key,
)
from app.storage.object_storage import ObjectTooLarge, StorageError
from app.utils.image_utils import edit_image


def effective_upload_max_bytes() -> int:
    """The enforced upload cap: ``min(host MAX_UPLOAD_SIZE, contract cap)``."""
    return effective_max_upload_bytes(current_config(), settings)


def _render_document_edit(workdir: str, render, storage: DocumentStorage, original) -> bytes:
    """Materialize the source, render into a temp path, return the bytes.

    Runs in a worker thread (``anyio.to_thread``): both the store read and the
    Pillow edit are blocking, and neither may hold the event loop.
    """
    suffix = Path(original.filename).suffix or ".png"
    with storage.materialize(original) as source:
        dest = Path(workdir) / f"edited{suffix}"
        render(source, dest)
        return dest.read_bytes()

# Allowed upload extensions (audit A3). Deliberately EXCLUDES types that can
# carry active content executable in the browser at the app origin — svg,
# html/htm, xml, js — because the download endpoint serves files inline and a
# stored XSS would run with the victim's session. Medical documents are
# PDF/images/DICOM/text only.
ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Documents
        ".pdf",
        ".txt",
        ".md",
        # Raster images (no svg — svg can embed <script>)
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".webp",
        ".tiff",
        ".tif",
        ".gif",
        # Medical imaging
        ".dcm",
    }
)

# Extensions that must NEVER be served inline (force-download only). Even if
# one slipped past the upload allowlist, it is served as an attachment.
_INLINE_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {".svg", ".svgz", ".html", ".htm", ".xml", ".xhtml", ".js"}
)


def _validate_upload_extension(filename: str | None) -> str:
    """Return the lowercased extension if allowed, else raise 400."""
    name = filename or "unknown"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or '(none)'}'. "
            f"Allowed: {sorted(ALLOWED_UPLOAD_EXTENSIONS)}.",
        )
    return ext


def should_serve_inline(filename: str | None) -> bool:
    """True if the file may be served ``inline`` (not an active-content type)."""
    name = filename or ""
    return Path(name).suffix.lower() not in _INLINE_BLOCKED_EXTENSIONS


async def _read_capped(file, max_bytes: int) -> bytes:
    """Read an UploadFile in chunks, aborting with 413 if it exceeds ``max_bytes``.

    Prevents a single oversized request from exhausting server RAM (audit A4)
    — the previous ``await file.read()`` loaded the whole body unconditionally.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MiB steps
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum allowed size is {max_bytes // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _resolve_practitioner_id(
    db: AsyncSession, owner_id: str | UUID, tenant_id: str | UUID
) -> UUID | None:
    """Look up the Practitioner (DoctorModel) id for a given owner user.

    Used by upload paths to populate DocumentModel.practitioner_id so that
    DocumentReference.author can emit a resolvable `Practitioner/<id>`
    reference (audit F11). Returns None if the owner has no DoctorModel row
    (e.g. admin/manager uploads) — in that case `author` is omitted rather
    than emit a wrong reference.
    """
    from app.models.doctor_model import DoctorModel

    try:
        owner_uuid = owner_id if isinstance(owner_id, UUID) else UUID(str(owner_id))
    except (ValueError, TypeError):
        return None
    result = await db.execute(
        select(DoctorModel.id).where(
            DoctorModel.user_id == owner_uuid,
            DoctorModel.tenant_id == tenant_id,
        )
    )
    row = result.scalar_one_or_none()
    return row


async def upload_document(
    file,
    patient_id: str | None,
    owner_id: str | UUID,
    tenant_id: str | UUID,
    db: AsyncSession,
    examination_id: str | None = None,
    include_in_extraction: bool = False,
) -> DocumentModel:
    """Upload a document (Starlette ``UploadFile``) and save to database.

    Thin wrapper around :func:`ingest_document_bytes` — reads the upload
    in capped chunks (audit A4: prevents RAM exhaustion from oversized
    bodies) and delegates the storage + DB write + best-effort OCR
    dispatch to the bytes-shaped canonical entrypoint. The bytes-shaped
    entrypoint exists so Celery / webhook / api-proxy contexts without an
    ``UploadFile`` can use the same ingestion path (workstream C of the
    integrations follow-ups pass).
    """
    max_bytes = effective_upload_max_bytes()
    content = await _read_capped(file, max_bytes)
    return await ingest_document_bytes(
        filename=file.filename or "unknown",
        content=content,
        content_type=file.content_type,
        tenant_id=tenant_id,
        patient_id=patient_id,
        owner_id=owner_id,
        db=db,
        examination_id=examination_id,
        include_in_extraction=include_in_extraction,
    )


async def ingest_document_bytes(
    *,
    filename: str,
    content: bytes,
    content_type: str | None,
    tenant_id: str | UUID,
    patient_id: str | UUID | None,
    owner_id: str | UUID,
    db: AsyncSession,
    examination_id: str | UUID | None = None,
    include_in_extraction: bool = True,
    category_concept_id: str | UUID | None = None,
    source_integration_id: str | UUID | None = None,
    external_id: str | None = None,
    principal: Any | None = None,
) -> DocumentModel:
    """Persist a document from raw bytes — the canonical ingestion path.

    Workstream C.1 of the integrations follow-ups pass. Extracted from
    the body of the previous ``upload_document`` so the same storage +
    DB-write logic serves both the UI ``UploadFile`` path and the
    integration ``pull_documents`` engine path. Also embeds the OCR
    dispatch (best-effort) so callers don't have to duplicate it.

    Args:
        filename: Original filename (used for extension validation +
            displayed in the UI). The on-disk name is a UUID-derived
            safe filename.
        content: Raw bytes — the caller (wrapper or engine) is
            responsible for any size capping before this point.
        content_type: Best-effort MIME (currently informational; the
            extension gate is what actually matters).
        tenant_id: Owning tenant (FK constraint enforced).
        patient_id: Optional patient scope. Required for FHIR
            ``DocumentReference.subject`` round-trip.
        owner_id: The uploading user (becomes ``DocumentModel.owner_id``
            + ``AuditMixin.created_by`` downstream).
        db: Active session. Caller commits via this function.
        examination_id: Optional exam link (FK with CASCADE).
        include_in_extraction: When True, dispatch the ``ocr_document``
            Celery task after the DB write succeeds. Best-effort —
            broker-down failures are logged + swallowed. The caller can
            re-trigger via :func:`trigger_extraction` later.
        category_concept_id: Optional catalog concept link (e.g.
            "Lab Report", "Imaging"). The engine resolves this from
            ``DocumentPull.category_concept_slug`` before calling.
        source_integration_id: Optional integration provenance + dedup
            key (item 3 of integrations-sdk-improvements plan). When
            supplied together with ``external_id``, the service looks
            up an existing document by
            ``(tenant, patient, integration, external_id)`` and returns
            it as-is — no duplicate file write, no re-OCR dispatch.
            UI uploads leave both unset and bypass dedup.
        external_id: Optional upstream stable document id. See
            ``source_integration_id`` above.
        principal: Optional acting principal (a scoped integration/PAT
            ``TokenData``). When supplied, the write is refused unless it
            holds ``document:write`` — the ingest/bridge seams that bypass
            the route gate pass it so a read-only credential cannot write.

    Returns:
        The persisted :class:`DocumentModel` (refreshed + ready to
        serialize). On a dedup hit, the existing row is returned without
        any side effects (no file write, no OCR dispatch).
    """
    import logging

    from app.core.authz.permissions import Permission
    from app.services.auth.access import require_principal_permission

    logger = logging.getLogger(__name__)

    if principal is not None:
        require_principal_permission(principal, Permission.DOCUMENT_WRITE)

    # Integration-key dedup (item 3 of integrations-sdk-improvements plan).
    # Mirrors the pattern on examination_service + clinical_event_service:
    # when both keys are supplied, look up the existing row by the
    # natural key and return it. The partial unique index
    # ``uq_document_integration_dedup`` catches the race window between
    # this SELECT and the INSERT below.
    if source_integration_id is not None and external_id is not None:
        existing = await _find_document_by_integration_key(
            db,
            tenant_id=tenant_id,
            patient_id=patient_id,
            source_integration_id=source_integration_id,
            external_id=external_id,
        )
        if existing is not None:
            logger.info(
                "Document dedup hit for integration=%s external_id=%s "
                "→ returning existing document %s (no re-OCR)",
                source_integration_id,
                external_id,
                existing.id,
            )
            return existing

    doc_id = uuid4()
    _validate_upload_extension(filename)
    object_key = document_key(tenant_id, doc_id, filename)

    document = DocumentModel(
        id=doc_id,
        filename=filename,
        # The wire never sees this; the facade dual-reads it for rows written
        # before object storage. New rows keep no server path at all.
        file_path="",
        object_key=object_key,
        owner_id=owner_id,
        tenant_id=tenant_id,
        patient_id=UUID(str(patient_id)) if patient_id else None,
        examination_id=UUID(str(examination_id)) if examination_id else None,
        category_concept_id=(UUID(str(category_concept_id)) if category_concept_id else None),
        include_in_extraction=include_in_extraction,
        status="uploaded",
        progress=0,
        updated_at=datetime.now(),
    )

    storage = DocumentStorage()
    try:
        await storage.aput(document, content, key=object_key)
    except ObjectTooLarge as e:
        raise HTTPException(
            status_code=413, detail="File too large for the configured storage limit."
        ) from e
    except StorageError as e:
        logger.error("Failed to store uploaded document: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="Document storage is unavailable") from e
    except Exception as e:
        logger.error("Failed to store uploaded document: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save uploaded file") from e
    if source_integration_id is not None:
        document.source_integration_id = UUID(str(source_integration_id))
    if external_id is not None:
        document.external_id = str(external_id)

    practitioner_id = await _resolve_practitioner_id(db, owner_id, tenant_id)
    if practitioner_id is not None:
        document.practitioner_id = practitioner_id

    assert_valid_fhir(document)
    db.add(document)
    try:
        await db.commit()
    except IntegrityError:
        # Race window: a concurrent sync beat won the INSERT against
        # the same dedup key. Roll back + re-fetch the winner rather
        # than surfacing the error. Mirrors examination_service.
        await db.rollback()
        existing = await _find_document_by_integration_key(
            db,
            tenant_id=tenant_id,
            patient_id=patient_id,
            source_integration_id=source_integration_id,
            external_id=external_id,
        )
        if existing is not None:
            logger.info(
                "Document dedup race recovered for integration=%s "
                "external_id=%s → returning document %s",
                source_integration_id,
                external_id,
                existing.id,
            )
            # The losing row's object is an orphan; provider lifecycle is the
            # net, but remove it eagerly when the store is reachable.
            with contextlib.suppress(Exception):
                await storage.adelete(document)
            return existing
        raise
    except Exception:
        with contextlib.suppress(Exception):
            await storage.adelete(document)
        raise
    await db.refresh(document)

    if include_in_extraction:
        try:
            from app.workers.ai_tasks import ocr_document

            cast(Any, ocr_document).apply_async(
                args=[
                    str(document.id),
                    document_object_key(document),
                    str(document.tenant_id),
                    str(document.owner_id),
                ]
            )
        except Exception as e:
            # Broker-down: the document is safely persisted; the user can
            # re-trigger extraction from the UI via trigger_extraction.
            # (The previous endpoint-layer fallback to BackgroundTasks
            # was UI-only and doesn't translate to the engine path; the
            # UX regression is intentional + documented.)
            logger.warning(
                "Could not dispatch ocr_document for %s: %s",
                document.id,
                e,
            )

    return document


async def _find_document_by_integration_key(
    db: AsyncSession,
    *,
    tenant_id: str | UUID,
    patient_id: str | UUID | None,
    source_integration_id: str | UUID,
    external_id: str,
) -> DocumentModel | None:
    """Look up an existing integration-sourced document by exact dedup key.

    Item 3 of the integrations-sdk-improvements plan. Mirrors
    ``examination_service._find_by_integration_key``. The partial unique
    index ``uq_document_integration_dedup`` makes this lookup fast; in
    the race window between the SELECT and the subsequent INSERT, the
    index also catches duplicates at the DB layer.
    """
    stmt = select(DocumentModel).where(
        DocumentModel.tenant_id == tenant_id,
        DocumentModel.source_integration_id == source_integration_id,
        DocumentModel.external_id == external_id,
    )
    if patient_id is not None:
        stmt = stmt.where(DocumentModel.patient_id == patient_id)
    else:
        stmt = stmt.where(DocumentModel.patient_id.is_(None))
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_document(
    document_id: str, db: AsyncSession, tenant_id: str | UUID | None = None
) -> DocumentModel | None:
    """Get document by ID from database.

    When ``tenant_id`` is provided the query is scoped to that tenant (audit
    A10) so a caller cannot read a cross-tenant document even if it forgets to
    re-check. Callers that legitimately need cross-tenant reads (e.g. the
    presigned-token download path, which authorises separately) may omit it.
    """
    stmt = select(DocumentModel).where(DocumentModel.id == document_id)
    if tenant_id is not None:
        stmt = stmt.where(DocumentModel.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def enrich_document_entities(doc_dict: dict, db: AsyncSession):
    """Enrich document entities JSON with matched biomarker IDs from Observations table"""
    from app.models.fhir.patient import Observation

    if not doc_dict.get("id") or not doc_dict.get("entities"):
        return doc_dict

    # Fetch observations linked to this document
    obs_result = await db.execute(
        select(Observation).where(Observation.document_id == UUID(doc_dict["id"]))
    )
    observations = obs_result.scalars().all()

    if not observations:
        return doc_dict

    # Create map: name -> biomarker_id
    obs_map = {
        obs.code.get("text", "").lower(): str(obs.biomarker_id)
        for obs in observations
        if obs.biomarker_id
    }

    entities = doc_dict["entities"]
    if isinstance(entities, dict):
        # Known
        known = entities.get("known_biomarkers", [])
        if isinstance(known, list):
            for b in known:
                if isinstance(b, dict):
                    b_id = obs_map.get(b.get("name", "").lower())
                    if b_id:
                        b["biomarker_id"] = b_id
        # Unknown
        unknown = entities.get("unknown_biomarkers", [])
        if isinstance(unknown, list):
            for b in unknown:
                if isinstance(b, dict):
                    b_id = obs_map.get(b.get("raw_name", "").lower())
                    if b_id:
                        b["biomarker_id"] = b_id

    return doc_dict


async def get_documents(
    tenant_id: str,
    owner_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession | None = None,
    patient_ids: set | None = None,
) -> list[DocumentModel]:
    """Get documents with optional filtering (hides originals if edited versions exist).

    ``patient_ids`` (the grant-aware reachable set) ORs with ``owner_id``:
    documents of reachable patients plus the caller's unattached uploads.
    """
    from uuid import UUID

    from sqlalchemy import not_

    # Convert tenant_id to UUID
    tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id

    if db is None:
        return []

    # Subquery to identify all parent_ids that have been edited
    parent_ids_subquery = select(DocumentModel.parent_id).where(
        DocumentModel.tenant_id == tenant_uuid,
        DocumentModel.parent_id.isnot(None),
    )

    base_query = select(DocumentModel).where(
        DocumentModel.tenant_id == tenant_uuid,
        not_(DocumentModel.id.in_(parent_ids_subquery)),
    )

    if patient_ids is not None:
        if owner_id:
            from uuid import UUID as UUIDType

            owner_uuid = UUIDType(owner_id) if isinstance(owner_id, str) else owner_id
            if patient_ids:
                base_query = base_query.where(
                    or_(
                        DocumentModel.patient_id.in_(patient_ids),
                        DocumentModel.owner_id == owner_uuid,
                    )
                )
            else:
                base_query = base_query.where(DocumentModel.owner_id == owner_uuid)
        else:
            base_query = base_query.where(
                DocumentModel.patient_id.in_(patient_ids) if patient_ids else false()
            )
    elif owner_id:
        from uuid import UUID as UUIDType

        owner_uuid = UUIDType(owner_id) if isinstance(owner_id, str) else owner_id
        base_query = base_query.where(DocumentModel.owner_id == owner_uuid)

    result = await db.execute(
        base_query.order_by(DocumentModel.updated_at.desc()).limit(limit).offset(offset)
    )

    documents = list(result.scalars().all())
    return documents


async def update_document(
    document_id: str, document_update: dict, db: AsyncSession
) -> DocumentModel | None:
    """Update document properties"""
    document = await get_document(document_id, db)
    if not document:
        return None

    for key, value in document_update.items():
        setattr(document, key, value)

    await db.commit()
    await db.refresh(document)
    return document


async def download_document(document: DocumentModel) -> str:
    """Return the document's storage key (never a host path)."""
    return document_object_key(document)


async def trigger_extraction(document_id: str, db: AsyncSession) -> str:
    """Trigger document extraction (OCR)"""
    document = await get_document(document_id, db)

    if not document:
        raise ValueError(f"Document {document_id} not found")

    cast(Any, document).status = "processing"
    cast(Any, document).progress = 10
    cast(Any, document).error_message = None
    await db.commit()

    # Trigger Celery task (OCR only)
    try:
        from app.workers.ai_tasks import ocr_document

        cast(Any, ocr_document).apply_async(
            args=[
                str(document.id),
                await astorage_key_or_legacy(document),
                str(document.tenant_id),
                str(document.owner_id),
            ]
        )
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Could not trigger Celery task: {e}")

    return f"ocr-{document_id}"


async def trigger_full_examination_extraction(examination_id: str, db: AsyncSession) -> str:
    """Trigger OCR for all included documents in an examination, followed by LLM analysis"""
    from app.models.document_model import DocumentModel
    from app.models.examination_model import ExaminationModel

    result = await db.execute(select(ExaminationModel).where(ExaminationModel.id == examination_id))
    exam = result.scalar_one_or_none()
    if not exam:
        raise ValueError(f"Examination {examination_id} not found")

    # Get all included documents
    doc_result = await db.execute(
        select(DocumentModel).where(
            DocumentModel.examination_id == UUID(examination_id),
            DocumentModel.include_in_extraction.is_(True),
        )
    )
    docs = doc_result.scalars().all()

    if not docs:
        raise ValueError("No documents included in extraction for this examination")

    # Mark examination as processing
    exam.extraction_status = "processing"
    exam.extraction_progress = 5
    exam.error_message = None
    await db.commit()

    # Trigger OCR for all included docs
    # This will naturally lead to cumulative_extraction being triggered as each doc finishes
    for doc in docs:
        await trigger_extraction(str(doc.id), db)

    return f"full-extraction-{examination_id}"


async def trigger_cumulative_extraction(examination_id: str, db: AsyncSession) -> str:
    """Trigger cumulative extraction for an examination"""
    from app.models.examination_model import ExaminationModel

    result = await db.execute(select(ExaminationModel).where(ExaminationModel.id == examination_id))
    exam = result.scalar_one_or_none()
    if not exam:
        raise ValueError(f"Examination {examination_id} not found")

    exam.extraction_status = "processing"
    exam.extraction_progress = 10
    exam.error_message = None
    await db.commit()

    try:
        from app.workers.ai_tasks import cumulative_extraction

        # Use the owner of the first included document or similar as the user context
        # In a multi-user exam, we assume the person triggering it wants their config used
        # For simplicity, we use owner_id from the exam if it exists (not currently in model)
        # So we'll fetch one of the docs' owner

        doc_res = await db.execute(
            select(DocumentModel.owner_id)
            .where(DocumentModel.examination_id == UUID(examination_id))
            .limit(1)
        )
        user_id = doc_res.scalar()

        cast(Any, cumulative_extraction).apply_async(
            args=[str(examination_id), str(user_id) if user_id else None]
        )
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Could not trigger Cumulative task: {e}")

    return f"cumulative-{examination_id}"


async def update_document_status(
    document_id: str,
    status: str,
    progress: int,
    extracted_text: str | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Update document processing status"""
    if db is None:
        raise ValueError("Database session is required")

    document = await get_document(document_id, db)

    if document:
        cast(Any, document).status = status
        cast(Any, document).progress = progress
        if extracted_text:
            cast(Any, document).extracted_text = extracted_text
        await db.commit()


async def edit_document_service(
    document_id: str,
    edit_params: dict,
    db: AsyncSession,
) -> DocumentModel:
    """Edit a document and save as a new version"""
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"edit_document_service called for {document_id} with params: {edit_params}")

    original = await get_document(document_id, db)
    if not original:
        raise HTTPException(status_code=404, detail="Document not found")

    # Only images are supported for now
    file_extension = Path(original.filename).suffix.lower()
    if file_extension not in [".jpg", ".jpeg", ".png", ".bmp"]:
        raise HTTPException(
            status_code=400,
            detail=f"Editing not supported for {file_extension} files. Only images (JPG, PNG, BMP) are currently supported.",
        )

    new_doc_id = uuid4()
    new_object_key = document_key(original.tenant_id, new_doc_id, original.filename)

    # Prepare crop tuple
    crop = None
    if all(
        edit_params.get(k) is not None
        for k in ["crop_left", "crop_top", "crop_right", "crop_bottom"]
    ):
        crop = (
            int(edit_params["crop_left"]),
            int(edit_params["crop_top"]),
            int(edit_params["crop_right"]),
            int(edit_params["crop_bottom"]),
        )
        logger.info(f"Prepared crop tuple: {crop}")

    # Perform edit. The source is materialized from the store (or the legacy
    # local file) and the result is written back through the store — the
    # temporary paths never outlive this call.
    storage = DocumentStorage()

    def _render(src: Path, dest: Path) -> None:
        edit_image(
            str(src),
            str(dest),
            crop=crop,
            perspective_points=edit_params.get("perspective_points"),
            brightness=float(edit_params.get("brightness", 1.0)),
            contrast=float(edit_params.get("contrast", 1.0)),
            sharpness=float(edit_params.get("sharpness", 1.0)),
            rotation=int(edit_params.get("rotation", 0)),
        )

    try:
        with tempfile.TemporaryDirectory(prefix="ha-edit-") as workdir:
            data = await anyio.to_thread.run_sync(
                _render_document_edit, workdir, _render, storage, original
            )
    except Exception as e:
        logger.error(f"Failed to edit image: {e}")
        raise HTTPException(status_code=500, detail="Failed to edit image")

    # Create database record
    new_document = DocumentModel(
        id=new_doc_id,
        filename=f"edited_{original.filename}",
        file_path="",
        object_key=new_object_key,
        owner_id=original.owner_id,
        tenant_id=original.tenant_id,
        patient_id=original.patient_id,
        examination_id=original.examination_id,
        include_in_extraction=original.include_in_extraction,
        status="uploaded",
        progress=0,
        parent_id=original.id,
        is_edited=True,
        updated_at=datetime.now(),
    )
    try:
        await storage.aput(new_document, data, key=new_object_key)
    except ObjectTooLarge as e:
        raise HTTPException(
            status_code=413, detail="Edited file exceeds the configured storage limit."
        ) from e
    except StorageError as e:
        logger.error("Failed to store edited document: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="Document storage is unavailable") from e

    # Deactivate original in extraction if it was active
    if original.include_in_extraction:
        original.include_in_extraction = False

    # F11: carry the resolved Practitioner id from the original so the
    # edited copy also emits a resolvable DocumentReference.author.
    new_document.practitioner_id = original.practitioner_id

    # FHIR validation gate (audit: write-time gate coverage). Verifies the
    # edited-copy DocumentModel projects to a valid DocumentReference.
    try:
        assert_valid_fhir(new_document)
        db.add(new_document)
        await db.commit()
    except Exception:
        # The object was written before the row. Remove it so a validation or
        # commit failure cannot leave orphaned PHI behind the database.
        with contextlib.suppress(Exception):
            await storage.adelete(new_document)
        raise
    await db.refresh(new_document)

    # Trigger OCR for new document if extraction is enabled
    if new_document.include_in_extraction:
        from app.services.documents.document_service import trigger_extraction

        await trigger_extraction(str(new_document.id), db)

    logger.info(f"New edited document created: {new_document.id}")
    return new_document


async def delete_document(
    document_id: str,
    db: AsyncSession,
    current_user: TokenData,
    *,
    trigger_cumulative: bool = True,
) -> bool:
    """Delete a document and its extracted observations.

    Takes the request actor so the scoped-credential write guard runs at the
    same seam as the upload: the ingest/bridge paths that call this directly
    never run the route gate, so a read-only PAT/integration principal must be
    refused here (ADR 0054 D8).
    """
    from app.core.authz.permissions import Permission
    from app.services.auth.access import require_principal_permission

    require_principal_permission(current_user, Permission.DOCUMENT_WRITE)

    document = await get_document(document_id, db)

    if not document:
        return False

    # If this is an edited version, check if we should restore the parent's extraction status
    if document.is_edited and document.parent_id:
        parent = await get_document(str(document.parent_id), db)
        if parent and document.include_in_extraction:
            parent.include_in_extraction = True

    # Remove the bytes through the storage seam (object first, then the
    # legacy local file). A provider failure must not wedge the DB delete;
    # it is logged and the lifecycle rule / migration sweep is the net.
    try:
        await DocumentStorage().adelete(document)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "Failed to delete stored document %s: %s", document.id, e
        )

    examination_id = document.examination_id

    # Also delete associated FHIR Observations if they were extracted.
    # ORM delete (audit fix): the previous raw `text("... document_id = :doc_id::uuid")`
    # silently failed under asyncpg — SQLAlchemy's `text()` bindparam regex
    # `:name(?!:)` skips a name immediately followed by `:` (the `::uuid`
    # Postgres cast), so the SQL reached asyncpg as a literal, hit a syntax
    # error on the `:`, poisoned the transaction, and the subsequent
    # `await db.delete(document)` aborted with InFailedSQLTransactionError.
    from sqlalchemy import delete

    from app.models.fhir import Observation

    try:
        await db.execute(delete(Observation).where(Observation.document_id == document.id))
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"Failed to clean up associated FHIR data: {e}")

    await db.delete(document)
    await db.commit()

    # Re-trigger cumulative if it was part of an exam and requested
    if examination_id and trigger_cumulative:
        with contextlib.suppress(Exception):
            await trigger_cumulative_extraction(str(examination_id), db)

    return True
