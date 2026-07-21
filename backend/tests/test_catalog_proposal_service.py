"""Tests for ``app.services.catalog_proposal_service.apply_proposal``.

Workstream F.1 covers the ``kind="biomarker"`` router only; F.2 will
extend this file with medication / concept / edge cases. These are
real-DB integration tests — they spin up a tenant + user-shaped actor
and exercise the full write path (slug lookup, unit resolution, class
concept resolution, scope stamping, integrity rollback on a race).
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.enums import CatalogScope
from app.models.tenant_model import TenantModel
from app.schemas.user import TokenData
from app.services.catalog_proposal_service import ApplyResult, apply_proposal
from integrations.sdk.catalog import (
    CatalogProposal,
    biomarker_proposal,
    concept_proposal,
    edge_proposal,
    medication_proposal,
)


@pytest_asyncio.fixture
async def tenant_and_actor():
    """Create an isolated tenant + an ADMIN-scoped actor for it.

    Returns ``(tenant_id, actor, slug_prefix)``. The prefix is a short
    UUID-derived namespace so the test's biomarker slugs are unique across
    runs (``BiomarkerDefinition.slug`` has a *global* unique constraint —
    leftover data from a previous run would otherwise make the happy-path
    test look like an idempotent no-op)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug_prefix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(
                id=tenant_id, name="Catalog P.", slug=f"cp-{tenant_id.hex[:8]}"
            )
        )
        await session.commit()
    actor = TokenData(
        user_id=user_id,
        tenant_id=tenant_id,
        role="ADMIN",
        sub="integration-owner@test.local",
        is_service_account=False,
    )
    return tenant_id, actor, slug_prefix


class _FakeIntegration:
    """Stand-in for ``UserIntegration`` — only used for logging context."""

    def __init__(self, *, tenant_id):
        self.id = uuid.uuid4()
        self.tenant_id = tenant_id


# ---------------------------------------------------------------------------
# biomarker router — happy path + provenance + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_biomarker_proposal_creates_definition(tenant_and_actor):
    """A ``kind="biomarker"`` proposal creates a ``BiomarkerDefinition`` row
    with TENANT scope, ``created_by`` = the actor's user_id, and an
    explicit ``meta_data["_provenance"] = "integration"`` tag (the model
    has no provenance column, so the tag is the only marker)."""
    tenant_id, actor, p = tenant_and_actor

    proposal = biomarker_proposal(
        name="Sleep Quality Score",
        slug=f"{p}-sleep-quality",
        category="Sleep",
        coding_system="custom",
        code="HKSleepQuality",
        is_telemetry=True,
        aliases=["sleep_index"],
        info="Wearable-derived sleep score",
        reference_range_min=0.0,
        reference_range_max=100.0,
        confidence=0.8,
        rationale="Observed recurring HKSleepQuality codes upstream",
    )

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        result = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

        assert isinstance(result, ApplyResult)
        assert result.kind == "biomarker"
        assert result.created is True
        assert result.slug == f"{p}-sleep-quality"
        assert result.entity_id is not None

        # Re-fetch and verify all the stamping actually persisted.
        fetched = (
            await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == result.entity_id
                )
            )
        ).scalar_one()
        assert fetched.slug == f"{p}-sleep-quality"
        assert fetched.name == "Sleep Quality Score"
        assert fetched.is_telemetry is True
        assert fetched.aliases == ["sleep_index"]
        assert fetched.scope == CatalogScope.TENANT
        assert fetched.tenant_id == tenant_id
        assert fetched.created_by == actor.user_id
        assert fetched.meta_data == {"_provenance": "integration"}


@pytest.mark.asyncio
async def test_apply_biomarker_proposal_idempotent_on_slug(tenant_and_actor):
    """Re-applying a biomarker proposal with the same slug is a no-op —
    the second call returns ``created=False`` + the existing row's id, no
    duplicate is inserted. This is the contract the engine relies on so
    re-syncs don't spam duplicates."""
    tenant_id, actor, p = tenant_and_actor
    proposal = biomarker_proposal(
        name="Heart Rate Variability", slug=f"{p}-hrv"
    )

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        first = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

        second = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

    assert first.created is True
    assert second.created is False
    assert first.entity_id == second.entity_id

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.slug == first.slug
                )
            )
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_apply_biomarker_proposal_derives_slug_from_name(tenant_and_actor):
    """When the payload omits ``slug``, the service derives one from the
    name (lowercase, ASCII-safe) — matching the chat HITL
    ``propose_create_biomarker_definition`` behavior. Uses a UUID-prefixed
    name so the derived slug is unique across runs."""
    tenant_id, actor, p = tenant_and_actor
    proposal = biomarker_proposal(name=f"{p} Steps Count Daily")

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        result = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

    assert result.slug == f"{p}-steps-count-daily"


@pytest.mark.asyncio
async def test_apply_biomarker_proposal_rejects_empty_name(tenant_and_actor):
    """A proposal missing a non-empty ``name`` must raise ``ValueError``
    before touching the DB. The engine's per-item try/except catches
    this and logs."""
    tenant_id, actor, _p = tenant_and_actor
    proposal = CatalogProposal(kind="biomarker", payload={"name": ""})

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        with pytest.raises(ValueError, match="non-empty 'name'"):
            await apply_proposal(db, actor, integration, proposal)


@pytest.mark.asyncio
async def test_apply_proposal_rejects_unknown_kind(tenant_and_actor):
    """The router handles exactly the four kinds {biomarker, medication,
    concept, edge}. Pydantic's ``Literal`` rejects anything else at
    construction time, before the service is even called — so the engine
    never sees an unknown kind at runtime. The defense-in-depth ValueError
    branch in the router is a safety net for direct callers bypassing
    Pydantic."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="literal_error"):
        CatalogProposal(kind="not_a_kind", payload={"name": "x"})


@pytest.mark.asyncio
async def test_apply_biomarker_proposal_swallows_unit_resolution_miss(
    tenant_and_actor,
):
    """A ``preferred_unit_symbol`` that doesn't exist in the ``units``
    table must not abort the proposal — the service leaves
    ``preferred_unit_id`` unset and still creates the definition. (The
    biomarker endpoint has the same best-effort behavior.)"""
    tenant_id, actor, p = tenant_and_actor
    proposal = biomarker_proposal(
        name=f"{p} Mystery Metric",
        slug=f"{p}-mystery-metric",
        preferred_unit_symbol="nonexistent_unit_xyz",
    )

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        result = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

        fetched = (
            await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == result.entity_id
                )
            )
        ).scalar_one()
    assert fetched.preferred_unit_id is None


# ---------------------------------------------------------------------------
# F.2 — medication router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_medication_proposal_creates_entry(tenant_and_actor):
    """A ``kind="medication"`` proposal routes through
    ``medication_service.create_catalog_medication`` and lands a new
    ``MedicationCatalog`` row with TENANT scope + ``created_by`` = the
    actor's user_id."""
    from app.models.enums import CatalogScope
    from app.models.fhir.medication import MedicationCatalog

    tenant_id, actor, p = tenant_and_actor
    proposal = medication_proposal(
        name=f"{p} Melatonin",
        description="Sleep aid",
        indications="Insomnia",
        side_effects=["drowsiness"],
    )

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        result = await apply_proposal(db, actor, integration, proposal)

        assert result.kind == "medication"
        assert result.created is True
        assert result.entity_id is not None

        fetched = (
            await db.execute(
                select(MedicationCatalog).where(
                    MedicationCatalog.id == result.entity_id
                )
            )
        ).scalar_one()
        assert fetched.name == f"{p} Melatonin"
        assert fetched.indications == "Insomnia"
        assert fetched.side_effects == ["drowsiness"]
        assert fetched.scope == CatalogScope.TENANT
        assert fetched.tenant_id == tenant_id
        assert fetched.created_by == actor.user_id


@pytest.mark.asyncio
async def test_apply_medication_proposal_idempotent_on_name(tenant_and_actor):
    """Re-applying a medication proposal with the same name in the same
    tenant is a no-op (the second call returns ``created=False``)."""
    tenant_id, actor, _p = tenant_and_actor
    proposal = medication_proposal(name="Aspirin Unique")

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        first = await apply_proposal(db, actor, integration, proposal)
        second = await apply_proposal(db, actor, integration, proposal)

    assert first.created is True
    assert second.created is False
    assert first.entity_id == second.entity_id


@pytest.mark.asyncio
async def test_apply_medication_proposal_rejects_empty_name(tenant_and_actor):
    """Missing ``name`` → ValueError before any DB write."""
    tenant_id, actor, _p = tenant_and_actor
    proposal = CatalogProposal(kind="medication", payload={"name": ""})

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        with pytest.raises(ValueError, match="non-empty 'name'"):
            await apply_proposal(db, actor, integration, proposal)


# ---------------------------------------------------------------------------
# F.2 — concept router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_concept_proposal_creates_concept(tenant_and_actor):
    """A ``kind="concept"`` proposal routes through
    ``ConceptService.create_concept`` and lands a TENANT-scoped concept
    with the supplied kind tag."""
    from app.models.concept_model import Concept
    from app.models.enums import ConceptKind

    tenant_id, actor, p = tenant_and_actor
    proposal = concept_proposal(
        slug=f"{p}-sleep-disorder",
        name="Sleep Disorder",
        kind="disease",
        description="Disturbance of normal sleep patterns",
        aliases=["insomnia"],
    )

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        result = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

        assert result.kind == "concept"
        assert result.created is True
        assert result.slug == f"{p}-sleep-disorder"

        fetched = (
            await db.execute(
                select(Concept).where(Concept.id == result.entity_id)
            )
        ).scalar_one()
        assert fetched.name == "Sleep Disorder"
        assert fetched.tenant_id == tenant_id
        assert fetched.primary_kind == ConceptKind.DISEASE
        assert "insomnia" in fetched.aliases


@pytest.mark.asyncio
async def test_apply_concept_proposal_idempotent_on_slug(tenant_and_actor):
    """Re-applying a concept proposal with the same slug returns the
    existing concept's id (no duplicate)."""
    tenant_id, actor, p = tenant_and_actor
    proposal = concept_proposal(
        slug=f"{p}-body-system-x", name="Body System X", kind="body_system"
    )

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        first = await apply_proposal(db, actor, integration, proposal)
        await db.commit()
        second = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

    assert first.created is True
    assert second.created is False
    assert first.entity_id == second.entity_id


@pytest.mark.asyncio
async def test_apply_concept_proposal_rejects_invalid_kind(tenant_and_actor):
    """A kind value that isn't a valid ``ConceptKind`` enum member raises
    ``ValueError`` listing the valid options."""
    tenant_id, actor, _p = tenant_and_actor
    proposal = concept_proposal(
        slug="whatever", name="Whatever", kind="not_a_real_kind"
    )

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        with pytest.raises(ValueError, match="not a valid ConceptKind"):
            await apply_proposal(db, actor, integration, proposal)


# ---------------------------------------------------------------------------
# F.2 — edge router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_edge_proposal_creates_edge_with_integration_provenance(
    tenant_and_actor,
):
    """A ``kind="edge"`` proposal routes through
    ``ConceptService.create_edge`` and stamps ``source=INTEGRATION`` +
    ``status=APPROVED`` — the only place in the catalog layer today that
    actually writes the ``ConceptProvenance.INTEGRATION`` marker."""
    from app.models.concept_model import ConceptEdge
    from app.models.enums import (
        ConceptKind,
        ConceptProvenance,
        ConceptRelationType,
        EdgeApprovalStatus,
        EdgeEndpointType,
    )
    from app.services.concept_service import ConceptService

    tenant_id, actor, p = tenant_and_actor
    async with AsyncSessionLocal() as db:
        # Pre-create the two endpoints so create_edge's existence check passes.
        svc = ConceptService(db)
        disease = await svc.create_concept(
            slug=f"{p}-dis-a",
            name="Dis A",
            kind=ConceptKind.DISEASE,
            tenant_id=tenant_id,
            role=actor.role,
            actor=actor,
        )
        biomarker_concept = await svc.create_concept(
            slug=f"{p}-bio-a",
            name="Bio A",
            kind=ConceptKind.BIOMARKER_CLASS,
            tenant_id=tenant_id,
            role=actor.role,
            actor=actor,
        )
        await db.commit()

        proposal = edge_proposal(
            src_type="concept",
            src_id=str(disease.id),
            dst_type="concept",
            dst_id=str(biomarker_concept.id),
            relation="MONITORS",
            properties={"upstream_source": "test_integration"},
        )
        integration = _FakeIntegration(tenant_id=tenant_id)
        result = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

        assert result.kind == "edge"
        assert result.created is True

        fetched = (
            await db.execute(
                select(ConceptEdge).where(ConceptEdge.id == result.entity_id)
            )
        ).scalar_one()
        assert fetched.source == ConceptProvenance.INTEGRATION
        assert fetched.status == EdgeApprovalStatus.APPROVED
        assert fetched.relation == ConceptRelationType.MONITORS
        assert fetched.src_type == EdgeEndpointType.CONCEPT
        assert fetched.tenant_id == tenant_id


@pytest.mark.asyncio
async def test_apply_edge_proposal_idempotent_on_endpoints(tenant_and_actor):
    """Re-applying an edge with the same endpoints + relation is a no-op."""
    from app.models.enums import ConceptKind
    from app.services.concept_service import ConceptService

    tenant_id, actor, p = tenant_and_actor
    async with AsyncSessionLocal() as db:
        svc = ConceptService(db)
        disease = await svc.create_concept(
            slug=f"{p}-dis-b",
            name="Dis B",
            kind=ConceptKind.DISEASE,
            tenant_id=tenant_id,
            role=actor.role,
            actor=actor,
        )
        biomarker_concept = await svc.create_concept(
            slug=f"{p}-bio-b",
            name="Bio B",
            kind=ConceptKind.BIOMARKER_CLASS,
            tenant_id=tenant_id,
            role=actor.role,
            actor=actor,
        )
        await db.commit()

        proposal = edge_proposal(
            src_type="concept",
            src_id=str(disease.id),
            dst_type="concept",
            dst_id=str(biomarker_concept.id),
            relation="INDICATES",
        )
        integration = _FakeIntegration(tenant_id=tenant_id)
        first = await apply_proposal(db, actor, integration, proposal)
        await db.commit()
        second = await apply_proposal(db, actor, integration, proposal)
        await db.commit()

    assert first.created is True
    assert second.created is False
    assert first.entity_id == second.entity_id


@pytest.mark.asyncio
async def test_apply_edge_proposal_rejects_invalid_uuid(tenant_and_actor):
    """A non-UUID ``src_id`` / ``dst_id`` raises ``ValueError`` before
    hitting the DB."""
    tenant_id, actor, _p = tenant_and_actor
    proposal = edge_proposal(
        src_type="concept",
        src_id="not-a-uuid",
        dst_type="concept",
        dst_id="00000000-0000-0000-0000-000000000001",
        relation="MONITORS",
    )

    async with AsyncSessionLocal() as db:
        integration = _FakeIntegration(tenant_id=tenant_id)
        with pytest.raises(ValueError, match="valid UUIDs"):
            await apply_proposal(db, actor, integration, proposal)
