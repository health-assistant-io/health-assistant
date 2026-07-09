from sqlalchemy import (
    Column,
    String,
    Boolean,
    Text,
    Integer,
    ForeignKey,
    Enum as SQLEnum,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin
from app.models.enums import AnatomyRelationType, CodingSystem, CatalogScope


class AnatomyStructure(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    __tablename__ = "anatomy_structures"

    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    class_concept_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Optional standard identifiers
    standard_system = Column(SQLEnum(CodingSystem), nullable=True)  # FMA/SNOMED/CUSTOM
    standard_code = Column(String(50), nullable=True)  # The actual ID, e.g., FMA_7088

    description = Column(Text, nullable=True)
    is_custom = Column(Boolean, default=False, nullable=False)

    # Optional rendering hints for the 2D anatomy explorer / body map.
    # Holds e.g. {"map": {"marker": {"view": "front", "cx": 102, "cy": 115,
    # "rx": 9, "ry": 10}}} so the frontend can draw organ markers and region
    # highlights data-driven instead of from a hardcoded lookup table.
    display = Column(JSONB, nullable=True)

    scope = Column(
        SQLEnum(CatalogScope, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CatalogScope.SYSTEM,
        index=True,
    )

    # Relationships
    class_concept = relationship(
        "Concept",
        foreign_keys="[AnatomyStructure.class_concept_id]",
        lazy="selectin",
    )
    outgoing_relations = relationship(
        "AnatomyRelation",
        foreign_keys="[AnatomyRelation.source_id]",
        back_populates="source_structure",
        cascade="all, delete-orphan",
    )
    incoming_relations = relationship(
        "AnatomyRelation",
        foreign_keys="[AnatomyRelation.target_id]",
        back_populates="target_structure",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("idx_anatomy_tenant_slug", "tenant_id", "slug"),)

    @property
    def class_concept_slug(self) -> str | None:
        """Slug of the anatomy-class concept (e.g. ``organ``), for filtering +
        display. Reads the eagerly-loaded ``class_concept`` relation."""
        return self.class_concept.slug if self.class_concept else None

    @property
    def class_concept_name(self) -> str | None:
        """Display name of the anatomy-class concept (e.g. ``Organ``)."""
        return self.class_concept.name if self.class_concept else None

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "class_concept_id": str(self.class_concept_id)
            if self.class_concept_id
            else None,
            "class_concept_slug": self.class_concept_slug,
            "class_concept_name": self.class_concept_name,
            "standard_system": self.standard_system.value
            if self.standard_system
            else None,
            "standard_code": self.standard_code,
            "description": self.description,
            "is_custom": self.is_custom,
            "display": self.display,
            "scope": self.scope.value if self.scope else "system",
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "created_by": str(self.created_by) if self.created_by else None,
        }


class AnatomyRelation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "anatomy_relations"

    source_id = Column(
        ForeignKey("anatomy_structures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_id = Column(
        ForeignKey("anatomy_structures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type = Column(SQLEnum(AnatomyRelationType), nullable=False)

    source_structure = relationship(
        "AnatomyStructure",
        foreign_keys=[source_id],
        back_populates="outgoing_relations",
    )

    target_structure = relationship(
        "AnatomyStructure",
        foreign_keys=[target_id],
        back_populates="incoming_relations",
    )

    __table_args__ = (
        Index(
            "idx_anatomy_relation_unique",
            "source_id",
            "target_id",
            "relation_type",
            unique=True,
        ),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "source_id": str(self.source_id),
            "target_id": str(self.target_id),
            "relation_type": self.relation_type.value,
        }


class AnatomyFigure(Base, UUIDMixin, TimestampMixin):
    """A body figure view stored as a raster image on disk (replaces the old
    hardcoded SVG atlas). Each row is one view of one figure (e.g.
    ``man-front``, ``woman-back``) backed by a WebP/PNG image file under
    ``UPLOAD_DIR/anatomy_figures/``. Markers on
    ``AnatomyStructure.display.map.markers`` are keyed by ``figure.slug``,
    normalized 0-1 relative to the image's pixel dimensions. Managed by
    SYSTEM_ADMIN.
    """

    __tablename__ = "anatomy_figures"

    slug = Column(String(100), nullable=False, unique=True, index=True)
    label = Column(String(200), nullable=False)
    # Groups views of the same figure: "man", "woman", or a custom group key.
    figure_key = Column(String(50), nullable=False, index=True)
    # Free-form view tag: "front", "back", "left", "right", or custom — not an
    # enum so admins can invent new aspects without code changes.
    view_key = Column(String(50), nullable=False)
    # Relative path to the image under UPLOAD_DIR (e.g. "anatomy_figures/man-front.webp").
    image_path = Column(String(500), nullable=True)
    # Original uncropped source image (for re-cropping in the editor). NULL when
    # no crop was applied (the image_path IS the original) or when discarded.
    source_image_path = Column(String(500), nullable=True)
    # Image pixel dimensions — markers resolve against these (normalized 0-1).
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)

    __table_args__ = (Index("idx_anatomy_figure_group", "figure_key", "view_key"),)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "slug": self.slug,
            "label": self.label,
            "figure_key": self.figure_key,
            "view_key": self.view_key,
            "image_path": self.image_path,
            "source_image_path": self.source_image_path,
            "width": self.width,
            "height": self.height,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
