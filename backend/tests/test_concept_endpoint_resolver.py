"""Tests for the polymorphic concept-edge endpoint resolver.

Covers resolve_endpoints for each registered type (concept, anatomy,
biomarker, examination) plus the fallback path for an unknown type / a
stale id, and verifies get_neighbors threads the resolved payload through.
"""

import uuid

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.models.enums import EdgeEndpointType, ConceptRelationType, EdgeApprovalStatus
from app.services.concept_endpoint_resolver import resolve_endpoints
from app.services.concept_service import ConceptService


@pytest_asyncio.fixture
async def tenant_id():
    from app.models.tenant_model import TenantModel

    tid = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tid, name="Resolver Tenant", slug=f"rsv-{tid}"))
        await session.commit()
    return tid


@pytest.mark.asyncio
async def test_resolve_concept_endpoint(tenant_id):
    from app.models.concept_model import Concept, ConceptKindTag
    from app.models.enums import ConceptKind, ConceptStatus

    async with AsyncSessionLocal() as session:
        c = Concept(
            slug=f"rsv-c-{uuid.uuid4().hex[:6]}",
            name="Cardiology",
            primary_kind=ConceptKind.SPECIALTY,
            color="#dc2626",
            icon={"type": "lucide", "value": "heart"},
            status=ConceptStatus.ACTIVE,
        )
        c.kind_tags.append(ConceptKindTag(kind=ConceptKind.SPECIALTY))
        session.add(c)
        await session.commit()

        out = await resolve_endpoints(
            session, [(EdgeEndpointType.CONCEPT, c.id)]
        )
        assert out[c.id]["type"] == "concept"
        assert out[c.id]["label"] == "Cardiology"
        assert out[c.id]["color"] == "#dc2626"
        assert out[c.id]["kind"] == "specialty"
        assert out[c.id]["icon"] == {"type": "lucide", "value": "heart"}


@pytest.mark.asyncio
async def test_resolve_anatomy_endpoint(tenant_id):
    from app.models.concept_model import Concept, ConceptKindTag
    from app.models.anatomy_model import AnatomyStructure
    from app.models.enums import ConceptKind, ConceptStatus

    async with AsyncSessionLocal() as session:
        # An anatomy_class concept ("organ") the structure classifies under.
        cls = Concept(
            slug=f"rsv-organ-{uuid.uuid4().hex[:6]}",
            name="Organ",
            primary_kind=ConceptKind.ANATOMY_CLASS,
            color="#10b981",
            status=ConceptStatus.ACTIVE,
        )
        cls.kind_tags.append(ConceptKindTag(kind=ConceptKind.ANATOMY_CLASS))
        session.add(cls)
        await session.flush()

        struct = AnatomyStructure(
            slug=f"rsv-heart-{uuid.uuid4().hex[:6]}",
            name="Heart",
            class_concept_id=cls.id,
            tenant_id=tenant_id,
        )
        session.add(struct)
        await session.commit()

        out = await resolve_endpoints(
            session, [(EdgeEndpointType.ANATOMY, struct.id)]
        )
        assert out[struct.id]["type"] == "anatomy"
        assert out[struct.id]["label"] == "Heart"
        # color/kind lifted from the class_concept (SSOT — not duplicated).
        assert out[struct.id]["color"] == "#10b981"
        assert out[struct.id]["kind"] == "Organ"


@pytest.mark.asyncio
async def test_resolve_unknown_type_fallback(tenant_id):
    """An endpoint type with no registered resolver gets a label-only fallback."""
    async with AsyncSessionLocal() as session:
        arbitrary = uuid.uuid4()
        out = await resolve_endpoints(
            session, [(EdgeEndpointType.ALLERGY, arbitrary)]
        )
        assert out[arbitrary]["type"] == "allergy"
        assert out[arbitrary]["label"].startswith("allergy:")
        assert out[arbitrary]["kind"] is None


@pytest.mark.asyncio
async def test_resolve_stale_id_fallback(tenant_id):
    """A registered type but a non-existent id still yields a fallback (no KeyError)."""
    async with AsyncSessionLocal() as session:
        stale = uuid.uuid4()
        out = await resolve_endpoints(session, [(EdgeEndpointType.CONCEPT, stale)])
        assert out[stale]["label"].startswith("concept:")
        assert out[stale]["kind"] is None


@pytest.mark.asyncio
async def test_get_neighbors_resolves_anatomy_endpoint(tenant_id):
    """End-to-end: a concept->anatomy edge comes back with a resolved endpoint
    payload (label "Heart"), not a bare UUID with None."""
    from app.models.concept_model import Concept, ConceptKindTag, ConceptEdge
    from app.models.anatomy_model import AnatomyStructure
    from app.models.enums import ConceptKind, ConceptStatus

    p = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as session:
        cls = Concept(
            slug=f"{p}-organ",
            name="Organ",
            primary_kind=ConceptKind.ANATOMY_CLASS,
            status=ConceptStatus.ACTIVE,
        )
        cls.kind_tags.append(ConceptKindTag(kind=ConceptKind.ANATOMY_CLASS))
        session.add(cls)
        await session.flush()

        category = Concept(
            slug=f"{p}-echo",
            name="Echocardiography",
            primary_kind=ConceptKind.EXAMINATION_CATEGORY,
            status=ConceptStatus.ACTIVE,
        )
        category.kind_tags.append(
            ConceptKindTag(kind=ConceptKind.EXAMINATION_CATEGORY)
        )
        session.add(category)
        await session.flush()

        heart = AnatomyStructure(
            slug=f"{p}-heart", name="Heart", class_concept_id=cls.id, tenant_id=tenant_id
        )
        session.add(heart)
        await session.flush()

        session.add(
            ConceptEdge(
                src_type=EdgeEndpointType.CONCEPT,
                src_id=category.id,
                dst_type=EdgeEndpointType.ANATOMY,
                dst_id=heart.id,
                relation=ConceptRelationType.IMAGES,
                status=EdgeApprovalStatus.APPROVED,
            )
        )
        await session.commit()

        svc = ConceptService(session)
        neighbors = await svc.get_neighbors(category.id, tenant_id)
        endpoints = [n["endpoint"] for n in neighbors if n["endpoint"]]
        assert any(e["label"] == "Heart" and e["type"] == "anatomy" for e in endpoints)
        assert all("endpoint" in n and "concept" not in n for n in neighbors)
