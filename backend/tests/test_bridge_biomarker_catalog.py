"""Phase B — catalog-driven biomarker list (`GET /biomarkers`).

DB-backed tests for the extended biomarker catalog read: rich fields
(``is_telemetry``, ``reference_range_min/max``, ``value_type``, the corrected
``unit`` emission), deterministic ordering, and tenant + global scoping.
Mirrors the ``test_bridge_telemetry_reads.py`` DB seeding pattern.
"""

import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from sqlalchemy import delete

from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider


async def _purge_catalog_data(
    *tenant_ids: uuid.UUID,
    global_def_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
) -> None:
    """Delete every row this module's fixture created.

    ``conftest`` truncates only once at session start; without this teardown the
    fixture's **global** BiomarkerDefinition + Unit (no tenant) leak into the
    seed-export default-catalog assertions and break them. Tenant-scoped rows
    are swept in dependency order; the two global rows are deleted by id.
    """
    tids = [t for t in tenant_ids if t]
    async with AsyncSessionLocal() as db:
        if tids:
            await db.execute(
                delete(UserIntegration).where(UserIntegration.tenant_id.in_(tids))
            )
            await db.execute(
                delete(BiomarkerDefinition).where(
                    BiomarkerDefinition.tenant_id.in_(tids)
                )
            )
            await db.execute(delete(Patient).where(Patient.tenant_id.in_(tids)))
            await db.execute(delete(UserModel).where(UserModel.tenant_id.in_(tids)))
            await db.execute(delete(TenantModel).where(TenantModel.id.in_(tids)))
        if global_def_id is not None:
            await db.execute(
                delete(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == global_def_id
                )
            )
        if unit_id is not None:
            await db.execute(delete(Unit).where(Unit.id == unit_id))
        await db.commit()


def _get_request(params: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.query_params = params or {}
    return req


@pytest_asyncio.fixture
async def bridge_with_catalog():
    """Two tenants + a bridge integration in tenant A, plus:
    * a tenant-A telemetry def ``heart-rate`` linked to a ``bpm`` Unit,
    * a tenant-A FHIR def ``glucose-fasting`` (no unit),
    * a global (NULL-tenant) def with a unique slug,
    * a tenant-B def that must be invisible to tenant A's bridge.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    hr_def_id = uuid.uuid4()
    gluc_def_id = uuid.uuid4()
    global_def_id = uuid.uuid4()
    b_def_id = uuid.uuid4()
    global_slug = f"global-marker-{uuid.uuid4().hex[:8]}"
    unit_symbol = f"bpm-{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(id=tenant_a, name="Catalog T.A", slug=f"cta-{tenant_a.hex[:8]}")
        )
        db.add(
            TenantModel(id=tenant_b, name="Catalog T.B", slug=f"ctb-{tenant_b.hex[:8]}")
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"cat-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_a,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_a,
                name={"family": "A", "given": ["Bound"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_a,
                user_id=user_id,
                patient_id=patient_id,
                provider="health_assistant_bridge",
                status="ACTIVE",
                user_config={},
            )
        )
        db.add(Unit(id=unit_id, symbol=unit_symbol, name="Beats per minute"))
        db.add(
            BiomarkerDefinition(
                id=hr_def_id,
                tenant_id=tenant_a,
                slug="heart-rate",
                name="Heart Rate",
                coding_system="loinc",
                code="8867-4",
                is_telemetry=True,
                preferred_unit_id=unit_id,
                reference_range_min=60,
                reference_range_max=100,
            )
        )
        db.add(
            BiomarkerDefinition(
                id=gluc_def_id,
                tenant_id=tenant_a,
                slug="glucose-fasting",
                name="Fasting Glucose",
                coding_system="loinc",
                code="2345-7",
                is_telemetry=False,
            )
        )
        db.add(
            BiomarkerDefinition(
                id=global_def_id,
                tenant_id=None,
                slug=global_slug,
                name="Global Marker",
                coding_system="custom",
                code="9000-0",
                is_telemetry=False,
                reference_range_min=0,
                reference_range_max=10,
            )
        )
        db.add(
            BiomarkerDefinition(
                id=b_def_id,
                tenant_id=tenant_b,
                slug="steps-tenant-b",
                name="Steps (tenant B)",
                coding_system="loinc",
                code="55423-8",
                is_telemetry=True,
            )
        )
        await db.commit()

    try:
        yield {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "integration_id": integration_id,
            "hr_def_id": hr_def_id,
            "gluc_def_id": gluc_def_id,
            "global_def_id": global_def_id,
            "b_def_id": b_def_id,
            "global_slug": global_slug,
            "unit_id": unit_id,
            "unit_symbol": unit_symbol,
        }
    finally:
        await _purge_catalog_data(
            tenant_a,
            tenant_b,
            global_def_id=global_def_id,
            unit_id=unit_id,
        )


async def _load_integration(integration_id) -> UserIntegration:
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(UserIntegration).where(UserIntegration.id == integration_id)
        )
        return res.scalar_one()


@pytest.mark.asyncio
async def test_biomarkers_emit_rich_fields(bridge_with_catalog):
    """Every item carries the extended fields; ``unit`` comes from the linked
    ``preferred_unit`` (the old ``default_unit`` getattr always returned None)."""
    ctx = bridge_with_catalog
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="biomarkers",
        method="GET",
        request=_get_request(),
    )

    by_slug = {b["slug"]: b for b in result["data"]}
    hr = by_slug["heart-rate"]
    assert hr["is_telemetry"] is True
    assert hr["unit"] == ctx["unit_symbol"], (
        "preferred_unit.symbol, not the always-None default_unit"
    )
    assert hr["reference_range_min"] == 60
    assert hr["reference_range_max"] == 100
    assert hr["value_type"] == "quantity"
    assert hr["coding_system"] == "loinc"
    assert hr["code"] == "8867-4"
    assert hr["id"] == str(ctx["hr_def_id"])

    gluc = by_slug["glucose-fasting"]
    assert gluc["is_telemetry"] is False
    assert gluc["unit"] is None
    assert gluc["reference_range_min"] is None
    assert gluc["value_type"] == "quantity"


@pytest.mark.asyncio
async def test_biomarkers_include_global_defs(bridge_with_catalog):
    """Global (NULL-tenant) definitions appear alongside tenant ones."""
    ctx = bridge_with_catalog
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="biomarkers",
        method="GET",
        request=_get_request(),
    )

    slugs = {b["slug"] for b in result["data"]}
    assert ctx["global_slug"] in slugs


@pytest.mark.asyncio
async def test_biomarkers_are_tenant_scoped(bridge_with_catalog):
    """Another tenant's definitions are invisible."""
    ctx = bridge_with_catalog
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="biomarkers",
        method="GET",
        request=_get_request(),
    )

    slugs = {b["slug"] for b in result["data"]}
    assert "steps-tenant-b" not in slugs
    assert not any(b["id"] == str(ctx["b_def_id"]) for b in result["data"])


@pytest.mark.asyncio
async def test_biomarkers_ordered_by_name(bridge_with_catalog):
    """Deterministic ordering by name (not insert order)."""
    ctx = bridge_with_catalog
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="biomarkers",
        method="GET",
        request=_get_request(),
    )

    names = [b["name"] for b in result["data"]]
    assert names == sorted(names)
