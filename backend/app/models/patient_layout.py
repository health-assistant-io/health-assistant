from sqlalchemy import Column, String, ForeignKey, Boolean, Index, UUID
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin


class PatientLayoutModel(Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin):
    __tablename__ = "patient_layouts"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False, default="Default Layout")
    is_default = Column(Boolean, nullable=False, default=False)

    # Stores the grid layout (lg, md, sm, etc.)
    layout_config = Column(JSONB, nullable=False, default=dict)

    # Stores the state of each card (e.g. which biomarker is selected in a specific card)
    # This allows adding more cards dynamically.
    # Format: [{"id": "card1", "type": "biomarker", "config": {"biomarker": "Glucose"}}, ...]
    cards_config = Column(JSONB, nullable=False, default=list)

    __table_args__ = (Index("idx_layout_user_patient", "user_id", "patient_id"),)

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "patient_id": str(self.patient_id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "is_default": self.is_default,
            "layout_config": self.layout_config,
            "cards_config": self.cards_config,
            "created_at": self.created_at.isoformat()
            if hasattr(self, "created_at") and self.created_at
            else None,
            "updated_at": self.updated_at.isoformat()
            if hasattr(self, "updated_at") and self.updated_at
            else None,
        }
