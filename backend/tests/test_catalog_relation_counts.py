"""Relation-count batch annotation — Phase D follow-up (#4).

`GET /catalogs/{type}?include=relations` must annotate each item with
``relation_count`` + ``relation_breakdown`` (per relation type) in a single
batched count query (no N+1). ``count_relations`` is also exercised directly.
"""

import uuid
from typing import Dict, Tuple

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.concept_model import ConceptEdge
from app.models.fhir.medication import MedicationCatalog
from app.models.tenant_model import TenantModel


async def _tenant_and_headers(role: str = "ADMIN") -> Tuple[uuid.UUID, Dict[str, str]]:
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="RC", slug=f"rc-{tenant_id}"))
        await db.commit()
    token = create_access_token(
        {
            "sub": f"{role.lower()}@rc.test",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": role,
        }
    )
    return tenant_id, {"Authorization": f"Bearer {token}"}


async def _create_medication(name: str) -> str:
    async with AsyncSessionLocal() as db:
        m = MedicationCatalog(name=name)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return str(m.id)


async def _add_edge(src_id: str, dst_id: str, relation: str = "CORRELATES_WITH") -> None:
    """Insert a concept_edges row directly (polymorphic, no FK to validate)."""
    from app.models.enums import EdgeEndpointType

    async with AsyncSessionLocal() as db:
        db.add(
            ConceptEdge(
                src_type=EdgeEndpointType.MEDICATION,
                src_id=uuid.UUID(src_id),
                dst_type=EdgeEndpointType.MEDICATION,
                dst_id=uuid.UUID(dst_id),
                relation=relation,  # type: ignore[arg-type]
                status="approved",  # type: ignore[arg-type]
                source="manual",  # type: ignore[arg-type]
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_count_relations_directly():
    """count_relations returns per-item totals + by_relation breakdown."""
    from app.models.enums import EdgeEndpointType
    from app.services.catalog_graph_service import count_relations

    a = await _create_medication("RC-A")
    b = await _create_medication("RC-B")
    c = await _create_medication("RC-C")
    # A -> B (two edges, different relations), A -> C (one), B has none outgoing.
    await _add_edge(a, b, "CORRELATES_WITH")
    await _add_edge(a, b, "MEMBER_OF")
    await _add_edge(a, c, "CORRELATES_WITH")

    async with AsyncSessionLocal() as db:
        counts = await count_relations(
            db,
            EdgeEndpointType.MEDICATION,
            [uuid.UUID(a), uuid.UUID(b), uuid.UUID(c)],
        )
    assert counts[a]["total"] == 3, counts[a]
    assert counts[a]["by_relation"]["CORRELATES_WITH"] == 2
    assert counts[a]["by_relation"]["MEMBER_OF"] == 1
    assert b not in counts  # no outgoing edges
    assert c not in counts


@pytest.mark.asyncio
async def test_list_with_include_relations_annotates_items(async_client):
    """GET /catalogs/{type}?include=relations adds relation_count per item."""
    _, headers = await _tenant_and_headers("ADMIN")
    a = await _create_medication("List-A")
    b = await _create_medication("List-B")
    await _add_edge(a, b, "CORRELATES_WITH")

    resp = await async_client.get(
        f"/api/v1/catalogs/medication?include=relations&search=List-",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_id = {it["id"]: it for it in body["items"]}
    assert by_id[a]["relation_count"] == 1, by_id[a]
    assert by_id[a]["relation_breakdown"]["CORRELATES_WITH"] == 1
    assert by_id[b]["relation_count"] == 0


@pytest.mark.asyncio
async def test_list_without_include_has_no_count(async_client):
    """Without include=relations, items are NOT annotated (backward-compat)."""
    _, headers = await _tenant_and_headers("ADMIN")
    a = await _create_medication("Plain-A")
    resp = await async_client.get(
        f"/api/v1/catalogs/medication?search=Plain-", headers=headers
    )
    assert resp.status_code == 200, resp.text
    by_id = {it["id"]: it for it in resp.json()["items"]}
    assert "relation_count" not in by_id[a]
