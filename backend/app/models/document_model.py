from sqlalchemy import Column, String, Integer, ForeignKey, Text, Index, JSON, Boolean
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from app.models.base import Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    pass


class DocumentModel(Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin):
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
        ForeignKey("fhir_patients.id", ondelete="SET NULL"),
        nullable=True,
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
    entities = Column(JSON, nullable=True)
    include_in_extraction = Column(Boolean, default=False, nullable=False)
    error_message = Column(Text, nullable=True)
    parent_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    is_edited = Column(Boolean, default=False, nullable=False)
    # updated_at is now handled by TimestampMixin

    __table_args__ = (Index("idx_doc_tenant_owner", "tenant_id", "owner_id"),)

    def to_dict(self) -> dict:
        # Type-safe datetime conversion
        updated_at_value = self.updated_at
        created_at_value = getattr(self, "created_at", None)

        return {
            "id": str(self.id) if self.id else None,
            "filename": self.filename,
            "file_path": self.file_path,
            "owner_id": str(self.owner_id) if self.owner_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "patient_id": str(self.patient_id) if self.patient_id else None,
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
