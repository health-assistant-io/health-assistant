from sqlalchemy import Column, String, Integer, ForeignKey, Text, Index, Boolean
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy import text as sa_text
from sqlalchemy.orm import relationship
from app.models.base import (
    Base,
    UUIDMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.services.fhir_helpers import build_fhir_resource, build_meta, fhir_isoformat
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    pass


class DocumentModel(
    Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin, SoftDeleteMixin
):
    __tablename__ = "documents"

    # Use UUID for id to match database schema
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    filename = Column(String(255), nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    owner_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
    )
    category_concept_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    examination_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("examinations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status = Column(String(50), default="uploaded", index=True)
    progress = Column(Integer, default=0)
    extracted_text = Column(Text, nullable=True)
    entities = Column(JSONB, nullable=True)
    include_in_extraction = Column(Boolean, default=False, nullable=False)
    error_message = Column(Text, nullable=True)
    parent_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    is_edited = Column(Boolean, default=False, nullable=False)
    # F11: resolved Practitioner (DoctorModel) id for FHIR
    # DocumentReference.author. Backfilled from owner_id at migration time;
    # set on new uploads via owner→doctor lookup. Nullable because not every
    # owner is a practitioner (some are admins/managers without a doctor row).
    # When NULL, to_fhir_dict() omits the `author` element rather than emit
    # a wrong `Practitioner/<owner_id>` (owner_id is a User FK, not a Doctor
    # FK — emitting it caused external clients to 404 on resolution).
    practitioner_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # updated_at is now handled by TimestampMixin

    # Relationships
    category_concept = relationship(
        "Concept",
        foreign_keys="[DocumentModel.category_concept_id]",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_doc_tenant_owner", "tenant_id", "owner_id"),
        # GIN index on entities JSONB for filtered queries
        # (analytics_service queries entities["document_category"]).
        Index(
            "ix_documents_entities_gin",
            sa_text("entities"),
            postgresql_using="gin",
        ),
    )

    def to_dict(self) -> dict:
        # Type-safe datetime conversion
        updated_at_value = self.updated_at
        created_at_value = getattr(self, "created_at", None)

        return {
            "id": str(self.id) if self.id else None,
            "filename": self.filename,
            "file_path": self.file_path,
            "owner_id": str(self.owner_id) if self.owner_id else None,
            "practitioner_id": str(self.practitioner_id)
            if getattr(self, "practitioner_id", None)
            else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "category_concept_id": str(self.category_concept_id)
            if getattr(self, "category_concept_id", None)
            else None,
            "examination_id": str(self.examination_id)
            if getattr(self, "examination_id", None)
            else None,
            "status": self.status,
            "progress": self.progress,
            "error_message": self.error_message,
            "include_in_extraction": self.include_in_extraction,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "is_edited": self.is_edited,
            "extracted_text": self.extracted_text,
            "entities": self.entities,
            "created_at": created_at_value.isoformat()
            if created_at_value is not None
            else (updated_at_value.isoformat() if updated_at_value else None),
            "updated_at": updated_at_value.isoformat()
            if updated_at_value is not None
            else None,
        }

    def to_fhir_dict(self) -> dict:
        """Project this DocumentModel to a FHIR R4B DocumentReference resource.

        The DocumentReference is metadata-only — binary content lives in the
        app's file storage and is referenced via ``content[].attachment.url``
        using a relative path. External systems fetching the binary must use
        the app's separate download endpoint (NOT the FHIR facade); the FHIR
        Binary resource pattern is out of scope for this v1 facade.

        Maps:
        - status → DocumentReference.status (default ``current``)
        - doc_status → DocumentReference.docStatus (draft|preliminary|final|...)
          derived from status column (e.g. ``uploaded`` → ``preliminary``)
        - patient_id → DocumentReference.subject
        - examination_id → DocumentReference.context.encounter
        - filename → content[0].attachment.title
        - file_path → content[0].attachment.url (relative)
        - owner_id → author
        - created_at → context.period.start + date
        - extracted_text → content[0].attachment.data if include_in_extraction

        Audit item C14: DocumentModel gains a FHIR projection so the facade
        can expose it at ``/fhir/R4/DocumentReference``. The previous
        ad-hoc _document_to_document_reference in export_service.py should
        be replaced by a call to this method (Phase 10 cleanup).
        """
        # Map app status → DocumentReference.status (always 'current' unless
        # the doc was deleted/superseded).
        dr_status = "current"
        if self.status in ("deleted", "archived"):
            dr_status = (
                "superseded" if self.status == "archived" else "entered-in-error"
            )

        # docStatus: limited enum (preliminary|final|amended|entered-in-error).
        # The app status vocabulary is broader; map the common ones.
        status_to_doc = {
            "uploaded": "preliminary",
            "processing": "preliminary",
            "extracted": "final",
            "completed": "final",
            "failed": "entered-in-error",
        }
        doc_status = status_to_doc.get(self.status or "", "preliminary")

        # Attachment: metadata-only. URL is a relative path the frontend
        # resolves against the app's storage root.
        attachment = {
            "contentType": "application/octet-stream",
            "title": self.filename or "Untitled",
        }
        if self.file_path:
            attachment["url"] = f"urn:ha-document:{self.id}"

        data = {
            "resourceType": "DocumentReference",
            "id": str(self.id) if self.id else None,
            "status": dr_status,
            "docStatus": doc_status,
            "content": [{"attachment": attachment}],
            "meta": build_meta(str(self.id) if self.id else None),
        }

        if self.patient_id:
            data["subject"] = {"reference": f"Patient/{self.patient_id}"}
        # F11: emit Practitioner/<practitioner_id> when we have a resolved
        # Practitioner; otherwise omit `author` entirely rather than emit a
        # wrong `Practitioner/<owner_id>` (owner_id is a User FK — would 404).
        if self.practitioner_id:
            data["author"] = [{"reference": f"Practitioner/{self.practitioner_id}"}]
        if self.examination_id:
            data["context"] = {
                "encounter": [{"reference": f"Encounter/{self.examination_id}"}]
            }
        if self.created_at:
            ts = fhir_isoformat(self.created_at)
            if "context" not in data:
                data["context"] = {}
            data["context"]["period"] = {"start": ts}

        return build_fhir_resource("DocumentReference", data)
