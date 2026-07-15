from sqlalchemy import Column, String, Float, ForeignKey, Enum, Text, Boolean, CheckConstraint
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
from app.models.enums import QuantityType, CodingSystem, CatalogScope


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

    __table_args__ = (
        CheckConstraint(
            "conversion_multiplier > 0", name="ck_units_positive_conversion_multiplier"
        ),
    )


class BiomarkerDefinition(Base, UUIDMixin, AuditMixin, TimestampMixin, VersionedMixin):
    __tablename__ = "biomarker_definitions"

    slug = Column(String(255), nullable=False, index=True)
    coding_system = Column(
        Enum(CodingSystem), nullable=False, default=CodingSystem.LOINC
    )
    code = Column(String(100), nullable=True)
    name = Column(String(255), nullable=False)
    class_concept_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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
    scope = Column(
        Enum(CatalogScope, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CatalogScope.SYSTEM,
        index=True,
    )
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )  # Optional tenant override

    # Relationships
    preferred_unit = relationship("Unit", lazy="selectin")
    class_concept = relationship(
        "Concept",
        foreign_keys="[BiomarkerDefinition.class_concept_id]",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "reference_range_min IS NULL "
            "OR reference_range_max IS NULL "
            "OR reference_range_min <= reference_range_max",
            name="ck_biomarker_definitions_ref_range_order",
        ),
    )

    @property
    def category(self) -> str | None:
        """Backward-compat: the old ``category`` column was replaced by the
        ``class_concept_id`` FK to ``concepts``. Return the concept name."""
        return self.class_concept.name if self.class_concept else None


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


# NOTE (Phase 3): ``BiomarkerRelationship`` and ``BiomarkerEventCorrelation``
# were dropped — their data migrated into the polymorphic ``concept_edges``
# graph (CORRELATES_WITH for biomarker↔biomarker, MONITORS for
# biomarker↔clinical_event_type). See ``dev/plans/unified-catalog-
# architecture-2026-07-08.md`` §3.5.
