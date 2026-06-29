import pytest
import pytest_asyncio
from httpx import AsyncClient
from typing import Dict, Any

from app.models.enums import AnatomyCategory, AnatomyRelationType, CodingSystem

@pytest.fixture
def anatomy_node_payload() -> Dict[str, Any]:
    return {
        "name": "Test Left Ventricle",
        "slug": "test-left-ventricle",
        "category": "ORGAN_PART",
        "standard_system": "snomed",
        "standard_code": "87878005",
        "description": "Lower left chamber",
        "is_custom": True
    }

@pytest_asyncio.fixture
async def sample_anatomy_nodes(client: AsyncClient, system_admin_headers: Dict[str, str]) -> list[str]:
    # Create two nodes for relationship tests
    node1 = {
        "name": "Test Heart",
        "slug": "test-heart",
        "category": "ORGAN",
        "is_custom": True
    }
    node2 = {
        "name": "Test Cardiovascular",
        "slug": "test-cardio",
        "category": "SYSTEM",
        "is_custom": True
    }
    
    res1 = await client.post("/api/v1/anatomy", json=node1, headers=system_admin_headers)
    assert res1.status_code == 200
    id1 = res1.json()["id"]
    
    res2 = await client.post("/api/v1/anatomy", json=node2, headers=system_admin_headers)
    assert res2.status_code == 200
    id2 = res2.json()["id"]
    
    return [id1, id2]

@pytest.mark.asyncio
async def test_create_and_get_anatomy_structure(
    client: AsyncClient, 
    system_admin_headers: Dict[str, str],
    anatomy_node_payload: Dict[str, Any]
):
    # 1. Create
    res = await client.post("/api/v1/anatomy", json=anatomy_node_payload, headers=system_admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["slug"] == anatomy_node_payload["slug"]
    assert "id" in data
    
    node_id = data["id"]
    
    # 2. Get by ID
    res_get = await client.get(f"/api/v1/anatomy/{node_id}", headers=system_admin_headers)
    assert res_get.status_code == 200
    assert res_get.json()["id"] == node_id
    
    # 3. Get by Slug
    res_slug = await client.get(f"/api/v1/anatomy/{anatomy_node_payload['slug']}", headers=system_admin_headers)
    assert res_slug.status_code == 200
    assert res_slug.json()["id"] == node_id

@pytest.mark.asyncio
async def test_anatomy_relations_and_traversal(
    client: AsyncClient, 
    system_admin_headers: Dict[str, str],
    sample_anatomy_nodes: list[str]
):
    source_id, target_id = sample_anatomy_nodes
    
    # 1. Create a relation (Heart -> PART_OF -> Cardiovascular)
    rel_payload = {
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": "PART_OF"
    }
    res = await client.post("/api/v1/anatomy/relations", json=rel_payload, headers=system_admin_headers)
    assert res.status_code == 200
    
    # 2. Fetch relationships for source (Heart should have outgoing to Cardio)
    res_related_source = await client.get(f"/api/v1/anatomy/{source_id}/related", headers=system_admin_headers)
    assert res_related_source.status_code == 200
    related_data = res_related_source.json()
    
    assert len(related_data["outgoing"]) == 1
    assert len(related_data["incoming"]) == 0
    assert related_data["outgoing"][0]["structure"]["id"] == target_id
    assert related_data["outgoing"][0]["relation_type"] == "PART_OF"
    
    # 3. Fetch relationships for target (Cardio should have incoming from Heart)
    res_related_target = await client.get(f"/api/v1/anatomy/{target_id}/related", headers=system_admin_headers)
    assert res_related_target.status_code == 200
    related_target_data = res_related_target.json()
    
    assert len(related_target_data["incoming"]) == 1
    assert len(related_target_data["outgoing"]) == 0
    assert related_target_data["incoming"][0]["structure"]["id"] == source_id

@pytest.mark.asyncio
async def test_anatomy_graph_multi_hop_depth(
    client: AsyncClient,
    system_admin_headers: Dict[str, str],
):
    """Multi-hop BFS: A -> B -> C, verify depth controls how many hops are returned."""
    a = await client.post("/api/v1/anatomy", json={"name": "Graph A", "slug": "graph-a", "category": "SYSTEM", "is_custom": True}, headers=system_admin_headers)
    b = await client.post("/api/v1/anatomy", json={"name": "Graph B", "slug": "graph-b", "category": "ORGAN", "is_custom": True}, headers=system_admin_headers)
    c = await client.post("/api/v1/anatomy", json={"name": "Graph C", "slug": "graph-c", "category": "ORGAN_PART", "is_custom": True}, headers=system_admin_headers)
    aid, bid, cid = a.json()["id"], b.json()["id"], c.json()["id"]

    # A -> B -> C (chain)
    await client.post("/api/v1/anatomy/relations", json={"source_id": bid, "target_id": aid, "relation_type": "PART_OF"}, headers=system_admin_headers)
    await client.post("/api/v1/anatomy/relations", json={"source_id": cid, "target_id": bid, "relation_type": "PART_OF"}, headers=system_admin_headers)

    # depth=1: only A and its direct neighbour B
    r1 = await client.get(f"/api/v1/anatomy/graph-a/graph?depth=1", headers=system_admin_headers)
    assert r1.status_code == 200
    d1 = r1.json()
    ids1 = {n["id"] for n in d1["nodes"]}
    assert aid in ids1 and bid in ids1 and cid not in ids1
    depths = {n["id"]: n["depth"] for n in d1["nodes"]}
    assert depths[aid] == 0 and depths[bid] == 1
    assert d1["root_id"] == aid

    # depth=2: includes C at depth 2
    r2 = await client.get(f"/api/v1/anatomy/graph-a/graph?depth=2", headers=system_admin_headers)
    d2 = r2.json()
    ids2 = {n["id"] for n in d2["nodes"]}
    assert {aid, bid, cid}.issubset(ids2)
    depths2 = {n["id"]: n["depth"] for n in d2["nodes"]}
    assert depths2[aid] == 0 and depths2[bid] == 1 and depths2[cid] == 2
    # The B->C edge should be present
    assert any(e["source_id"] == cid and e["target_id"] == bid for e in d2["edges"])

    # depth bounds enforced
    r_bad = await client.get(f"/api/v1/anatomy/graph-a/graph?depth=5", headers=system_admin_headers)
    assert r_bad.status_code == 422

@pytest.mark.asyncio
async def test_anatomy_import_upsert_logic(
    client: AsyncClient, 
    system_admin_headers: Dict[str, str]
):
    import_payload = {
        "nodes": [
            {
                "slug": "test-import-lung",
                "name": "Test Lung",
                "category": "ORGAN",
                "is_custom": True
            },
            {
                "slug": "test-import-resp",
                "name": "Test Resp System",
                "category": "SYSTEM",
                "is_custom": True
            }
        ],
        "edges": [
            {
                "source_slug": "test-import-lung",
                "target_slug": "test-import-resp",
                "relation_type": "PART_OF"
            }
        ]
    }
    
    # 1. Initial Import
    res1 = await client.post("/api/v1/anatomy/import", json=import_payload, headers=system_admin_headers)
    assert res1.status_code == 200
    stats1 = res1.json()
    assert stats1["nodes_added"] == 2
    assert stats1["nodes_updated"] == 0
    assert stats1["edges_added"] == 1
    
    # 2. Re-import (should upsert/skip)
    import_payload["nodes"][0]["name"] = "Test Lung Updated"
    res2 = await client.post("/api/v1/anatomy/import", json=import_payload, headers=system_admin_headers)
    assert res2.status_code == 200
    stats2 = res2.json()
    assert stats2["nodes_added"] == 0
    assert stats2["nodes_updated"] == 2
    assert stats2["edges_added"] == 0
    assert stats2["edges_updated"] == 1
    
    # Verify the update actually applied
    res_get = await client.get("/api/v1/anatomy/test-import-lung", headers=system_admin_headers)
    assert res_get.json()["name"] == "Test Lung Updated"
