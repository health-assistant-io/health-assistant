from __future__ import annotations

from typing import List

from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    Enum as SQLEnum,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship

from app.models.base import (
    Base,
    UUIDMixin,
    AuditMixin,
    TenantMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.models.enums import (
    ConceptKind,
    ConceptStatus,
    ConceptProvenance,
    EdgeApprovalStatus,
    EdgeEndpointType,
    ConceptRelationType,
    CatalogScope,
)


def _enum_values(enum_cls):
    """Persist the enum ``.value`` (not the member name) for enums whose
    value differs from its name (e.g. lowercase / symbolic values)."""
    return [e.value for e in enum_cls]


class Concept(
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    """A single node in the unified medical taxonomy / knowledge graph.

    Holds the controlled-vocabulary terms for every category domain in the
    system (specialties, examination/event/biomarker/anatomy/document
    categories, biomarker panels, medication classes, diseases, …). Entity
    tables reference Concepts either via a direct FK (single-valued
    classification such as ``doctors.specialty_concept_id``) or via a
    polymorphic ``ConceptEdge`` row (M:N grouping such as biomarker panel
    membership).

    ``tenant_id`` is NULL for global/seeded canonical rows and set for
    tenant-private overrides. The unique partial index on
    ``(COALESCE(tenant_id, sentinel), slug)`` prevents duplicate global slugs
    without blocking tenant overrides.

    A concept is **multi-kind**: it carries zero or more ``ConceptKindTag``
    rows (one per domain it belongs to — e.g. "Blood Laboratory" is tagged as
    both ``examination_category`` and ``biomarker_class``). The
    denormalized ``primary_kind`` column mirrors one of those tags for cheap
    display ordering and backward-compatible single-badge rendering.
    """

    __tablename__ = "concepts"

    slug = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    primary_kind = Column(
        SQLEnum(ConceptKind, values_callable=_enum_values),
        nullable=True,
        index=True,
    )
    parent_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    description = Column(Text, nullable=True)
    # coding_system is a short code string ("loinc","snomed","atc","icd10",
    # "cvx","mesh","fma","custom") rather than an enum — terminology systems
    # are open-ended and adding new ones must not require a migration. Matches
    # the FHIR ``system`` field semantics (a URI/string, not a closed set).
    coding_system = Column(String(50), nullable=True)
    code = Column(String(100), nullable=True)
    aliases = Column(JSONB, nullable=False, default=list)
    icon = Column(JSONB, nullable=True)
    color = Column(String(50), nullable=True)
    status = Column(
        SQLEnum(ConceptStatus, values_callable=_enum_values),
        nullable=False,
        default=ConceptStatus.ACTIVE,
    )
    display_order = Column(Integer, nullable=False, default=0)
    meta_data = Column(JSONB, nullable=True)
    scope = Column(
        SQLEnum(CatalogScope, values_callable=_enum_values),
        nullable=False,
        default=CatalogScope.SYSTEM,
        index=True,
    )

    parent = relationship(
        "Concept",
        remote_side="[Concept.id]",
        foreign_keys=[parent_id],
        back_populates="children",
        lazy="selectin",
    )
    children = relationship(
        "Concept",
        foreign_keys=[parent_id],
        back_populates="parent",
        lazy="selectin",
    )
    kind_tags = relationship(
        "ConceptKindTag",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ConceptKindTag.kind",
    )

    __table_args__ = (
        Index("ix_concepts_primary_kind_status", "primary_kind", "status"),
        Index("ix_concepts_parent", "parent_id"),
    )

    @property
    def is_global(self) -> bool:
        return self.tenant_id is None

    @property
    def is_active(self) -> bool:
        return self.deleted_at is None and self.status == ConceptStatus.ACTIVE

    @property
    def kinds(self) -> List[str]:
        """All kind-domain tags on this concept, as their string values."""
        return [t.kind.value for t in (self.kind_tags or [])]

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "slug": self.slug,
            "name": self.name,
            "kinds": self.kinds,
            "primary_kind": self.primary_kind.value if self.primary_kind else None,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "description": self.description,
            "coding_system": self.coding_system,
            "code": self.code,
            "aliases": self.aliases or [],
            "icon": self.icon,
            "color": self.color,
            "status": self.status.value,
            "display_order": self.display_order,
            "meta_data": self.meta_data,
            "scope": self.scope.value if self.scope else "system",
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ConceptKindTag(
    Base,
    UUIDMixin,
    TimestampMixin,
):
    """A many-to-many tag linking a :class:`Concept` to a ``ConceptKind`` domain.

    A single concept can carry multiple kind tags — e.g. "Blood Laboratory" is
    tagged as both ``examination_category`` and ``biomarker_class`` (and also
    ``document_category``). The pair ``(concept_id, kind)`` is unique. The
    ``conceptkind`` PG enum type is shared with the legacy ``concepts.kind``
    column (which this tag table replaces).

    Cascade-deletes with its parent concept. The ``Concept.kind_tags``
    relationship is ``selectin``-loaded, so reads see the tags without an
    explicit join.
    """

    __tablename__ = "concept_kind_tags"

    concept_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(
        SQLEnum(ConceptKind, values_callable=_enum_values),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_concept_kind_tags_unique", "concept_id", "kind", unique=True),
        Index("ix_concept_kind_tags_kind", "kind"),
    )


class ConceptEdge(
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    TimestampMixin,
):
    """A typed, directed edge between two nodes in the knowledge graph.

    Endpoints are **polymorphic**: ``src_type``/``dst_type`` tag the table the
    UUID refers to (``concept`` → ``concepts.id``; ``biomarker`` →
    ``biomarker_definitions.id``; ``doctor`` → ``doctors.id``; …). There is no
    cross-table FK — orphan prevention is a service-layer concern backed by a
    nightly cleanup job (polymorphic refs cannot be FK-constrained in
    Postgres).

    ``status='approved'`` is the only value read by graph queries; ``proposed``
    rows are HITL-pending (AI suggestions) and ``rejected`` rows are retained
    for audit. ``source`` records provenance (seed/integration/ai/manual) and
    drives the curated-wins conflict resolution.
    """

    __tablename__ = "concept_edges"

    src_type = Column(
        SQLEnum(EdgeEndpointType, values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    src_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    dst_type = Column(
        SQLEnum(EdgeEndpointType, values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    dst_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    relation = Column(
        SQLEnum(ConceptRelationType, values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    properties = Column(JSONB, nullable=True)
    evidence = Column(JSONB, nullable=True)
    source = Column(
        SQLEnum(ConceptProvenance, values_callable=_enum_values),
        nullable=False,
        default=ConceptProvenance.MANUAL,
    )
    status = Column(
        SQLEnum(EdgeApprovalStatus, values_callable=_enum_values),
        nullable=False,
        default=EdgeApprovalStatus.APPROVED,
    )

    __table_args__ = (
        Index("ix_concept_edges_src", "src_type", "src_id"),
        Index("ix_concept_edges_dst", "dst_type", "dst_id"),
        Index("ix_concept_edges_relation_status", "relation", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "src_type": self.src_type.value,
            "src_id": str(self.src_id),
            "dst_type": self.dst_type.value,
            "dst_id": str(self.dst_id),
            "relation": self.relation.value,
            "properties": self.properties,
            "evidence": self.evidence,
            "source": self.source.value,
            "status": self.status.value,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
