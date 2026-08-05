from sqlalchemy import Column, String, Float, ForeignKey, Enum, Text, Boolean, CheckConstraint, UniqueConstraint, Integer
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
from app.models.enums import (
    QuantityType,
    CodingSystem,
    CatalogScope,
    Gender,
    BiomarkerValueType,
)


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
    # Discriminator: numeric vs categorical. See ``BiomarkerValueType``.
    value_type = Column(
        Enum(BiomarkerValueType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=BiomarkerValueType.QUANTITY,
        index=True,
    )
    # STATE biomarkers only: when True, Observations use FHIR ``component[]``
    # (one ``valueCodeableConcept`` per sub-context) instead of a single
    # top-level value. Ignored for QUANTITY biomarkers.
    supports_multi_state = Column(Boolean, nullable=False, default=False)
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
    # STATE biomarkers only: the controlled vocabulary this biomarker accepts.
    # Join rows carry ``is_normal`` (the "normal set" replacing numeric ref
    # ranges) and ``sort_order`` (stable UI rendering).
    allowed_states = relationship(
        "BiomarkerAllowedState",
        back_populates="biomarker",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="BiomarkerAllowedState.sort_order",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "reference_range_min IS NULL "
            "OR reference_range_max IS NULL "
            "OR reference_range_min <= reference_range_max",
            name="ck_biomarker_definitions_ref_range_order",
        ),
        # STATE biomarkers cannot be telemetry (telemetry_data.value is
        # Float NOT NULL — categorical values have nowhere to go).
        CheckConstraint(
            "is_telemetry = FALSE OR value_type != 'state'",
            name="ck_biomarker_definitions_state_not_telemetry",
        ),
        # STATE biomarkers have no unit (categorical values are unitless).
        CheckConstraint(
            "value_type != 'state' OR preferred_unit_id IS NULL",
            name="ck_biomarker_definitions_state_no_unit",
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


class BiomarkerState(Base, UUIDMixin, AuditMixin, TimestampMixin):
    """The controlled vocabulary of categorical biomarker values (states).

    Universal catalog (no ``tenant_id``) — clinical state codes are global.
    Codes are drawn from standard code systems so FHIR interop is immediate:

    - HL7 v3-ObservationInterpretation
      (``http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation``)
      — POS, NEG, IND, H, L, S, R, ...
    - SNOMED CT (``http://snomed.info/sct``) — Detected / Not detected / ...
    - FHIR DataAbsentReason
      (``http://terminology.hl7.org/CodeSystem/data-absent-reason``)
    - ``urn:uuid:health-assistant:custom-state`` — proprietary codes
      (e.g. WITHIN_LIMITS) paralleling ``CodingSystem.CUSTOM``.

    A ``BiomarkerDefinition`` with ``value_type=STATE`` declares its allowed
    values via ``BiomarkerAllowedState`` join rows pointing here.
    """

    __tablename__ = "biomarker_states"

    slug = Column(String(80), nullable=False, unique=True, index=True)
    # The FHIR ``coding.code`` value (e.g. "POS", "260373001", "WITHIN_LIMITS").
    code = Column(String(100), nullable=False)
    # The FHIR ``coding.system`` URL identifying the code system.
    system = Column(String(255), nullable=False)
    # Human-readable label (FHIR ``coding.display``).
    display = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # Group label for UI navigation (e.g. "microbiology_serology",
    # "susceptibility", "data_absent"). Nullable for backward compat —
    # states without a category render in an "Other" group.
    category = Column(String(80), nullable=True)
    # Stable ordering for UI pickers / dropdowns.
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        # A code is unique within its code system (POS in v3-OI is unambiguous;
        # a different POS in another system would be a different concept).
        UniqueConstraint(
            "code", "system", name="uq_biomarker_states_code_system"
        ),
    )


class BiomarkerAllowedState(Base, UUIDMixin):
    """Join row: a STATE biomarker ↔ a state it accepts.

    Carries the per-biomarker metadata that replaces numeric reference ranges
    for categorical biomarkers:

    - ``is_normal`` — whether this state is in the biomarker's "normal set".
      The analytics status computation is:
      ``state in normal_set → "Normal" else "Abnormal"``.
    - ``sort_order`` — stable UI rendering (e.g. Positive → Negative → Indet).
    """

    __tablename__ = "biomarker_allowed_states"

    biomarker_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    state_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_states.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    is_normal = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)

    # Relationships
    biomarker = relationship(
        "BiomarkerDefinition", back_populates="allowed_states"
    )
    state = relationship("BiomarkerState", lazy="selectin")

    __table_args__ = (
        # A biomarker lists each state at most once.
        UniqueConstraint(
            "biomarker_id", "state_id", name="uq_biomarker_allowed_states"
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
