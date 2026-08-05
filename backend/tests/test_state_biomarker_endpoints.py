"""End-to-end biomarker-endpoint tests for STATE biomarkers
(plan Step 6).

Real-DB flow: POST /biomarkers (STATE), PATCH /biomarkers/{id}/allowed_states,
GET /biomarkers/{id}, PATCH /biomarkers/{id} (telemetry-block on STATE).
Confirms the endpoints serialize value_type + allowed_states correctly and
that the hard telemetry-block on STATE biomarkers fires.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerState


V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


@pytest.fixture(autouse=True)
async def _scrub_test_rows():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "DELETE FROM biomarker_allowed_states WHERE biomarker_id IN "
                "(SELECT id FROM biomarker_definitions WHERE slug LIKE 'state-e2e-%')"
            )
        )
        await session.execute(
            text("DELETE FROM biomarker_definitions WHERE slug LIKE 'state-e2e-%'")
        )
        await session.execute(
            text("DELETE FROM biomarker_states WHERE slug LIKE 'state-e2e-%'")
        )
        await session.commit()


async def _seed_pos_neg_states():
    """Ensure POS/NEG BiomarkerState rows exist (look up first, insert if missing)."""
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        for code, display in [("POS", "Positive"), ("NEG", "Negative")]:
            existing = (
                await session.execute(
                    select(BiomarkerState).where(
                        BiomarkerState.code == code, BiomarkerState.system == V3
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    BiomarkerState(
                        slug=f"state-e2e-{code.lower()}-{uuid.uuid4().hex[:6]}",
                        code=code,
                        system=V3,
                        display=display,
                    )
                )
        await session.commit()


@pytest.fixture
async def auth_headers(system_admin_headers):
    """Reuse the real-JWT SYSTEM_ADMIN fixture from conftest."""
    return system_admin_headers


@pytest.mark.asyncio
async def test_create_state_biomarker_persists_allowed_states(
    async_client: AsyncClient, auth_headers
):
    """POST /biomarkers with value_type=state resolves the allowed_states slug
    list to BiomarkerState rows and persists the join rows; the response
    emits the resolved allowed_states list with code/system/display."""
    await _seed_pos_neg_states()

    response = await async_client.post(
        "/api/v1/biomarkers/",
        json={
            "slug": "state-e2e-pcr",
            "name": "SARS-CoV-2 PCR",
            "value_type": "state",
            "allowed_states": [
                {"state_slug": "positive", "is_normal": False},
                {"state_slug": "negative", "is_normal": True},
            ],
        },
        headers=auth_headers,
    )
    # If the canonical seed isn't loaded, fall back to the test-inserted
    # state-e2e-* slugs.
    if response.status_code != 200:
        # Look up the actual POS/NEG slugs in the DB and retry
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            pos = (
                await session.execute(
                    select(BiomarkerState).where(
                        BiomarkerState.code == "POS", BiomarkerState.system == V3
                    )
                )
            ).scalar_one()
            neg = (
                await session.execute(
                    select(BiomarkerState).where(
                        BiomarkerState.code == "NEG", BiomarkerState.system == V3
                    )
                )
            ).scalar_one()
        response = await async_client.post(
            "/api/v1/biomarkers/",
            json={
                "slug": "state-e2e-pcr",
                "name": "SARS-CoV-2 PCR",
                "value_type": "state",
                "allowed_states": [
                    {"state_slug": pos.slug, "is_normal": False},
                    {"state_slug": neg.slug, "is_normal": True},
                ],
            },
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["value_type"] == "state"
    assert data["supports_multi_state"] is False
    assert len(data["allowed_states"]) == 2
    codes = {s["code"] for s in data["allowed_states"]}
    assert codes == {"POS", "NEG"}
    # The is_normal flag is preserved per-state.
    normal_codes = {s["code"] for s in data["allowed_states"] if s["is_normal"]}
    assert normal_codes == {"NEG"}


@pytest.mark.asyncio
async def test_patch_state_biomarker_replaces_allowed_states(
    async_client: AsyncClient, auth_headers
):
    """PATCH /biomarkers/{id} with allowed_states replaces the join set
    atomically (the old rows are deleted via cascade)."""
    await _seed_pos_neg_states()
    # Create with POS only
    create_body = {
        "slug": "state-e2e-patch",
        "name": "Patch Test",
        "value_type": "state",
        "allowed_states": [{"state_slug": "positive", "is_normal": False}],
    }
    response = await async_client.post(
        "/api/v1/biomarkers/", json=create_body, headers=auth_headers
    )
    if response.status_code != 200:
        # Use test-seeded slugs as fallback (see prior test).
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            pos = (
                await session.execute(
                    select(BiomarkerState).where(
                        BiomarkerState.code == "POS", BiomarkerState.system == V3
                    )
                )
            ).scalar_one()
        create_body["allowed_states"] = [
            {"state_slug": pos.slug, "is_normal": False}
        ]
        response = await async_client.post(
            "/api/v1/biomarkers/", json=create_body, headers=auth_headers
        )
    assert response.status_code == 200, response.text
    bio_id = response.json()["id"]
    assert len(response.json()["allowed_states"]) == 1

    # Now PATCH: replace with NEG (normal)
    patch_body = {
        "allowed_states": [{"state_slug": "negative", "is_normal": True}]
    }
    response = await async_client.patch(
        f"/api/v1/biomarkers/{bio_id}", json=patch_body, headers=auth_headers
    )
    if response.status_code != 200:
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            neg = (
                await session.execute(
                    select(BiomarkerState).where(
                        BiomarkerState.code == "NEG", BiomarkerState.system == V3
                    )
                )
            ).scalar_one()
        patch_body["allowed_states"] = [
            {"state_slug": neg.slug, "is_normal": True}
        ]
        response = await async_client.patch(
            f"/api/v1/biomarkers/{bio_id}", json=patch_body, headers=auth_headers
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["allowed_states"]) == 1
    assert data["allowed_states"][0]["code"] == "NEG"
    assert data["allowed_states"][0]["is_normal"] is True


@pytest.mark.asyncio
async def test_patch_state_biomarker_rejects_telemetry_toggle(
    async_client: AsyncClient, auth_headers
):
    """PATCH /biomarkers/{id} { is_telemetry: true } on a STATE biomarker is
    rejected with HTTP 400 — telemetry is hard-blocked for STATE."""
    await _seed_pos_neg_states()
    response = await async_client.post(
        "/api/v1/biomarkers/",
        json={
            "slug": "state-e2e-no-telemetry",
            "name": "No Telemetry",
            "value_type": "state",
            "allowed_states": [{"state_slug": "positive", "is_normal": False}],
        },
        headers=auth_headers,
    )
    if response.status_code != 200:
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            pos = (
                await session.execute(
                    select(BiomarkerState).where(
                        BiomarkerState.code == "POS", BiomarkerState.system == V3
                    )
                )
            ).scalar_one()
        response = await async_client.post(
            "/api/v1/biomarkers/",
            json={
                "slug": "state-e2e-no-telemetry",
                "name": "No Telemetry",
                "value_type": "state",
                "allowed_states": [{"state_slug": pos.slug, "is_normal": False}],
            },
            headers=auth_headers,
        )
    assert response.status_code == 200, response.text
    bio_id = response.json()["id"]

    bad_patch = await async_client.patch(
        f"/api/v1/biomarkers/{bio_id}",
        json={"is_telemetry": True},
        headers=auth_headers,
    )
    assert bad_patch.status_code == 400
    assert "STATE" in bad_patch.json()["detail"] or "state" in bad_patch.json()[
        "detail"
    ].lower()


@pytest.mark.asyncio
async def test_post_state_biomarker_rejects_unknown_state_slug(
    async_client: AsyncClient, auth_headers
):
    """POST /biomarkers with an allowed_states slug that doesn't resolve → 400."""
    response = await async_client.post(
        "/api/v1/biomarkers/",
        json={
            "slug": "state-e2e-bad-slug",
            "name": "Bad Slug",
            "value_type": "state",
            "allowed_states": [
                {"state_slug": "__definitely_not_a_real_state__", "is_normal": False}
            ],
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Unknown biomarker_state slug" in response.json()["detail"]


@pytest.mark.asyncio
async def test_post_quantity_biomarker_unchaged_shape(
    async_client: AsyncClient, auth_headers
):
    """A QUANTITY biomarker POST still works exactly as before — the new fields
    default and the response includes value_type=quantity + empty allowed_states."""
    response = await async_client.post(
        "/api/v1/biomarkers/",
        json={
            "slug": "state-e2e-qty",
            "name": "Plain Quantity",
            "preferred_unit_symbol": "mg/dL",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["value_type"] == "quantity"
    assert data["supports_multi_state"] is False
    assert data["allowed_states"] == []
