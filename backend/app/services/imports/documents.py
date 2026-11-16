"""Document restore from export bundles."""

import logging

from app.services.imports.types import (
    UUID,
    Any,
    DocumentModel,
    _uuid,
    uuid4,
    zipfile,
)

logger = logging.getLogger(__name__)


class DocumentsMixin:
    # ---------------- document restore ----------------

    async def restore_documents(
        self,
        documents_meta: list[dict[str, Any]],
        archive: zipfile.ZipFile | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        owner_id: UUID,
    ) -> int:
        from pathlib import PurePosixPath

        from app.services.documents.document_service import (
            ALLOWED_UPLOAD_EXTENSIONS,
        )
        from app.storage.documents import DocumentStorage, document_key

        storage = DocumentStorage()
        count = 0
        for meta in documents_meta:
            try:
                pid = meta.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                archive_path = meta.get("_archive_path")

                # C-3 (audit 2026-08): the filename's extension is
                # validated against the same allowlist as interactive
                # uploads, and anything path-like is stripped. A crafted
                # documents.json previously let the "extension" contain
                # '../' traversal → arbitrary file write.
                raw_filename = str(meta.get("filename") or "")
                ext = PurePosixPath(raw_filename).suffix.lower()
                if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                    ext = ".bin"
                doc_id = uuid4()
                object_key = document_key(tenant_id, doc_id, f"restored{ext}")
                data = None
                if archive and archive_path:
                    try:
                        data = archive.read(archive_path)
                    except KeyError:
                        data = None

                doc = DocumentModel(
                    id=doc_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    patient_id=_uuid(pid),
                    filename=meta.get("filename") or "imported",
                    file_path="",
                    object_key=object_key if data is not None else None,
                    status=meta.get("status") or "uploaded",
                    extracted_text=meta.get("extracted_text"),
                    entities=meta.get("entities"),
                    include_in_extraction=bool(meta.get("include_in_extraction", False)),
                    is_edited=bool(meta.get("is_edited", False)),
                )
                if data is not None:
                    await storage.aput(doc, data, key=object_key)
                self.db.add(doc)
                count += 1
            except Exception as e:
                logger.warning(f"document skipped: {e}")
        await self.db.flush()
        return count
