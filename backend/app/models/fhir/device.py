"""FHIR R4B Device resource model.

Audit item C9: a Device resource represents a telemetry source (wearable,
CGM, etc.). Today ``telemetry_data.device_id`` is a free-text column with
no Device resource backing it. This model gives each integration a
canonical Device resource.

Spec: https://hl7.org/fhir/R4/device.html

TelemetryDataModel.device_id references Device.id by **convention** — no
FK constraint on the TimescaleDB hypertable (FKs on hypertables are
problematic).
"""
from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.services.fhir_helpers import build_fhir_resource, build_meta, fhir_isoformat


class DeviceModel(
    Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin, SoftDeleteMixin
):
    """A FHIR Device resource representing a telemetry source."""

    __tablename__ = "fhir_devices"

    identifier = Column(JSONB, nullable=True)  # [{system, value}]
    device_name = Column(JSONB, nullable=True)  # [{name, type}]
    type = Column(JSONB, nullable=True)  # CodeableConcept
    manufacturer = Column(String(255), nullable=True)
    model_number = Column(String(255), nullable=True)
    serial_number = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default="active")  # active|inactive|entered-in-error|unknown
    owner_integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "identifier": self.identifier,
            "device_name": self.device_name,
            "type": self.type,
            "manufacturer": self.manufacturer,
            "model_number": self.model_number,
            "serial_number": self.serial_number,
            "status": self.status,
            "owner_integration_id": str(self.owner_integration_id) if self.owner_integration_id else None,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B Device resource via fhir.resources (validated)."""
        data = {
            "resourceType": "Device",
            "id": str(self.id) if self.id else None,
            "identifier": self.identifier,
            "deviceName": self.device_name,
            "type": self.type,
            "manufacturer": self.manufacturer,
            "modelNumber": self.model_number,
            "serialNumber": self.serial_number,
            "status": self.status,
            "meta": build_meta(str(self.id) if self.id else None),
        }
        if self.patient_id:
            data["patient"] = {"reference": f"Patient/{self.patient_id}"}
        if self.owner_integration_id:
            data["owner"] = {"reference": f"Integration/{self.owner_integration_id}"}
        return build_fhir_resource("Device", data)
