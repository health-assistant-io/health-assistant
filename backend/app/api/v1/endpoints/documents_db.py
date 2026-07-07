from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    UploadFile,
    File,
    BackgroundTasks,
    Form,
)
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Optional, cast
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.document_service_db import (
    upload_document,
    get_document,
    get_documents,
    trigger_extraction,
    delete_document,
    update_document,
)
from app.workers.ai_tasks import process_document_sync
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

from app.models.enums import Role
from app.schemas.user import TokenData

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("")
async def upload_document_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    patient_id: str = Form(None),
    examination_id: str = Form(None),
    include_in_extraction: bool = Form(False),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a medical document"""
    from app.models.user_model import UserModel
    from sqlalchemy import select

    user_id = current_user.user_id

    # Get user and tenant from database
    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    tenant_id = user.tenant_id
    user_uuid = user_id
    tenant_uuid = tenant_id

    # Validate tenant exists (use raw SQL to avoid selecting non-existent columns)
    from sqlalchemy import text

    result = await db.execute(
        text("SELECT id, name, settings FROM tenants WHERE id = :tenant_id"),
        {"tenant_id": tenant_id},
    )
    tenant = result.fetchone()

    if not tenant:
        raise HTTPException(status_code=400, detail=f"Tenant not found: {tenant_id}")

    # Convert include_in_extraction if it comes as a string from FormData
    if isinstance(include_in_extraction, str):
        include_in_extraction = include_in_extraction.lower() == "true"

    # Create document
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

    # Trigger async processing (OCR) only if requested or if it's a new upload that needs basic indexing
    # If the user specifically said NOT to include in extraction, we can skip OCR for now to save resources
    # and avoid "processing" status confusion.
    if include_in_extraction:
        try:
            from app.workers.ai_tasks import ocr_document

            logger.info(f"Triggering OCR task for document {document.id}")
            task = cast(Any, ocr_document).apply_async(
                args=[str(document.id), str(document.file_path), str(tenant_uuid)]
            )
            logger.info(f"OCR task triggered: {task.id}")
        except Exception as e:
            logger.warning(f"Celery task not started (Redis unavailable): {e}")
            logger.info("Falling back to synchronous background processing.")
            if background_tasks:
                background_tasks.add_task(
                    process_document_sync,
                    str(document.id),
                    document.file_path,
                    str(tenant_uuid),
                )
    else:
        logger.info(
            f"Skipping OCR task for document {document.id} as include_in_extraction is False"
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
    tenant_id = current_user.tenant_id
    role = current_user.role

    # Admins can see all documents in tenant, users only see their own
    owner_id = (
        None
        if current_user.role
        in [Role.ADMIN.value, Role.MANAGER.value, Role.SYSTEM_ADMIN.value]
        else str(user_id)
    )

    # Convert tenant_id to string if it's a UUID object
    tenant_id_str = str(tenant_id)

    documents = await get_documents(
        tenant_id=tenant_id_str, owner_id=owner_id, limit=limit, offset=offset, db=db
    )

    from app.services.document_service_db import enrich_document_entities

    return [await enrich_document_entities(doc.to_dict(), db) for doc in documents]


@router.get("/{document_id}")
async def get_document_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document information"""
    document = await get_document(document_id, db)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check permissions
    user_id = current_user.user_id
    role = current_user.role

    is_admin = current_user.role in [
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ]
    if not is_admin and str(document.owner_id) != str(user_id):
        raise HTTPException(
            status_code=403, detail="Not authorized to view this document"
        )

    from app.services.document_service_db import enrich_document_entities

    return await enrich_document_entities(document.to_dict(), db)


from app.schemas.document import DocumentUpdate, DocumentResponse, DocumentEdit


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document_endpoint(
    document_id: str,
    document_update: DocumentUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update document"""
    document = await get_document(document_id, db)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    user_id = current_user.user_id
    role = current_user.role

    if role not in [
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ] and str(document.owner_id) != str(user_id):
        raise HTTPException(
            status_code=403, detail="Not authorized to update this document"
        )

    old_include = document.include_in_extraction
    updated_document = await update_document(
        document_id, document_update.model_dump(exclude_unset=True), db
    )
    if not updated_document:
        raise HTTPException(status_code=404, detail="Document not found")

    # If inclusion changed or text was updated, re-trigger cumulative extraction
    if (
        updated_document.include_in_extraction != old_include
        or document_update.extracted_text is not None
    ) and updated_document.examination_id:
        # If we just enabled extraction and it hasn't been OCR'd yet (or failed), trigger OCR first
        if updated_document.include_in_extraction and updated_document.status in [
            "uploaded",
            "failed",
        ]:
            from app.services.document_service_db import trigger_extraction

            await trigger_extraction(str(updated_document.id), db)
        else:
            from app.services.document_service_db import trigger_cumulative_extraction

            await trigger_cumulative_extraction(
                str(updated_document.examination_id), db
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
    document = await get_document(document_id, db)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check permissions
    user_id = current_user.user_id
    role = current_user.role

    if role not in [
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ] and str(document.owner_id) != str(user_id):
        raise HTTPException(
            status_code=403, detail="Not authorized to edit this document"
        )

    from app.services.document_service_db import edit_document_service

    new_document = await edit_document_service(
        document_id, edit_params.model_dump(), db
    )

    return new_document.to_dict()


@router.get("/{document_id}/presign")
async def get_presigned_url_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a short-lived token to safely download a document without passing JWT"""
    document = await get_document(document_id, db)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    user_id = current_user.user_id
    role = current_user.role

    is_admin = current_user.role in [
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ]
    if not is_admin and str(document.owner_id) != str(user_id):
        raise HTTPException(
            status_code=403, detail="Not authorized to view this document"
        )

    from app.core.security import create_presigned_token

    token = create_presigned_token(document_id)
    return {"url": f"/api/v1/documents/{document_id}/download?token={token}"}


@router.get("/{document_id}/download")
async def download_document_endpoint(
    document_id: str,
    token: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Download document file"""
    if not token:
        raise HTTPException(status_code=401, detail="Missing presigned token")

    from app.core.security import verify_presigned_token

    if not verify_presigned_token(token, document_id):
        raise HTTPException(status_code=401, detail="Invalid or expired download token")

    document = await get_document(document_id, db)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    import mimetypes
    from fastapi.responses import FileResponse

    # Try to guess the media type to render it inline instead of downloading
    content_type, _ = mimetypes.guess_type(str(document.filename))
    if not content_type:
        content_type = "application/octet-stream"

    return FileResponse(
        str(document.file_path),
        filename=str(document.filename),
        media_type=content_type,
        content_disposition_type="inline",  # Crucial to view in browser instead of forcing download
    )


@router.post("/{document_id}/extract")
async def trigger_extraction_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger document extraction"""
    try:
        document = await get_document(document_id, db)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        user_id = current_user.user_id
        role = current_user.role

        if role not in [
            Role.ADMIN.value,
            Role.MANAGER.value,
            Role.SYSTEM_ADMIN.value,
        ] and str(document.owner_id) != str(user_id):
            raise HTTPException(
                status_code=403, detail="Not authorized to extract this document"
            )

        job_id = await trigger_extraction(document_id, db)
        return {"job_id": job_id, "message": "Extraction started"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Extraction error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")


@router.get("/{document_id}/extract/status")
async def get_extraction_status_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document extraction status"""
    document = await get_document(document_id, db)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check permissions
    user_id = current_user.user_id
    role = current_user.role

    # Convert to string for comparison (handle both UUID and string types)
    doc_owner_id = str(document.owner_id) if document.owner_id is not None else None
    current_user_id = str(user_id) if user_id else None

    logger.info(
        f"Checking permissions: user={current_user_id}, role={role}, doc_owner={doc_owner_id}"
    )

    if (
        role not in [Role.ADMIN.value, Role.MANAGER.value, Role.SYSTEM_ADMIN.value]
        and doc_owner_id != current_user_id
    ):
        logger.warning(
            f"Permission denied: user {current_user_id} (role: {role}) tried to access document owned by {doc_owner_id}"
        )
        raise HTTPException(
            status_code=403, detail="Not authorized to view extraction status"
        )

    logger.info(f"Permission granted: user {current_user_id} accessing their document")

    return {
        "status": document.status,
        "progress": document.progress,
        "error_message": document.error_message,
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
    from app.ai.processors.ocr.utils import convert_to_images
    from fastapi.responses import Response
    from pathlib import Path

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
        # Save temp file
        with open(temp_path, "wb") as buffer:
            content = await file.read()
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
        return Response(
            content=images[requested_frame], media_type="image/jpeg", headers=headers
        )
    except Exception as e:
        if temp_path.exists():
            os.remove(temp_path)
        logger.error(f"Temp preview failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}/dicom-metadata")
async def get_dicom_metadata_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get metadata for a DICOM document"""
    document = await get_document(document_id, db)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check permissions
    user_id = current_user.user_id
    role = current_user.role
    is_admin = current_user.role in [
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ]
    if not is_admin and str(document.owner_id) != str(user_id):
        raise HTTPException(
            status_code=403, detail="Not authorized to view this document"
        )

    if not document.filename.lower().endswith(".dcm"):
        raise HTTPException(status_code=400, detail="Document is not a DICOM file")

    import pydicom
    from pathlib import Path

    try:
        file_path = Path(document.file_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="DICOM file not found on disk")

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
    except Exception as e:
        logger.error(f"Failed to read DICOM metadata: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to read DICOM metadata: {str(e)}"
        )


@router.get("/{document_id}/preview")
async def get_document_preview_endpoint(
    request: Request,
    document_id: str,
    page: int = 0,
    token: Optional[str] = None,
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
    from app.core.security import decode_access_token, verify_presigned_token

    authenticated_tenant_id = None

    if token:
        # Presigned-token path. ``verify_presigned_token`` checks the JWT
        # signature, the ``sub == "download"`` claim, the ``doc_id`` match,
        # and the ``exp`` window. Tenant enforcement happened at mint time
        # (the authenticated caller asked for a doc they could already read).
        if not verify_presigned_token(token, document_id):
            raise HTTPException(
                status_code=401, detail="Invalid or expired preview token"
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
        payload = decode_access_token(bearer)
        if not payload:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            token_data = TokenData(**payload)
        except Exception:
            raise HTTPException(
                status_code=401,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # SYSTEM_ADMIN can preview any document (operator role); other
        # roles are constrained to their own tenant below.
        if token_data.role != Role.SYSTEM_ADMIN.value:
            authenticated_tenant_id = token_data.tenant_id

    document = await get_document(document_id, db)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Tenant enforcement for the authenticated-session path. (The presigned
    # path is scoped to the document_id and was minted by an authenticated
    # caller, so the tenant check already happened at mint time.)
    if authenticated_tenant_id is not None:
        try:
            doc_tenant = UUID(str(document.tenant_id))
            caller_tenant = UUID(str(authenticated_tenant_id))
        except (ValueError, TypeError):
            raise HTTPException(status_code=403, detail="Tenant mismatch")
        if doc_tenant != caller_tenant:
            # 404 (not 403) so we don't leak that the doc exists in another tenant.
            raise HTTPException(status_code=404, detail="Document not found")

    from app.ai.processors.ocr.utils import convert_to_images
    from fastapi.responses import Response
    from pathlib import Path

    file_path = Path(document.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # If it's already an image, just serve it
    if document.filename.lower().endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
    ):
        import mimetypes

        content_type, _ = mimetypes.guess_type(document.filename)
        with open(file_path, "rb") as f:
            return Response(content=f.read(), media_type=content_type or "image/jpeg")

    # For DICOM and PDF, convert to image list
    try:
        images = await convert_to_images(file_path)
        if not images:
            raise HTTPException(
                status_code=500, detail="Failed to generate preview image"
            )

        # Ensure page index is within bounds
        if page < 0 or page >= len(images):
            page = 0

        return Response(
            content=images[page],
            media_type="image/jpeg",
            headers={"X-Total-Pages": str(len(images)), "X-Current-Page": str(page)},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Preview generation failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Preview generation failed: {str(e)}"
        )


@router.delete("/{document_id}")
async def delete_document_endpoint(
    document_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document"""
    document = await get_document(document_id, db)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check permissions (only owner or admin can delete)
    user_id = current_user.user_id
    role = current_user.role

    if role not in [
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ] and str(document.owner_id) != str(user_id):
        raise HTTPException(
            status_code=403, detail="Not authorized to delete this document"
        )

    success = await delete_document(document_id, db)

    if success:
        return {"message": "Document deleted successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete document")
