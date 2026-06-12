from sqlalchemy import Column, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin


class AlertModel(Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin):
    __tablename__ = "alerts"
    
    patient_id = Column(UUID(as_uuid=True), ForeignKey("fhir_patients.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(100), nullable=False)
    threshold = Column(Float, nullable=True)
    enabled = Column(Boolean, default=True)
    last_triggered = Column(DateTime(timezone=True), nullable=True)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "patient_id": self.patient_id,
            "type": self.type,
            "threshold": self.threshold,
            "enabled": self.enabled,
            "last_triggered": self.last_triggered.isoformat() if self.last_triggered else None,
        }
