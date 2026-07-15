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
from app.models.enums import QuantityType, CodingSystem, CatalogScope, Gender


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
    reference_ranges = relationship(
        "BiomarkerReferenceRange",
        back_populates="biomarker",
        cascade="all, delete-orphan",
        passive_deletes=True,
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


class BiomarkerReferenceRange(Base, UUIDMixin, AuditMixin, TimestampMixin):
    """A stratified reference range for a biomarker (audit B9 / F3).

    ``BiomarkerDefinition`` previously carried a single global
    ``reference_range_min``/``max`` — unreliable for anyone outside the
    "default" demographic (wrong sex/age/unit → wrong ``relative_score`` and
    status). FHIR ``Observation.referenceRange`` supports ``0..*`` ranges each
    scoped by ``age``/``appliesTo``(sex)/unit, so this child table mirrors that.

    Each row applies to a sub-population; a NULL dimension means "any value for
    that axis" (NULL ``sex`` → both sexes, NULL ``age_min``/``age_max`` → all
    ages, NULL ``unit_id`` → any unit). The resolver
    (:func:`app.services.reference_ranges.resolve_reference_range`) picks the
    most-specific matching row for a given patient, falling back to the
    biomarker's legacy global range when no stratified row matches.
    """

    __tablename__ = "biomarker_reference_ranges"

    biomarker_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sex = Column(Enum(Gender), nullable=True)  # NULL → applies to any sex
    age_min = Column(Float, nullable=True)  # years (inclusive); NULL → no lower bound
    age_max = Column(Float, nullable=True)  # years (inclusive); NULL → no upper bound
    unit_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )  # NULL → applies to any unit
    low = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    text = Column(Text, nullable=True)  # human-readable range (FHIR referenceRange.text)
    # Optional population/condition tag (e.g. "pregnant", "pediatric") — reserved
    # for future stratification without a schema change.
    applies_to = Column(String(100), nullable=True)

    # Relationship back to the parent definition.
    biomarker = relationship(
        "BiomarkerDefinition", back_populates="reference_ranges"
    )

    __table_args__ = (
        CheckConstraint(
            "low IS NULL OR high IS NULL OR low <= high",
            name="ck_biomarker_reference_ranges_low_le_high",
        ),
        # Enforce sane age windows at the DB layer.
        CheckConstraint(
            "age_min IS NULL OR age_max IS NULL OR age_min <= age_max",
            name="ck_biomarker_reference_ranges_age_window",
        ),
    )


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
