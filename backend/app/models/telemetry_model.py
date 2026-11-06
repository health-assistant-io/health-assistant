"""Telemetry persistence: one hypertable, its device registry and anomalies.

``telemetry_data`` is the long-format TimescaleDB hypertable: one row per
``(timestamp, device, slug)``. Adding a telemetry biomarker stays a row-only
change (flip ``BiomarkerDefinition.is_telemetry``) — no DDL, no per-metric
column, no JSONB catch-all. ``device_id`` is a UUID naming a row in
``telemetry_devices`` (no FK: hypertables cannot enforce referential
integrity, so the tenant/patient cleanup job owns the cascade contract).
``value``/``unit`` store the canonical measurement; ``raw_value``/``raw_unit``
preserve exactly what the source sent, so a conversion-table fix can re-derive
the canonical pair without re-ingesting device data.

``telemetry_devices`` is the row-not-DDL registry of physical sources: a
device is identified by ``(tenant, patient, source, external_ref)`` and new
hardware is an INSERT. ``device_id`` on the hypertable points at it.

``telemetry_anomalies`` is the persisted output of the CAgg-based detection
engine: one row per detected event window, deduplicated on
``(tenant, patient, device, slug, window_start, kind)`` so a re-run updates the
event instead of duplicating it. ``resolved_at`` records recovery.
``payload`` is an open, ``payload_schema_version``-versioned diagnostics
document (baseline statistics), never a query surface.

Retention: ``telemetry_data`` is operational (the ladder's ``drop_chunks``
policy bounds it); ``telemetry_devices`` and ``telemetry_anomalies`` are
operational metadata bounded by the same tenant/patient cleanup contract.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.models.base import (
    AuditMixin,
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
    VersionedMixin,
)
from app.models.enums import TelemetryAnomalyKind


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


class TelemetryDeviceModel(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    """A physical telemetry source (watch, ring, phone, integration instance).

    Identity is ``(tenant_id, patient_id, source, external_ref)``: the same
    hardware bound to two patients is two rows, and two sources may reuse an
    external reference without colliding. ``external_ref`` is the opaque id the
    client sends; ``label`` is the human-readable name.
    """

    __tablename__ = "telemetry_devices"

    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source = Column(String(64), nullable=False)
    external_ref = Column(String(255), nullable=False)
    label = Column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "patient_id",
            "source",
            "external_ref",
            name="uq_telemetry_devices_device_identity",
        ),
    )


class TelemetryDataModel(Base, TenantMixin, AuditMixin, VersionedMixin):
    """Long-format telemetry hypertable (TimescaleDB).

    One row per ``(timestamp, device, slug)`` — the canonical TimescaleDB /
    InfluxDB / Prometheus pattern for variable-schema metrics. Adding a new
    telemetry biomarker is a row-only change: flip
    ``BiomarkerDefinition.is_telemetry = True`` and the integration sync
    starts emitting rows here with no DDL or service-layer branching. See
    ``docs/TELEMETRY_AND_AGGREGATION.md``; the aggregate ladder is created by
    ``0002_telemetry``.

    ``value`` is canonicalized at ingest to the biomarker's preferred unit;
    ``raw_value``/``raw_unit`` keep the source measurement. ``device_id`` is a
    UUID naming ``telemetry_devices`` (no FK — hypertable constraint).

    Retention: operational readings, bounded by the ``drop_chunks`` retention
    policy on the hypertable. Tenant/patient attribution is ``NOT NULL``; the
    hypertable carries no FK constraints, so the tenant-cleanup job purges
    rows whose tenant or patient is gone (constitution rule 5 exception).
    """

    __tablename__ = "telemetry_data"

    # Override TenantMixin.tenant_id WITHOUT a foreign key. TimescaleDB
    # hypertables do not reliably support FK constraints (chunk-based
    # partitioning breaks referential integrity checks). The tenant-cleanup
    # job purges telemetry rows after their tenant is deleted.
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)

    # Composite Primary Key (required by TimescaleDB for hypertables).
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=text("gen_random_uuid()"))
    timestamp = Column(DateTime(timezone=True), primary_key=True, index=True)

    device_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    # Biomarker slug (e.g. ``heart-rate``, ``steps``, ``spo2``, ``glucose``).
    slug = Column(String(255), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(64), nullable=True)
    # Source provenance, kept so conversion-table fixes re-run without ingest.
    raw_value = Column(Float, nullable=False)
    raw_unit = Column(String(64), nullable=True)

    # Persisted patient attribution, populated by the integration pipeline.
    # No FK (hypertable limitation); same cleanup-job contract as tenant_id.
    patient_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)

    __table_args__ = (
        # Hot path: tenant + metric + time range (analytics package).
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
            "raw_value": self.raw_value,
            "raw_unit": self.raw_unit,
            "patient_id": self.patient_id,
        }


class TelemetryAnomalyModel(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    """One persisted anomaly event detected over the CAgg ladder.

    The unique key includes both ``patient_id`` and ``device_id``: two patients
    (or two devices) sharing a slug in one tenant are distinct series, so they
    must never collide and silently drop each other's event. ``kind`` is the
    closed spike/drop vocabulary; ``z_score``/``delta`` are the statistics that
    fired. ``resolved_at`` is set when a later run sees the series recover.
    """

    __tablename__ = "telemetry_anomalies"

    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    slug = Column(String(255), nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    kind = Column(
        SQLEnum(
            TelemetryAnomalyKind,
            name="telemetry_anomaly_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    z_score = Column(Float, nullable=False)
    delta = Column(Float, nullable=False)
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    payload_schema_version = Column(Integer, nullable=False, default=1, server_default=text("1"))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "patient_id",
            "device_id",
            "slug",
            "window_start",
            "kind",
            name="uq_telemetry_anomalies_event",
        ),
        Index(
            "ix_telemetry_anomalies_tenant_patient_slug",
            "tenant_id",
            "patient_id",
            "slug",
        ),
        Index(
            "ix_telemetry_anomalies_tenant_resolved",
            "tenant_id",
            "resolved_at",
        ),
        CheckConstraint("payload_schema_version > 0", name="payload_schema_version_positive"),
    )
