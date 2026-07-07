from sqlalchemy import (
    Column,
    String,
    ForeignKey,
    Integer,
    Text,
    DateTime,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    TimestampMixin,
)
from app.models.enums import ExportScope, ExportType, JobStatus


def _enum_values(enum_cls):
    return [e.value for e in enum_cls]


class ExportJobModel(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    __tablename__ = "export_jobs"

    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope = Column(SQLEnum(ExportScope, values_callable=_enum_values), nullable=False)
    export_type = Column(
        SQLEnum(ExportType, values_callable=_enum_values), nullable=False
    )
    status = Column(
        SQLEnum(JobStatus, values_callable=_enum_values),
        default=JobStatus.PENDING,
        nullable=False,
    )

    progress = Column(Integer, default=0, nullable=False)
    patient_ids = Column(JSONB, nullable=True)

    file_path = Column(Text, nullable=True)
    manifest_path = Column(Text, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    resource_counts = Column(JSONB, nullable=True)
    smart_scope = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "scope": self.scope.value if self.scope else None,
            "export_type": self.export_type.value if self.export_type else None,
            "status": self.status.value if self.status else None,
            "progress": self.progress,
            "patient_ids": self.patient_ids,
            "file_path": self.file_path,
            "manifest_path": self.manifest_path,
            "file_size_bytes": self.file_size_bytes,
            "resource_counts": self.resource_counts,
            "smart_scope": self.smart_scope,
            "error_message": self.error_message,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ImportJobModel(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    __tablename__ = "import_jobs"

    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_filename = Column(String(255), nullable=True)
    status = Column(
        SQLEnum(JobStatus, values_callable=_enum_values),
        default=JobStatus.PENDING,
        nullable=False,
    )

    progress = Column(Integer, default=0, nullable=False)
    total_records = Column(Integer, default=0, nullable=False)
    processed_records = Column(Integer, default=0, nullable=False)
    failed_records = Column(Integer, default=0, nullable=False)

    restore_result = Column(JSONB, nullable=True)
    errors = Column(JSONB, nullable=True)
    warnings = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "source_filename": self.source_filename,
            "status": self.status.value if self.status else None,
            "progress": self.progress,
            "total_records": self.total_records,
            "processed_records": self.processed_records,
            "failed_records": self.failed_records,
            "restore_result": self.restore_result,
            "errors": self.errors,
            "warnings": self.warnings,
            "error_message": self.error_message,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
