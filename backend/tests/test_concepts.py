"""Tests for the Concept and ConceptEdge models + migration schema.

Real-DB integration tests — exercises the actual tables, indexes, and unique
constraints created by migrations ``1a3dd1256035`` and ``9a3f7c2e1b4d``.

Multi-kind model (post ``9a3f7c2e1b4d``): a concept's domain membership lives
in the ``concept_kind_tags`` join table; ``primary_kind`` is a denormalized
mirror of one tag. Tests here build concepts with ``primary_kind=`` plus
explicit ``ConceptKindTag`` rows.

NOTE: the test conftest has no per-test transaction rollback, so each test
uses a UUID-derived slug prefix to avoid collisions with data left by prior
runs.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
from app.models.enums import (
    ConceptKind,
    ConceptStatus,
    ConceptProvenance,
    EdgeApprovalStatus,
    EdgeEndpointType,
    ConceptRelationType,
)


@pytest_asyncio.fixture
async def tenant_id():
    """Create a real tenant row so tenant-scoped FK constraints are satisfied."""
    from app.models.tenant_model import TenantModel

    tid = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(id=tid, name="Concept Test Tenant", slug=f"concept-test-{tid}")
        )
        await session.commit()
    return tid


def _slug(label: str = "") -> str:
    """Generate a unique slug to avoid cross-test data collisions."""
    return f"test-{label}-{uuid.uuid4().hex[:8]}"


def _make_concept(slug, name, kind, **extra):
    """Build a concept with a primary_kind + a matching kind tag."""
    extra.setdefault("status", ConceptStatus.ACTIVE)
    c = Concept(slug=slug, name=name, primary_kind=kind, **extra)
    c.kind_tags.append(ConceptKindTag(kind=kind))
    return c


@pytest.mark.asyncio
async def test_create_concept_global(tenant_id):
    """A global concept (tenant_id NULL) can be created and queried back."""
    slug = _slug("cardio")
    async with AsyncSessionLocal() as session:
        concept = _make_concept(
            slug,
            "Cardiology",
            ConceptKind.SPECIALTY,
            coding_system="snomed",
            code="394579002",
            aliases=["cardio", "heart medicine"],
        )
        session.add(concept)
        await session.commit()

        fetched = await session.get(Concept, concept.id)
        assert fetched is not None
        assert fetched.slug == slug
        assert fetched.primary_kind == ConceptKind.SPECIALTY
        assert fetched.tenant_id is None
        assert fetched.is_global is True
        assert fetched.is_active is True
        assert fetched.aliases == ["cardio", "heart medicine"]
        assert fetched.kinds == ["specialty"]


@pytest.mark.asyncio
async def test_create_concept_tenant_scoped(tenant_id):
    """A tenant-scoped concept carries the tenant_id."""
    slug = _slug("panel")
    async with AsyncSessionLocal() as session:
        concept = _make_concept(
            slug,
            "My Custom Panel",
            ConceptKind.BIOMARKER_PANEL,
            tenant_id=tenant_id,
        )
        session.add(concept)
        await session.commit()

        fetched = await session.get(Concept, concept.id)
        assert fetched.tenant_id == tenant_id
        assert fetched.is_global is False


@pytest.mark.asyncio
async def test_unique_slug_global(tenant_id):
    """Two global concepts with the same slug collide (NULL tenant fix)."""
    slug = _slug("dup")
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                _make_concept(slug, "First", ConceptKind.BIOMARKER_CLASS),
                _make_concept(slug, "Second", ConceptKind.BIOMARKER_CLASS),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_tenant_override_of_global_slug(tenant_id):
    """A tenant concept can share a slug with a global concept."""
    slug = _slug("override")
    async with AsyncSessionLocal() as session:
        global_c = _make_concept(slug, "Global", ConceptKind.BIOMARKER_CLASS)
        tenant_c = _make_concept(
            slug,
            "Tenant Override",
            ConceptKind.BIOMARKER_CLASS,
            tenant_id=tenant_id,
        )
        session.add_all([global_c, tenant_c])
        await session.commit()
        assert global_c.id != tenant_c.id


@pytest.mark.asyncio
async def test_parent_children_relationship(tenant_id):
    """Self-referential parent/children hierarchy works (selectin-loaded)."""
    parent_slug = _slug("parent")
    child_slug = _slug("child")
    async with AsyncSessionLocal() as session:
        parent = _make_concept(
            parent_slug, "Parent Cat", ConceptKind.EXAMINATION_CATEGORY
        )
        session.add(parent)
        await session.flush()

        child = _make_concept(
            child_slug,
            "Child Cat",
            ConceptKind.EXAMINATION_CATEGORY,
            parent_id=parent.id,
        )
        session.add(child)
        await session.commit()
        await session.refresh(parent)

        assert len(parent.children) == 1
        assert parent.children[0].slug == child_slug
        assert child.parent.slug == parent_slug


@pytest.mark.asyncio
async def test_create_edge_concept_to_concept(tenant_id):
    """A concept->concept edge (e.g. specialty EXAMINES body_system) persists."""
    async with AsyncSessionLocal() as session:
        cardio = _make_concept(_slug("cardio"), "Cardiology", ConceptKind.SPECIALTY)
        cvs = _make_concept(_slug("cvs"), "Cardiovascular", ConceptKind.BODY_SYSTEM)
        session.add_all([cardio, cvs])
        await session.flush()

        edge = ConceptEdge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=cardio.id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=cvs.id,
            relation=ConceptRelationType.EXAMINES,
            source=ConceptProvenance.SEED,
            status=EdgeApprovalStatus.APPROVED,
        )
        session.add(edge)
        await session.commit()

        fetched = await session.get(ConceptEdge, edge.id)
        assert fetched.relation == ConceptRelationType.EXAMINES
        assert fetched.source == ConceptProvenance.SEED
        assert fetched.status == EdgeApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_create_edge_entity_to_concept(tenant_id):
    """A polymorphic entity->concept edge (biomarker MEMBER_OF panel) persists."""
    async with AsyncSessionLocal() as session:
        panel = _make_concept(
            _slug("panel"), "Lipid Panel", ConceptKind.BIOMARKER_PANEL
        )
        session.add(panel)
        await session.flush()

        biomarker_uuid = uuid.uuid4()
        edge = ConceptEdge(
            src_type=EdgeEndpointType.BIOMARKER,
            src_id=biomarker_uuid,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=panel.id,
            relation=ConceptRelationType.MEMBER_OF,
            properties={"display_order": 1},
        )
        session.add(edge)
        await session.commit()

        result = await session.execute(
            select(ConceptEdge).where(
                ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                ConceptEdge.src_id == biomarker_uuid,
                ConceptEdge.relation == ConceptRelationType.MEMBER_OF,
            )
        )
        found = result.scalar_one()
        assert found.dst_id == panel.id
        assert found.properties["display_order"] == 1


@pytest.mark.asyncio
async def test_edge_unique_constraint(tenant_id):
    """Duplicate (src, dst, relation) edges are rejected."""
    async with AsyncSessionLocal() as session:
        c1 = _make_concept(_slug("src"), "Src", ConceptKind.DISEASE)
        c2 = _make_concept(_slug("dst"), "Dst", ConceptKind.DISEASE)
        session.add_all([c1, c2])
        await session.flush()

        edge1 = ConceptEdge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=c1.id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=c2.id,
            relation=ConceptRelationType.TREATS,
        )
        edge2 = ConceptEdge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=c1.id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=c2.id,
            relation=ConceptRelationType.TREATS,
        )
        session.add_all([edge1, edge2])
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_concept_to_dict(tenant_id):
    """to_dict() returns JSON-safe fields with enum values rendered as strings."""
    async with AsyncSessionLocal() as session:
        concept = _make_concept(
            _slug("ecg"),
            "Electrocardiogram",
            ConceptKind.EXAMINATION_CATEGORY,
            description="Heart electrical activity",
            color="#ef4444",
            aliases=["ekg"],
            icon={"type": "lucide", "value": "activity"},
        )
        session.add(concept)
        await session.commit()

        d = concept.to_dict()
        assert d["primary_kind"] == "examination_category"
        assert d["kinds"] == ["examination_category"]
        assert d["status"] == "active"
        assert d["color"] == "#ef4444"
        assert d["icon"] == {"type": "lucide", "value": "activity"}
        assert d["tenant_id"] is None
        assert isinstance(d["id"], str)


@pytest.mark.asyncio
async def test_count_by_primary_kind_scoped(tenant_id):
    """Querying by primary_kind + slug prefix reflects only this test's data."""
    prefix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                _make_concept(
                    f"{prefix}-s1",
                    "S1",
                    ConceptKind.SPECIALTY,
                    status=ConceptStatus.ACTIVE,
                ),
                _make_concept(
                    f"{prefix}-s2",
                    "S2",
                    ConceptKind.SPECIALTY,
                    status=ConceptStatus.ACTIVE,
                ),
                _make_concept(
                    f"{prefix}-s3",
                    "S3",
                    ConceptKind.SPECIALTY,
                    status=ConceptStatus.RETIRED,
                ),
            ]
        )
        await session.commit()

        active_count = await session.scalar(
            select(func.count())
            .select_from(Concept)
            .where(
                Concept.primary_kind == ConceptKind.SPECIALTY,
                Concept.status == ConceptStatus.ACTIVE,
                Concept.slug.startswith(prefix),
            )
        )
        assert active_count == 2

        all_count = await session.scalar(
            select(func.count())
            .select_from(Concept)
            .where(
                Concept.primary_kind == ConceptKind.SPECIALTY,
                Concept.slug.startswith(prefix),
            )
        )
        assert all_count == 3
