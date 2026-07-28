from sqlalchemy import Column, String, Float, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy import text

from app.models.base import Base, TenantMixin, AuditMixin, VersionedMixin


class TelemetryDataModel(Base, TenantMixin, AuditMixin, VersionedMixin):
    """Long-format telemetry hypertable (TimescaleDB).

    One row per ``(timestamp, device, slug)`` — the canonical TimescaleDB /
    InfluxDB / Prometheus pattern for variable-schema metrics. Adding a new
    telemetry biomarker is a row-only change: flip
    ``BiomarkerDefinition.is_telemetry = True`` and the integration sync
    starts emitting rows here with no DDL or service-layer branching. See
    ``docs/TELEMETRY_AND_AGGREGATION.md`` and migration
    ``t1e2l3o4n5g6_telemetry_long_format``.
    """

    __tablename__ = "telemetry_data"

    # Override TenantMixin.tenant_id WITHOUT a foreign key. TimescaleDB
    # hypertables do not reliably support FK constraints (chunk-based
    # partitioning breaks referential integrity checks). A periodic cleanup
    # job is responsible for purging telemetry rows after their tenant is
    # deleted.
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)

    # Composite Primary Key (required by TimescaleDB for hypertables).
    id = Column(
        PG_UUID(as_uuid=True), primary_key=True, default=text("gen_random_uuid()")
    )
    timestamp = Column(DateTime(timezone=True), primary_key=True, index=True)

    device_id = Column(String(255), nullable=False, index=True)
    # Biomarker slug (e.g. ``heart-rate``, ``steps``, ``spo2``, ``glucose``).
    slug = Column(String(255), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(64), nullable=True)

    # Persisted patient attribution, populated at insert by the integration
    # pipeline. Optional — older/legacy rows may be NULL. No FK (TimescaleDB
    # hypertable limitation); same cleanup-job contract as ``tenant_id``.
    patient_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)

    __table_args__ = (
        # Hot path: tenant + metric + time range (analytics_service).
        Index(
            "ix_telemetry_data_tenant_slug_ts",
            "tenant_id",
            "slug",
            "timestamp",
        ),
        # Per-device timeline (telemetry_service reads).
        Index(
            "ix_telemetry_data_tenant_device_ts",
            "tenant_id",
            "device_id",
            "timestamp",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "slug": self.slug,
            "value": self.value,
            "unit": self.unit,
            "patient_id": self.patient_id,
        }
