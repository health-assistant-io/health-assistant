from sqlalchemy import Column, String, Float, ForeignKey, Integer, Enum, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.models.base import (
    Base,
    UUIDMixin,
    AuditMixin,
    TenantMixin,
    VersionedMixin,
    TimestampMixin,
)
from app.models.enums import QuantityType, CodingSystem


class Unit(Base, UUIDMixin, AuditMixin, TimestampMixin):
    __tablename__ = "units"

    symbol = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    quantity_type = Column(
        Enum(QuantityType), nullable=False, default=QuantityType.OTHER
    )
    base_unit_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversion_multiplier = Column(Float, nullable=False, default=1.0)
    dashboard_config = Column(JSONB, nullable=True)

    # Relationships
    base_unit = relationship("Unit", remote_side="[Unit.id]")


class BiomarkerDefinition(Base, UUIDMixin, AuditMixin, TimestampMixin, VersionedMixin):
    __tablename__ = "biomarker_definitions"

    slug = Column(String(255), unique=True, nullable=False, index=True)
    coding_system = Column(Enum(CodingSystem), nullable=False, default=CodingSystem.LOINC)
    code = Column(String(100), nullable=True)
    name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=True)
    preferred_unit_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )
    aliases = Column(JSONB, nullable=False, default=list)  # List of strings
    description = Column(Text, nullable=True)
    info = Column(Text, nullable=True)
    reference_range_min = Column(Float, nullable=True)
    reference_range_max = Column(Float, nullable=True)
    is_telemetry = Column(Boolean, nullable=False, default=False)
    meta_data = Column(JSONB, nullable=True)
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )  # Optional tenant override

    # Relationships
    preferred_unit = relationship("Unit", lazy="selectin")


class BiomarkerGroup(Base, UUIDMixin, AuditMixin, TimestampMixin, TenantMixin):
    __tablename__ = "biomarker_groups"

    name = Column(String(255), nullable=False)
    type = Column(String(100), nullable=True)  # e.g., "Panel"
    display_order = Column(Integer, default=0)
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Relationships
    members = relationship(
        "BiomarkerGroupMember", back_populates="group", cascade="all, delete-orphan"
    )


class BiomarkerGroupMember(Base, UUIDMixin, AuditMixin, TimestampMixin):
    __tablename__ = "biomarker_group_members"

    group_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    biomarker_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_order = Column(Integer, default=0)

    # Relationships
    group = relationship("BiomarkerGroup", back_populates="members")
    biomarker = relationship("BiomarkerDefinition")


class BiomarkerRelationship(Base, UUIDMixin, AuditMixin, TimestampMixin):
    __tablename__ = "biomarker_relationships"

    source_biomarker_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_biomarker_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type = Column(String(100), nullable=False)

    # Relationships
    source = relationship("BiomarkerDefinition", foreign_keys=[source_biomarker_id])
    target = relationship("BiomarkerDefinition", foreign_keys=[target_biomarker_id])


class Laboratory(Base, UUIDMixin, AuditMixin, TimestampMixin, TenantMixin):
    __tablename__ = "laboratories"

    name = Column(String(255), nullable=False)
    location = Column(String(255), nullable=True)
    standard_rating = Column(Float, nullable=True)
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )


class BiomarkerEventCorrelation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "biomarker_event_correlations"

    biomarker_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_event_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    correlation_type = Column(String(100), nullable=True)  # e.g., "diagnostic", "monitoring"
    description = Column(Text, nullable=True)

    # Relationships
    biomarker = relationship("BiomarkerDefinition", backref="event_correlations")
    # Using string reference to avoid circular import
    event_type = relationship("ClinicalEventType", backref="biomarker_correlations")
