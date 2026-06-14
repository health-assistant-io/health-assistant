from sqlalchemy import Column, String, Float, DateTime, UUID, text
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base, TenantMixin, AuditMixin, VersionedMixin


class TelemetryDataModel(Base, TenantMixin, AuditMixin, VersionedMixin):
    __tablename__ = "telemetry_data"

    # Composite Primary Key (Required by TimescaleDB for hypertables)
    id = Column(UUID(as_uuid=True), primary_key=True, default=text("gen_random_uuid()"))
    timestamp = Column(DateTime(timezone=True), primary_key=True, index=True)

    device_id = Column(String(255), nullable=False, index=True)
    data = Column(JSONB, nullable=True)

    # Optional specific columns for common metrics to facilitate analysis
    heart_rate = Column(Float, nullable=True)
    steps = Column(Float, nullable=True)
    calories = Column(Float, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "data": self.data,
            "heart_rate": self.heart_rate,
            "steps": self.steps,
            "calories": self.calories,
        }
