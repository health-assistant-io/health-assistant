# ruff: noqa: B904,E501 -- long immutable strings; reflow when touched
import logging
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.roles import is_unscoped_system_admin
from app.core.database import get_db
from app.core.errors import AuthorizationError, NotFoundError
from app.core.security import get_current_user
from app.models.enums import Role
from app.models.patient_access_grant import GrantPermission
from app.schemas.document import DocumentEdit, DocumentResponse, DocumentUpdate
from app.schemas.user import TokenData
from app.services.audit.audit_service import audit_read, log_audit_action
from app.services.auth.access import accessible_patient_ids, check_patient_access
from app.services.documents.document_service import (
    delete_document,
    get_document,
    get_documents,
    trigger_extraction,
    update_document,
    upload_document,
)

# Top-level workers import keeps the load order stable: documents.py is
# imported before examinations.py by the v1 router, and importing the
# workers module here forces ``app.workers.ai_tasks`` to load before
# ``app.ai.pipeline.service`` finishes initializing (otherwise a circular
# import fires). ``ocr_document`` is the task the service layer dispatches
# inside ``ingest_document_bytes`` — keeping the symbol at module scope
# documents the dependency even though the dispatch itself lives in the
# service.
from app.workers.ai_tasks import ocr_document  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


async def _require_document_patient_access(
    document,
    current_user: TokenData,
    db: AsyncSession,
    required: GrantPermission = GrantPermission.READ,
    action: str = "view",
    deny_status: int = 403,
) -> None:
    """ADR 0054 D5 — document visibility rides patient access, not owner_id.

    The tenant boundary was already enforced by ``get_document``
    (SYSTEM_ADMIN passes ``None``). Restricted roles (USER; a MANAGER under
    ``manager_scope=assigned``) must reach the document's patient at
    ``required`` level through :func:`check_patient_access`; a document not
    attached to a patient stays uploader-only. ``deny_status=404`` hides
    existence on file-render surfaces.
    """
    if document.patient_id:
        try:
            await check_patient_access(document.patient_id, current_user, db, required)
            return
        except NotFoundError:
            if document.owner_id and str(document.owner_id) == str(current_user.user_id):
                return
            raise HTTPException(
                status_code=deny_status, detail=f"Not authorized to {action} this document"
            )
        except AuthorizationError:
            raise HTTPException(
                status_code=deny_status, detail=f"Not authorized to {action} this document"
            )
    # A scoped credential naming SYSTEM_ADMIN keeps tenant reach (capped by
    # ADR 0054 D8), so it does not inherit the operator's tenant-wide
    # shortcut on patient-less documents: uploader-only, like any other
    # restricted principal.
    is_tenant_wide = is_unscoped_system_admin(current_user) or current_user.role in [
        Role.ADMIN.value,
        Role.MANAGER.value,
    ]
    if not is_tenant_wide and str(document.owner_id) != str(current_user.user_id):
        raise HTTPException(
            status_code=deny_status, detail=f"Not authorized to {action} this document"
        )


async def _validate_presign_claims(
    claims: dict,
    document,
    db: AsyncSession,
) -> None:
    """S8 — re-verify presign claims against the loaded document and user.

    The token proves issuance (signature, kind, ``doc_id`` binding, TTL);
    authorization is re-derived here on every download/preview: the
    ``tenant_id`` claim must match the document's tenant, and the
    ``user_id`` claim must resolve to a live, active user — so a token
    minted by one tenant never serves another tenant's file, and
    deactivating or deleting a user kills their outstanding tokens within
    the TTL window. Tokens missing either claim (minted before S8) are
    invalid.
    """
    token_tenant = claims.get("tenant_id")
    token_user = claims.get("user_id")
    if not token_tenant or not token_user:
        raise HTTPException(status_code=401, detail="Invalid or expired download token")
    if str(document.tenant_id) != str(token_tenant):
        raise HTTPException(status_code=401, detail="Invalid or expired download token")
    from sqlalchemy import select

    from app.models.user_model import UserModel

    try:
        user_pk = UUID(str(token_user))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid or expired download token")
    user = (
        await db.execute(
            select(UserModel).where(UserModel.id == user_pk, UserModel.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired download token")


@router.post("")
async def upload_document_endpoint(
    file: UploadFile = File(...),
    patient_id: str = Form(None),
    examination_id: str = Form(None),
    include_in_extraction: bool = Form(False),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a medical document"""
    from sqlalchemy import select

    from app.models.user_model import UserModel

    user_id = current_user.user_id

    # Get user and tenant from database
    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    tenant_id = user.tenant_id
    user_uuid = user_id
    tenant_uuid = tenant_id

    if patient_id:
        from app.services.auth.access import check_patient_access

        await check_patient_access(patient_id, current_user, db, required=GrantPermission.WRITE)

    if examination_id:
        from app.services.auth.access import check_examination_access

        examination = await check_examination_access(
            examination_id, current_user, db, required=GrantPermission.WRITE
        )
        if patient_id and str(examination.patient_id) != str(patient_id):
            raise HTTPException(
                status_code=400, detail="Examination does not belong to the given patient"
            )

    # Validate tenant exists (use raw SQL to avoid selecting non-existent columns)
    from sqlalchemy import text

    result = await db.execute(
        text("SELECT id, name, settings FROM tenants WHERE id = :tenant_id"),
        {"tenant_id": tenant_id},
    )
    tenant = result.fetchone()

    if not tenant:
        raise HTTPException(status_code=400, detail="Tenant not found")

    # Convert include_in_extraction if it comes as a string from FormData
    if isinstance(include_in_extraction, str):
        include_in_extraction = include_in_extraction.lower() == "true"

    # Create document. ``upload_document`` reads the UploadFile in capped
    # chunks (audit A4), delegates to ``ingest_document_bytes`` (workstream
    # C.1) which writes the file + DB row + dispatches the OCR Celery task
    # best-effort. The previous inline OCR dispatch + broker-down fallback
    # to ``BackgroundTasks.process_document_sync`` moved into the service
    # layer so engine/webhook paths get OCR for free without duplicating
    # the dispatch logic. UX regression: broker-down no longer triggers
    # sync background processing; user can re-trigger from the UI.
    document = await upload_document(
        file,
        patient_id,
        user_uuid,
        str(tenant_uuid),
        db,
        examination_id,
        include_in_extraction,
    )

    logger.info(
        f"Document created: {document.id}, include_in_extraction: {include_in_extraction} (type: {type(include_in_extraction)})"
    )

    # §17: document (record) writes are audited (plan 16 H2).
    await log_audit_action(
        required=True,
        tenant_id=str(tenant_uuid) if tenant_uuid else None,
        user_id=user_uuid,
        action="create_document",
        resource_type="Document",
        resource_id=getattr(document, "id", None),
        new_value={
            "filename": file.filename,
            "patient_id": patient_id,
            "examination_id": examination_id,
            "include_in_extraction": bool(include_in_extraction),
        },
    )

    return document.to_dict()


@router.get("")
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all documents"""
    user_id = current_user.user_id
    tenant_id_str = str(current_user.tenant_id)

    # ADR 0054 D5: restricted roles see documents of reachable patients
    # (own + grants) plus their own unattached uploads; tenant-wide roles
    # see the tenant's documents.
    reachable = await accessible_patient_ids(current_user, db)
    owner_id = None
    patient_scope = None
    if reachable is not None:
        patient_scope = reachable
        owner_id = str(user_id)
    elif current_user.role not in [
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ]:
        owner_id = str(user_id)

    documents = await get_documents(
        tenant_id=tenant_id_str,
        owner_id=owner_id,
        limit=limit,
        offset=offset,
        db=db,
        patient_ids=patient_scope,
    )

    from app.services.documents.document_service import enrich_document_entities

    return [await enrich_document_entities(doc.to_dict(), db) for doc in documents]


@router.get("/{document_id}")
@audit_read("Document", id_param="document_id")
async def get_document_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document information"""
    document = await get_document(
        document_id,
        db,
        None if is_unscoped_system_admin(current_user) else current_user.tenant_id,
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await _require_document_patient_access(document, current_user, db, action="view")

    from app.services.documents.document_service import enrich_document_entities

    return await enrich_document_entities(document.to_dict(), db)


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document_endpoint(
    document_id: str,
    document_update: DocumentUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update document"""
    document = await get_document(
        document_id,
        db,
        None if is_unscoped_system_admin(current_user) else current_user.tenant_id,
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await _require_document_patient_access(
        document, current_user, db, required=GrantPermission.WRITE, action="update"
    )

    prior_include = document.include_in_extraction
    updated_document = await update_document(
        document_id, document_update.model_dump(exclude_unset=True), db
    )
    if not updated_document:
        raise HTTPException(status_code=404, detail="Document not found")

    # If inclusion changed or text was updated, re-trigger cumulative extraction
    if (
        updated_document.include_in_extraction != prior_include
        or document_update.extracted_text is not None
    ) and updated_document.examination_id:
        # If we just enabled extraction and it hasn't been OCR'd yet (or failed), trigger OCR first
        if updated_document.include_in_extraction and updated_document.status in [
            "uploaded",
            "failed",
        ]:
            from app.services.documents.document_service import trigger_extraction

            await trigger_extraction(str(updated_document.id), db)
        else:
            from app.services.documents.document_service import trigger_cumulative_extraction

            await trigger_cumulative_extraction(str(updated_document.examination_id), db)

    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="update_document",
        resource_type="Document",
        resource_id=document_id,
        new_value=document_update.model_dump(mode="json", exclude_unset=True),
    )
    return updated_document.to_dict()


@router.post("/{document_id}/edit", response_model=DocumentResponse)
async def edit_document_endpoint(
    document_id: str,
    edit_params: DocumentEdit,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Apply image edits (crop, brightness, contrast) to a document"""
    document = await get_document(
        document_id,
        db,
        None if is_unscoped_system_admin(current_user) else current_user.tenant_id,
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await _require_document_patient_access(
        document, current_user, db, required=GrantPermission.WRITE, action="edit"
    )

    from app.services.documents.document_service import edit_document_service

    new_document = await edit_document_service(document_id, edit_params.model_dump(), db)

    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="edit_document",
        resource_type="Document",
        resource_id=document_id,
        new_value=edit_params.model_dump(mode="json"),
    )

    return new_document.to_dict()


@router.get("/{document_id}/presign")
async def get_presigned_url_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a short-lived token to safely download a document without passing JWT"""
    # API-H5 (audit 2026-08): tenant-scoped fetch. Previously the admin
    # short-circuit skipped BOTH the owner check and any tenant filter, so
    # a tenant ADMIN/MANAGER could presign (and download) another tenant's
    # document by UUID.
    document = await get_document(
        document_id,
        db,
        None if is_unscoped_system_admin(current_user) else current_user.tenant_id,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await _require_document_patient_access(document, current_user, db, action="presign")

    from app.core.security import create_presigned_token

    token = create_presigned_token(
        document_id,
        user_id=str(current_user.user_id),
        tenant_id=str(document.tenant_id),
    )
    return {"url": f"/api/v1/documents/{document_id}/download?token={token}"}


@router.get("/{document_id}/download")
async def download_document_endpoint(
    document_id: str,
    token: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Download document file"""
    if not token:
        raise HTTPException(status_code=401, detail="Missing presigned token")

    from app.core.security import verify_presigned_token
    from app.core.tenant_context import set_tenant as set_tenant_context

    claims = verify_presigned_token(token, document_id)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired download token")

    # RLS context (ADR 0054 Phase 5): the download route holds no session,
    # so the signature-verified token's tenant claim is the context — the
    # document query below fails closed unless the row belongs to it.
    token_tenant = claims.get("tenant_id")
    if not token_tenant:
        raise HTTPException(status_code=401, detail="Invalid or expired download token")
    set_tenant_context(token_tenant, actor=str(claims.get("user_id") or ""))

    document = await get_document(document_id, db)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # S8: tenant + live-user re-verification of the token's claims.
    await _validate_presign_claims(claims, document, db)

    import mimetypes

    from fastapi.responses import RedirectResponse, StreamingResponse

    from app.storage.documents import PHI_PRESIGN_TTL_SECONDS, DocumentStorage
    from app.storage.object_storage import ObjectNotFound, StorageError, UnsupportedOperation

    # Try to guess the media type to render it inline instead of downloading
    content_type, _ = mimetypes.guess_type(str(document.filename))
    if not content_type:
        content_type = "application/octet-stream"

    # Audit A3: never serve active-content types inline (stored-XSS at the app
    # origin). The upload allowlist already blocks svg/html/xml, but defence-in-
    # depth forces an attachment for those extensions regardless. All other
    # types get X-Content-Type-Options: nosniff so browsers don't MIME-sniff a
    # benign extension into something executable.
    from app.services.documents.document_service import should_serve_inline

    serve_inline = should_serve_inline(document.filename)
    storage = DocumentStorage()
    key = storage.object_key(document)
    download_name = str(document.filename or "document")

    # Record access at mint time: the token has already been re-validated
    # (tenant + live user), and the bytes are about to leave the app either
    # through a short-lived provider URL or a streamed response.
    try:
        from app.storage.object_storage import safe_download_filename

        attachment = safe_download_filename(download_name)
        disposition = (
            f'inline; filename="{attachment}"'
            if serve_inline
            else f'attachment; filename="{attachment}"'
        )
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Disposition": disposition,
        }
        if serve_inline:
            # Inline files are meant to be previewed in a same-origin
            # <iframe> (see AuthenticatedPdf / PdfViewer). The default
            # X-Frame-Options: DENY set by security_headers_middleware would
            # block that, so relax to SAMEORIGIN only for previewable inline
            # content.
            headers["X-Frame-Options"] = "SAMEORIGIN"

        # Existence is a store call too; a transient provider failure must
        # answer 503 (not a misleading 404).
        if not await storage.aexists(document):
            raise ObjectNotFound(key)

        await log_audit_action(
            required=True,
            tenant_id=UUID(str(document.tenant_id)),
            user_id=UUID(str(claims["user_id"])),
            action="record_access",
            resource_type="Document",
            resource_id=document.id,
            new_value={
                "surface": "download",
                "delivery": "stream" if serve_inline else "presign",
                "phi_presign_ttl_s": PHI_PRESIGN_TTL_SECONDS,
            },
        )

        # Inline responses are streamed by the app: a provider presigned URL
        # is always an attachment + no-store, and PDF/text inline previews
        # need the app's headers. Presign only true attachment downloads.
        if not serve_inline:
            try:
                url = await storage.apresign_key(
                    key,
                    download_filename=download_name,
                    inline=False,
                )
            except UnsupportedOperation:
                url = None
            if url:
                return RedirectResponse(url=url, status_code=307, headers=headers)

        return StreamingResponse(
            storage.astream(document),
            media_type=content_type,
            headers=headers,
        )
    except ObjectNotFound:
        raise HTTPException(status_code=404, detail="Document file not found")
    except StorageError as exc:
        logger.error("Document download storage failure: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Document storage is unavailable") from exc


@router.post("/{document_id}/extract")
async def trigger_extraction_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger document extraction"""
    try:
        document = await get_document(
            document_id,
            db,
            None if is_unscoped_system_admin(current_user) else current_user.tenant_id,
        )
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        await _require_document_patient_access(
            document, current_user, db, required=GrantPermission.WRITE, action="extract"
        )

        job_id = await trigger_extraction(document_id, db)
        return {"job_id": job_id, "message": "Extraction started"}
    except HTTPException:
        raise
    except Exception:
        # Re-raise so the global handler returns a generic 500 + correlation
        # id. Never surface the raw exception (may leak internals/PHI).
        logger.exception("Document extraction trigger failed")
        raise


@router.get("/{document_id}/extract/status")
async def get_extraction_status_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document extraction status"""
    document = await get_document(
        document_id,
        db,
        None if is_unscoped_system_admin(current_user) else current_user.tenant_id,
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await _require_document_patient_access(document, current_user, db, action="view")

    error_message = document.error_message
    if error_message and current_user.role not in (
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ):
        error_message = (
            "Processing failed. Try again or contact your administrator."
            if document.status == "failed"
            else None
        )

    return {
        "status": document.status,
        "progress": document.progress,
        "error_message": error_message,
    }


@router.post("/preview-temp")
async def upload_temp_preview(
    file: UploadFile = File(...),
    page: int = 0,
    current_user: TokenData = Depends(get_current_user),
):
    """Temporary upload for previewing DICOM/PDF before saving examination. Supports multiple pages/frames via 'page' query param."""
    import os
    import uuid
    from pathlib import Path

    from fastapi.responses import Response

    from app.ai.processors.ocr.utils import convert_to_images

    # Security: check extension
    filename = file.filename or "temp"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".dcm", ".pdf", ".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(status_code=400, detail="Unsupported file type for preview")

    # Create temp directory if not exists
    temp_dir = Path("/tmp/health_assistant_previews")
    temp_dir.mkdir(parents=True, exist_ok=True)

    temp_path = temp_dir / f"preview_{uuid.uuid4()}{ext}"

    try:
        # Save temp file — cap size to prevent RAM exhaustion (audit A4)
        from app.core.config import settings as _settings
        from app.services.documents.document_service import _read_capped

        content = await _read_capped(file, _settings.MAX_UPLOAD_SIZE * 1024 * 1024)
        with open(temp_path, "wb") as buffer:
            buffer.write(content)

        # Convert to image
        images = await convert_to_images(temp_path)

        # Clean up original temp file
        if temp_path.exists():
            os.remove(temp_path)

        if not images:
            raise HTTPException(status_code=500, detail="Failed to process preview")

        # Ensure page index is within bounds
        if page < 0 or page >= len(images):
            page = 0

        # Return requested frame
        requested_frame = page if 0 <= page < len(images) else 0

        # We also return total frames in a header
        headers = {
            "X-Total-Pages": str(len(images)),
            "X-Current-Page": str(requested_frame),
        }
        return Response(content=images[requested_frame], media_type="image/jpeg", headers=headers)
    except Exception:
        if temp_path.exists():
            os.remove(temp_path)
        logger.exception("Temp document preview generation failed")
        raise


@router.get("/{document_id}/dicom-metadata")
async def get_dicom_metadata_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get metadata for a DICOM document"""
    # API-H5 (audit 2026-08): tenant-scoped fetch (see presign).
    document = await get_document(
        document_id,
        db,
        None if is_unscoped_system_admin(current_user) else current_user.tenant_id,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await _require_document_patient_access(document, current_user, db, action="view")

    if not document.filename.lower().endswith(".dcm"):
        raise HTTPException(status_code=400, detail="Document is not a DICOM file")

    import pydicom

    from app.storage.documents import DocumentStorage
    from app.storage.object_storage import ObjectNotFound

    try:
        storage = DocumentStorage()
        async with storage.amaterialize(document) as file_path:
            ds = pydicom.dcmread(str(file_path), stop_before_pixels=True)

            metadata = {}
            # Basic common DICOM tags
            tags = {
                "PatientName": "Patient Name",
                "PatientID": "Patient ID",
                "PatientBirthDate": "Birth Date",
                "PatientSex": "Sex",
                "StudyDate": "Study Date",
                "StudyDescription": "Study Description",
                "SeriesDescription": "Series Description",
                "Modality": "Modality",
                "Manufacturer": "Manufacturer",
                "InstitutionName": "Institution",
                "BodyPartExamined": "Body Part",
                "ProtocolName": "Protocol",
                "KVP": "kVp",
                "ExposureTime": "Exposure Time (ms)",
                "XRayTubeCurrent": "Tube Current (mA)",
                "Exposure": "Exposure (mAs)",
                "SliceThickness": "Slice Thickness (mm)",
                "PixelSpacing": "Pixel Spacing (mm)",
                "WindowCenter": "Window Center",
                "WindowWidth": "Window Width",
            }

            for tag, label in tags.items():
                if hasattr(ds, tag):
                    val = getattr(ds, tag)
                    metadata[tag] = {"label": label, "value": str(val)}

            return metadata
    except ObjectNotFound:
        raise HTTPException(status_code=404, detail="DICOM file not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to read DICOM metadata")
        raise


@router.get("/{document_id}/preview")
async def get_document_preview_endpoint(
    request: Request,
    document_id: str,
    page: int = 0,
    token: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get an image preview of a document.

    Converts DICOM/PDF to image. Supports multiple pages/frames via ``page``
    query param.

    Auth (audit item B5 — previously this endpoint had no auth at all when
    ``?token=`` was omitted): a caller MUST present either

    1. a valid presigned ``?token=...`` (short-lived JWT bound to this
       ``document_id`` — used by ``<img src="...">`` tags in the frontend
       that cannot send an ``Authorization`` header), OR
    2. a valid ``Authorization: Bearer <jwt>`` for the document's tenant
       (used by JSON-fetch clients like the AI assistant that already
       carry the user's session).

    A request with neither credential → 401. A request with a valid
    credential that doesn't match the document's tenant → 404 (no
    information leak that the row exists in another tenant).
    """
    from app.core.security import authenticate_session_token, verify_presigned_token

    authenticated_tenant_id = None
    presign_claims: dict | None = None

    if token:
        # Presigned-token path. ``verify_presigned_token`` checks the JWT
        # signature, the ``token_kind == "download"`` claim, the ``doc_id``
        # match, and the ``exp`` window; the ``tenant_id``/``user_id``
        # claims are re-verified against the loaded document and live user
        # below (S8).
        presign_claims = verify_presigned_token(token, document_id)
        if not presign_claims:
            raise HTTPException(status_code=401, detail="Invalid or expired preview token")
        if not presign_claims.get("tenant_id"):
            raise HTTPException(status_code=401, detail="Invalid or expired preview token")
        # RLS context (ADR 0054 Phase 5): the presigned path holds no
        # session — the token's tenant claim (signature-verified above)
        # is the context for the document query below.
        from app.core.tenant_context import set_tenant as set_tenant_context

        set_tenant_context(
            presign_claims["tenant_id"], actor=str(presign_claims.get("user_id") or "")
        )
    else:
        # No presigned token → require an Authorization: Bearer <jwt>.
        authorization = request.headers.get("authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail="Authentication required: provide a presigned token or a Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        bearer = authorization[len("Bearer ") :]
        # Session tokens only, verified against the live user row
        # (is_active + ver — identity-auth §8/§18.9).
        token_data = await authenticate_session_token(bearer)
        # SYSTEM_ADMIN can preview any document (operator role); other
        # roles are constrained to their own tenant below.
        if token_data.role != Role.SYSTEM_ADMIN.value:
            authenticated_tenant_id = token_data.tenant_id

    document = await get_document(document_id, db)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # S8: presigned tokens re-verify tenant + live-user on every render.
    if presign_claims is not None:
        await _validate_presign_claims(presign_claims, document, db)

    # Tenant enforcement for the authenticated-session path. (Presigned
    # tokens re-verified their tenant/user claims above.)
    if authenticated_tenant_id is not None:
        try:
            doc_tenant = UUID(str(document.tenant_id))
            caller_tenant = UUID(str(authenticated_tenant_id))
        except (ValueError, TypeError):
            raise HTTPException(status_code=403, detail="Tenant mismatch")
        if doc_tenant != caller_tenant:
            # 404 (not 403) so we don't leak that the doc exists in another tenant.
            raise HTTPException(status_code=404, detail="Document not found")

        # API-H4 (audit 2026-08): tenant match alone is not enough for
        # restricted roles — a tenant member must not render other members'
        # documents. Mirrors the gate on GET /documents/{id}, grant-aware
        # per ADR 0054 D5 (owner_id shortcuts are gone); file-render
        # surfaces hide existence with 404.
        await _require_document_patient_access(
            document,
            token_data,
            db,
            action="view",
            deny_status=404,
        )

    from fastapi.responses import Response

    from app.ai.processors.ocr.utils import convert_to_images
    from app.storage.documents import DocumentStorage
    from app.storage.object_storage import ObjectNotFound

    storage = DocumentStorage()

    # If it's already an image, serve it from the store (legacy local rows
    # dual-read through the same facade).
    if document.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
        import mimetypes

        content_type, _ = mimetypes.guess_type(document.filename)
        try:
            data = await storage.aread(document)
        except ObjectNotFound:
            raise HTTPException(status_code=404, detail="File not found")
        if data is None:
            raise HTTPException(status_code=404, detail="File not found")
        return Response(
            content=data,
            media_type=content_type or "image/jpeg",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    # For DICOM and PDF, convert to an image list. The object is materialized
    # into a private temp file for the duration of the conversion only.
    try:
        async with storage.amaterialize(document) as file_path:
            images = await convert_to_images(file_path)
        if not images:
            raise HTTPException(status_code=500, detail="Failed to generate preview image")

        # Ensure page index is within bounds
        if page < 0 or page >= len(images):
            page = 0

        return Response(
            content=images[page],
            media_type="image/jpeg",
            headers={
                "X-Total-Pages": str(len(images)),
                "X-Current-Page": str(page),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ObjectNotFound:
        raise HTTPException(status_code=404, detail="File not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Document preview generation failed")
        raise


@router.delete("/{document_id}")
async def delete_document_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document"""
    document = await get_document(
        document_id,
        db,
        None if is_unscoped_system_admin(current_user) else current_user.tenant_id,
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await _require_document_patient_access(
        document, current_user, db, required=GrantPermission.WRITE, action="delete"
    )

    success = await delete_document(document_id, db, current_user)

    if success:
        await log_audit_action(
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="delete_document",
            resource_type="Document",
            resource_id=document_id,
            old_value={"filename": str(document.filename)},  # gate-allow: *, audit diff
        )
        return {"message": "Document deleted successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete document")
