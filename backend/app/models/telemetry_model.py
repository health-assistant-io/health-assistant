from sqlalchemy import Column, String, Float, DateTime, UUID, text, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from app.models.base import Base, TenantMixin, AuditMixin, VersionedMixin


class TelemetryDataModel(Base, TenantMixin, AuditMixin, VersionedMixin):
    __tablename__ = "telemetry_data"

    # Override tenant_id WITHOUT a foreign key. TimescaleDB hypertables do
    # not reliably support FK constraints (chunk-based partitioning breaks
    # referential integrity checks). A periodic cleanup job is responsible
    # for purging telemetry rows after their tenant is deleted.
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)

    # Composite Primary Key (Required by TimescaleDB for hypertables)
    id = Column(UUID(as_uuid=True), primary_key=True, default=text("gen_random_uuid()"))
    timestamp = Column(DateTime(timezone=True), primary_key=True, index=True)

    device_id = Column(String(255), nullable=False, index=True)
    data = Column(JSONB, nullable=True)

    # Optional specific columns for common metrics to facilitate analysis
    heart_rate = Column(Float, nullable=True)
    steps = Column(Float, nullable=True)
    calories = Column(Float, nullable=True)

    __table_args__ = (
        # Composite index for tenant-wide analytics queries that filter by
        # time range (analytics_service, telemetry_service).
        Index("ix_telemetry_data_tenant_timestamp", "tenant_id", "timestamp"),
    )

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
