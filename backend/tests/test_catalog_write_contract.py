"""Unified catalog write contract (plan A Phase 2 — ADR 0055 D5).

One write service per catalog type; the generic ``/catalogs/{type}`` routes and
the domain routes dispatch to the same functions. This file pins the contract:

- **Full-field round-trips through the generic layer** for all six types
  (create → read → update → read) with a no-silent-drop assertion: every field
  of the typed create schema appears in the response, and every sent value
  survives to storage (computed fields included: unit symbol, class concept,
  reference ranges, allowed states, codes, derived slug, ``seed_key``).
- **Parity**: the domain endpoint and the generic endpoint produce the same
  stored row and response shape for the same logical payload; concept/anatomy
  writes through ``/catalogs`` no longer 405 (promote on concept does — no
  scope-transition semantics exist for the curated taxonomy).
- **Contract errors**: typed 422 for unknown/read-only fields (``scope``,
  ``tenant_id``, ``seed_key``, ``created_by``, typos), 409 for duplicates
  (``(coding_system, code)``, derived slugs, concept slugs) with a clear
  message — never a raw IntegrityError/500 — plus 403 policy, 404 missing,
  401 unauthenticated, identical from both doors.
- **Tenant isolation**: two tenants — class-concept resolution, code/slug
  collisions and row visibility; system rows + tenant overlays.
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs import CatalogRegistry
from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerState, Unit
from app.models.concept_model import Concept, ConceptKindTag
from app.models.enums import ConceptKind
from app.models.tenant_model import TenantModel
from tests._auth_helpers import headers_for_claims

PREFIX = "wc"  # write-contract


def _sweep_wc_rows() -> None:
    """Remove this module's rows (sync — runs outside any test event loop).

    The tests create global-vocabulary rows (``biomarker_states``,
    ``units``) whose presence would otherwise leak into later modules (e.g.
    the biomarker-states seed idempotency count). Everything this module
    creates is ``wc``-prefixed, so a LIKE sweep is exact; tenant deletion
    cascades the module's tenant-scoped catalog rows + users.
    """
    import asyncio

    from sqlalchemy import delete, text

    from app.models.biomarker_model import BiomarkerAllowedState, BiomarkerState, Unit

    async def _run() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "DELETE FROM biomarker_allowed_states WHERE biomarker_id IN "
                    "(SELECT id FROM biomarker_definitions WHERE slug LIKE 'wc-%')"
                )
            )
            await db.execute(
                delete(BiomarkerAllowedState).where(
                    BiomarkerAllowedState.state_id.in_(
                        select(BiomarkerState.id).where(BiomarkerState.slug.like("wc-st-%"))
                    )
                )
            )
            await db.execute(text("DELETE FROM biomarker_definitions WHERE slug LIKE 'wc-%'"))
            await db.execute(delete(BiomarkerState).where(BiomarkerState.slug.like("wc-st-%")))
            await db.execute(delete(Unit).where(Unit.symbol.like("wc-%")))
            await db.execute(text("DELETE FROM concepts WHERE slug LIKE 'wc-%'"))
            await db.execute(text("DELETE FROM anatomy_structures WHERE slug LIKE 'wc-%'"))
            await db.execute(text("DELETE FROM vaccine_catalog WHERE slug LIKE 'wc-%'"))
            await db.execute(text("DELETE FROM medication_catalog WHERE name LIKE 'Legacy Med %'"))
            await db.execute(text("DELETE FROM medication_catalog WHERE name LIKE 'Domain Med %'"))
            await db.execute(
                text("DELETE FROM allergy_catalog WHERE name LIKE 'Domain Allergen %'")
            )
            await db.execute(text("DELETE FROM tenants WHERE slug LIKE 'wc-%'"))
            await db.commit()
        # The sweep runs on its own asyncio loop — close its pooled
        # connections so the next test's loop never picks them up.
        from app.core.database import engine

        await engine.dispose()

    asyncio.run(_run())


@pytest.fixture(scope="module", autouse=True)
def _cleanup_wc_rows():
    _sweep_wc_rows()
    yield
    _sweep_wc_rows()


async def _tenant_and_headers(role: str) -> tuple[uuid.UUID, dict[str, str]]:
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="WC", slug=f"{PREFIX}-{tenant_id}"))
        await db.commit()
    headers = await headers_for_claims(
        {"sub": f"{role.lower()}@{PREFIX}.test", "tenant_id": str(tenant_id), "role": role}
    )
    return tenant_id, headers


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _assert_full_shape(payload_schema, response: dict, skip: tuple[str, ...] = ()) -> None:
    """No silent drop: every typed create-schema field must appear as a
    response key (values are asserted by the callers). ``skip`` names
    input-convenience fields the response deliberately does not echo."""
    missing = [f for f in payload_schema.model_fields if f not in response and f not in skip]
    assert not missing, f"response dropped schema fields: {missing}"


async def _create_concept(
    slug: str,
    name: str,
    kind: ConceptKind,
    tenant_id: uuid.UUID | None = None,
    db: AsyncSession | None = None,
    deleted_at: datetime | None = None,
) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        concept = Concept(
            slug=slug,
            name=name,
            primary_kind=kind,
            tenant_id=tenant_id,
            scope="system" if tenant_id is None else "tenant",
            deleted_at=deleted_at,
        )
        session.add(concept)
        await session.flush()
        session.add(ConceptKindTag(concept_id=concept.id, kind=kind))
        await session.commit()
        return concept.id


async def _concept_id_by_slug(slug: str, tenant_id: uuid.UUID | None) -> uuid.UUID | None:
    async with AsyncSessionLocal() as db:
        stmt = select(Concept.id).where(
            Concept.slug == slug,
            Concept.tenant_id == tenant_id if tenant_id else Concept.tenant_id.is_(None),
        )
        return (await db.execute(stmt)).scalar_one_or_none()


async def _seed_unit(symbol: str) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        unit = Unit(symbol=symbol, name=f"unit {symbol}", quantity_type="OTHER")
        db.add(unit)
        await db.commit()
        await db.refresh(unit)
        return unit.id


# ---------------------------------------------------------------------------
# Generic-layer full-field round-trips (one per catalog type)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_medication_round_trip_via_generic(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    class_slug = f"{PREFIX}-med-class-{s}"
    await _create_concept(class_slug, "WC Med Class", ConceptKind.MEDICATION_CLASS)

    body = {
        "name": f"Ibuprofen {s}",
        "coding_system": "RxNorm",
        "code": f"rx-{s}",
        "class_concept_slug": class_slug,
        "description": "an NSAID",
        "indications": "pain, fever",
        "side_effects": ["nausea"],
        "contraindications": "bleeding disorders",
        "dosage_info": "200-400mg",
    }
    create = await async_client.post("/api/v1/catalogs/medication", json=body, headers=admin)
    assert create.status_code == 201, create.text
    row = create.json()
    _assert_full_shape(CatalogRegistry.get("medication").write.create_schema, row)
    for key, value in body.items():
        if key == "class_concept_slug":
            assert row["class_concept_slug"] == value
        else:
            assert row[key] == value, key
    assert row["seed_key"] is None  # response-only, never client-settable
    assert row["scope"] == "tenant"

    got = await async_client.get(f"/api/v1/catalogs/medication/{row['id']}", headers=admin)
    assert got.status_code == 200
    assert got.json()["code"] == f"rx-{s}"
    assert got.json()["class_concept_name"] == "WC Med Class"

    new_class_slug = f"{PREFIX}-med-class2-{s}"
    await _create_concept(new_class_slug, "WC Med Class 2", ConceptKind.MEDICATION_CLASS)
    update = await async_client.put(
        f"/api/v1/catalogs/medication/{row['id']}",
        json={
            "code": f"rx-{s}-b",
            "description": "updated",
            "class_concept_slug": new_class_slug,
        },
        headers=admin,
    )
    assert update.status_code == 200, update.text
    updated = update.json()
    assert updated["code"] == f"rx-{s}-b"
    assert updated["description"] == "updated"
    assert updated["class_concept_slug"] == new_class_slug
    assert updated["name"] == body["name"]  # untouched field survives

    reread = await async_client.get(f"/api/v1/catalogs/medication/{row['id']}", headers=admin)
    assert reread.json()["class_concept_slug"] == new_class_slug


@pytest.mark.asyncio
async def test_allergy_round_trip_via_generic(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    body = {
        "name": f"Peanut {s}",
        "coding_system": "SNOMED",
        "code": f"sn-{s}",
        "category": "FOOD",
        "description": "legume allergen",
        "typical_reactions": ["hives", "anaphylaxis"],
    }
    create = await async_client.post("/api/v1/catalogs/allergy", json=body, headers=admin)
    assert create.status_code == 201, create.text
    row = create.json()
    _assert_full_shape(CatalogRegistry.get("allergy").write.create_schema, row)
    for key, value in body.items():
        assert row[key] == value, key
    assert row["scope"] == "tenant"

    update = await async_client.put(
        f"/api/v1/catalogs/allergy/{row['id']}",
        json={"code": f"sn-{s}-b", "category": "MEDICATION", "typical_reactions": ["urticaria"]},
        headers=admin,
    )
    assert update.status_code == 200, update.text
    assert update.json()["code"] == f"sn-{s}-b"
    assert update.json()["category"] == "MEDICATION"
    assert update.json()["typical_reactions"] == ["urticaria"]


@pytest.mark.asyncio
async def test_vaccine_round_trip_via_generic(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    body = {
        "name": f"Hepatitis B Vaccine {s}",
        "coding_system": "cvx",
        "code": f"cvx-{s}",
        "description": "three-dose series",
        "dose_schedule": {"doses": 3, "interval_days": 30},
        "contraindications": "none",
        "side_effects": ["sore arm"],
    }
    create = await async_client.post("/api/v1/catalogs/vaccine", json=body, headers=admin)
    assert create.status_code == 201, create.text
    row = create.json()
    _assert_full_shape(CatalogRegistry.get("vaccine").write.create_schema, row)
    for key, value in body.items():
        assert row[key] == value, key
    # Slug derived server-side from the name, never client-supplied.
    assert row["slug"] == f"hepatitis-b-vaccine-{s}"
    assert row["scope"] == "tenant"

    update = await async_client.put(
        f"/api/v1/catalogs/vaccine/{row['id']}",
        json={"description": "booster every 10y"},
        headers=admin,
    )
    assert update.status_code == 200, update.text
    assert update.json()["slug"] == f"hepatitis-b-vaccine-{s}"  # display-only, stable
    assert update.json()["description"] == "booster every 10y"


@pytest.mark.asyncio
async def test_biomarker_quantity_round_trip_via_generic(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    unit_id = await _seed_unit(f"{PREFIX}-u1-{s}")
    unit2_id = await _seed_unit(f"{PREFIX}-u2-{s}")
    class_slug = f"{PREFIX}-bio-class-{s}"
    await _create_concept(class_slug, "WC Bio Class", ConceptKind.BIOMARKER_CLASS)

    body = {
        "slug": f"{PREFIX}-bio-{s}",
        "name": f"Glucose {s}",
        "coding_system": "loinc",
        "code": f"loinc-{s}",
        "description": "fasting glucose",
        "class_concept_slug": class_slug,
        "aliases": ["GLU"],
        "info": "serum",
        "reference_range_min": 70,
        "reference_range_max": 100,
        "preferred_unit_symbol": f"{PREFIX}-u1-{s}",
        "reference_ranges": [
            {
                "sex": "FEMALE",
                "age_min": 18,
                "age_max": 60,
                "low": 60,
                "high": 90,
                "text": "adult female",
            }
        ],
    }
    create = await async_client.post("/api/v1/catalogs/biomarker", json=body, headers=admin)
    assert create.status_code == 201, create.text
    row = create.json()
    _assert_full_shape(CatalogRegistry.get("biomarker").write.create_schema, row)
    assert row["preferred_unit_id"] == str(unit_id)  # symbol → id resolved
    assert row["preferred_unit_symbol"] == f"{PREFIX}-u1-{s}"
    assert row["class_concept_slug"] == class_slug
    assert len(row["reference_ranges"]) == 1
    rr = row["reference_ranges"][0]
    assert rr["sex"] == "FEMALE"
    assert rr["low"] == 60 and rr["high"] == 90
    assert rr["biomarker_id"] == row["id"]

    got = await async_client.get(f"/api/v1/catalogs/biomarker/{row['id']}", headers=admin)
    assert got.status_code == 200
    assert len(got.json()["reference_ranges"]) == 1  # persisted, not response-only

    update = await async_client.put(
        f"/api/v1/catalogs/biomarker/{row['id']}",
        json={
            "info": "plasma",
            "reference_range_min": 65,
            "preferred_unit_symbol": f"{PREFIX}-u2-{s}",
            "description": "updated glucose",
        },
        headers=admin,
    )
    assert update.status_code == 200, update.text
    updated = update.json()
    assert updated["preferred_unit_id"] == str(unit2_id)
    assert updated["preferred_unit_symbol"] == f"{PREFIX}-u2-{s}"
    assert updated["reference_range_min"] == 65
    assert updated["info"] == "plasma"
    assert updated["class_concept_slug"] == class_slug  # untouched resolution survives


@pytest.mark.asyncio
async def test_biomarker_state_round_trip_via_generic(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    async with AsyncSessionLocal() as db:
        states = []
        for code, display in [("POS", "Positive"), ("NEG", "Negative")]:
            state = BiomarkerState(
                slug=f"{PREFIX}-st-{code.lower()}-{s}",
                code=code,
                system=f"urn:wc:{s}",
                display=display,
            )
            db.add(state)
            states.append(state)
        await db.commit()
        await db.refresh(states[0])
        await db.refresh(states[1])
        pos_slug, neg_slug = states[0].slug, states[1].slug

    body = {
        "slug": f"{PREFIX}-state-{s}",
        "name": f"SARS-CoV-2 PCR {s}",
        "value_type": "state",
        "allowed_states": [
            {"state_slug": pos_slug, "is_normal": False, "sort_order": 2},
            {"state_slug": neg_slug, "is_normal": True, "sort_order": 1},
        ],
    }
    create = await async_client.post("/api/v1/catalogs/biomarker", json=body, headers=admin)
    assert create.status_code == 201, create.text
    row = create.json()
    assert row["value_type"] == "state"
    codes = {a["code"]: a for a in row["allowed_states"]}
    assert set(codes) == {"POS", "NEG"}
    assert codes["NEG"]["is_normal"] is True
    assert codes["POS"]["is_normal"] is False
    assert codes["POS"]["sort_order"] == 2

    update = await async_client.put(
        f"/api/v1/catalogs/biomarker/{row['id']}",
        json={"allowed_states": [{"state_slug": neg_slug, "is_normal": False, "sort_order": 1}]},
        headers=admin,
    )
    assert update.status_code == 200, update.text
    assert len(update.json()["allowed_states"]) == 1
    assert update.json()["allowed_states"][0]["code"] == "NEG"
    assert update.json()["allowed_states"][0]["is_normal"] is False


@pytest.mark.asyncio
async def test_concept_round_trip_via_generic(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    parent_slug = f"{PREFIX}-parent-{s}"
    await _create_concept(parent_slug, "WC Parent", ConceptKind.SYMPTOM)

    body = {
        "slug": f"{PREFIX}-concept-{s}",
        "name": f"Wc Concept {s}",
        "kinds": ["symptom"],
        "tenant_scoped": True,
        "description": "test domain",
        "coding_system": "custom",
        "code": f"c-{s}",
        "parent_id": str(await _concept_id_by_slug(parent_slug, None)),
        "aliases": ["wcc"],
        "display_order": 7,
    }
    create = await async_client.post("/api/v1/catalogs/concept", json=body, headers=admin)
    assert create.status_code == 201, create.text
    row = create.json()
    _assert_full_shape(
        CatalogRegistry.get("concept").write.create_schema,
        row,
        skip=("kind", "tenant_scoped"),  # input conveniences, not persisted fields
    )
    assert row["kinds"] == ["symptom"]
    assert row["scope"] == "tenant"
    assert row["parent_slug"] == parent_slug  # computed field resolved
    assert row["seed_key"] is None

    got = await async_client.get(f"/api/v1/catalogs/concept/{row['id']}", headers=admin)
    assert got.status_code == 200
    assert got.json()["parent_slug"] == parent_slug

    update = await async_client.put(
        f"/api/v1/catalogs/concept/{row['id']}",
        json={"description": "updated", "code": f"c-{s}-b", "kinds": ["symptom", "disease"]},
        headers=admin,
    )
    assert update.status_code == 200, update.text
    assert update.json()["description"] == "updated"
    assert sorted(update.json()["kinds"]) == ["disease", "symptom"]


@pytest.mark.asyncio
async def test_anatomy_round_trip_via_generic(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    class_slug = f"{PREFIX}-organ-{s}"
    await _create_concept(class_slug, "WC Organ", ConceptKind.ANATOMY_CLASS)

    body = {
        "name": f"Liver {s}",
        "slug": f"{PREFIX}-liver-{s}",
        "class_concept_slug": class_slug,
        "standard_system": "custom",
        "standard_code": f"FMA_{s}",
        "description": "largest internal organ",
        "display": {"map": {"marker": {"view": "front"}}},
    }
    create = await async_client.post("/api/v1/catalogs/anatomy", json=body, headers=admin)
    assert create.status_code == 201, create.text
    row = create.json()
    _assert_full_shape(CatalogRegistry.get("anatomy").write.create_schema, row)
    assert row["class_concept_slug"] == class_slug
    assert row["class_concept_name"] == "WC Organ"
    assert row["display"] == body["display"]
    assert row["scope"] == "tenant"

    update = await async_client.put(
        f"/api/v1/catalogs/anatomy/{row['id']}",
        json={"description": "updated", "standard_code": f"FMA_{s}b"},
        headers=admin,
    )
    assert update.status_code == 200, update.text
    assert update.json()["description"] == "updated"
    assert update.json()["class_concept_slug"] == class_slug


# ---------------------------------------------------------------------------
# Parity: domain endpoint vs generic endpoint (same row, same shape)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain_path", "generic_type", "payload_factory"),
    [
        (
            "/api/v1/medications/catalog",
            "medication",
            lambda s: {
                "name": f"Parity Med {s}",
                "coding_system": "RxNorm",
                "code": f"pm-{s}",
                "description": "parity",
                "side_effects": ["dizziness"],
            },
        ),
        (
            "/api/v1/allergies/catalog",
            "allergy",
            lambda s: {
                "name": f"Parity Allergen {s}",
                "coding_system": "SNOMED",
                "code": f"pa-{s}",
                "category": "ENVIRONMENT",
                "typical_reactions": ["sneezing"],
            },
        ),
        (
            "/api/v1/vaccines/catalog",
            "vaccine",
            lambda s: {
                "name": f"Parity Vaccine {s}",
                "coding_system": "cvx",
                "code": f"pv-{s}",
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_domain_generic_create_parity(
    async_client: AsyncClient, domain_path, generic_type, payload_factory
):
    """Both doors return 201 with the SAME response shape, and each echoes its
    own payload (no door drops fields the other keeps)."""
    _, admin = await _tenant_and_headers("ADMIN")
    s1, s2 = _suffix(), _suffix()
    p1, p2 = payload_factory(s1), payload_factory(s2)

    domain = await async_client.post(domain_path, json=p1, headers=admin)
    generic = await async_client.post(f"/api/v1/catalogs/{generic_type}", json=p2, headers=admin)
    assert domain.status_code == generic.status_code == 201, (domain.text, generic.text)
    d, g = domain.json(), generic.json()
    assert set(d.keys()) == set(g.keys()), "response shapes diverge"
    for key, value in p1.items():
        assert d[key] == value, f"domain door dropped {key}"
    for key, value in p2.items():
        assert g[key] == value, f"generic door dropped {key}"


@pytest.mark.asyncio
async def test_biomarker_domain_generic_parity(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s1, s2 = _suffix(), _suffix()
    await _seed_unit(f"{PREFIX}-pu-{s1}")
    await _seed_unit(f"{PREFIX}-pu-{s2}")
    payload = lambda s: {  # noqa: E731
        "slug": f"{PREFIX}-pb-{s}",
        "name": f"Parity Bio {s}",
        "coding_system": "loinc",
        "code": f"pb-{s}",
        "aliases": ["PB"],
        "info": "parity",
        "reference_range_min": 1,
        "reference_range_max": 2,
        "preferred_unit_symbol": f"{PREFIX}-pu-{s}",
    }
    domain = await async_client.post("/api/v1/biomarkers/", json=payload(s1), headers=admin)
    generic = await async_client.post("/api/v1/catalogs/biomarker", json=payload(s2), headers=admin)
    assert domain.status_code == generic.status_code == 201, (domain.text, generic.text)
    d, g = domain.json(), generic.json()
    assert set(d.keys()) == set(g.keys()), "response shapes diverge"
    assert d["preferred_unit_symbol"] == f"{PREFIX}-pu-{s1}"
    assert g["preferred_unit_symbol"] == f"{PREFIX}-pu-{s2}"
    assert d["scope"] == g["scope"] == "tenant"


@pytest.mark.asyncio
async def test_biomarker_slug_derived_server_side_both_doors(async_client: AsyncClient):
    """An omitted slug is derived from the name server-side (plan D8 — the
    browse dialog / HITL proposals submit prefilled names without one).

    Both doors answer 201 with the same deterministic ``name → slug``
    algorithm; non-Latin characters are stripped (never a 422, never a
    non-identifier slug)."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    domain = await async_client.post(
        "/api/v1/biomarkers/",
        json={"name": f"WC Marker {s}"},
        headers=admin,
    )
    generic = await async_client.post(
        "/api/v1/catalogs/biomarker",
        json={"name": f"Ζάχαρη wc-{s} 異常"},
        headers=admin,
    )
    assert domain.status_code == 201, domain.text
    assert generic.status_code == 201, generic.text
    assert domain.json()["slug"] == f"wc-marker-{s}"
    # Non-Latin characters drop out of the derivation — the slug stays a
    # strict identifier (it is interpolated into raw SQL downstream).
    assert generic.json()["slug"] == f"wc-{s}"


@pytest.mark.asyncio
async def test_biomarker_derived_slug_collision_gets_unique_suffix(
    async_client: AsyncClient,
):
    """Two same-named slugless creates in one tenant scope both succeed — the
    second derives a ``-2`` suffixed slug instead of 409ing (the unique check
    mirrors the per-scope slug index exactly, system tier included)."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    name = f"WC Dup Marker {s}"
    first = await async_client.post("/api/v1/biomarkers/", json={"name": name}, headers=admin)
    assert first.status_code == 201, first.text
    assert first.json()["slug"] == f"wc-dup-marker-{s}"

    second = await async_client.post(
        "/api/v1/catalogs/biomarker", json={"name": name}, headers=admin
    )
    assert second.status_code == 201, second.text
    assert second.json()["slug"] == f"wc-dup-marker-{s}-2"

    # A derived base longer than the identifier cap is truncated to 77 chars
    # so a ``-2`` auto-suffix still fits the 80-char bound.
    long = await async_client.post(
        "/api/v1/catalogs/biomarker",
        json={"name": f"wc {'a' * 100} {s}"},
        headers=admin,
    )
    assert long.status_code == 201, long.text
    assert len(long.json()["slug"]) == 77
    assert long.json()["slug"].startswith("wc-")

    # The suffixed twin of that long name stays within the 80-char bound.
    long_dup = await async_client.post(
        "/api/v1/biomarkers/",
        json={"name": f"wc {'a' * 100} {s}"},
        headers=admin,
    )
    assert long_dup.status_code == 201, long_dup.text
    assert long_dup.json()["slug"] == f"{long.json()['slug']}-2"
    assert len(long_dup.json()["slug"]) == 79


@pytest.mark.asyncio
async def test_biomarker_explicit_slug_honored_and_duplicate_409(
    async_client: AsyncClient,
):
    """An explicit slug is honored verbatim (seed/import contract) and a
    duplicate still collides → 409 — never silently suffixed."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    slug = f"{PREFIX}-explicit-{s}"
    first = await async_client.post(
        "/api/v1/biomarkers/", json={"name": "First", "slug": slug}, headers=admin
    )
    assert first.status_code == 201, first.text
    assert first.json()["slug"] == slug

    dup = await async_client.post(
        "/api/v1/catalogs/biomarker", json={"name": "Second", "slug": slug}, headers=admin
    )
    assert dup.status_code == 409, dup.text

    # An explicit slug is never rewritten: colliding with a *derived* slug
    # (here: the name would derive the same slug) is still a hard 409.
    same_name = await async_client.post(
        "/api/v1/biomarkers/", json={"name": "First", "slug": slug}, headers=admin
    )
    assert same_name.status_code == 409, same_name.text


@pytest.mark.asyncio
async def test_concept_domain_generic_parity(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s1, s2 = _suffix(), _suffix()
    payload = lambda s: {  # noqa: E731
        "slug": f"{PREFIX}-pc-{s}",
        "name": f"Parity Concept {s}",
        "kinds": ["specialty"],
        "tenant_scoped": True,
        "description": "parity",
    }
    domain = await async_client.post("/api/v1/concepts", json=payload(s1), headers=admin)
    generic = await async_client.post("/api/v1/catalogs/concept", json=payload(s2), headers=admin)
    assert domain.status_code == generic.status_code == 201, (domain.text, generic.text)
    d, g = domain.json(), generic.json()
    assert set(d.keys()) == set(g.keys()), "response shapes diverge"
    assert d["kinds"] == g["kinds"] == ["specialty"]
    assert d["scope"] == g["scope"] == "tenant"


@pytest.mark.asyncio
async def test_anatomy_domain_generic_parity(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s1, s2 = _suffix(), _suffix()
    payload = lambda s: {  # noqa: E731
        "name": f"Parity Organ {s}",
        "slug": f"{PREFIX}-po-{s}",
        "description": "parity",
        "standard_system": "custom",
    }
    domain = await async_client.post("/api/v1/anatomy", json=payload(s1), headers=admin)
    generic = await async_client.post("/api/v1/catalogs/anatomy", json=payload(s2), headers=admin)
    assert domain.status_code == generic.status_code == 201, (domain.text, generic.text)
    d, g = domain.json(), generic.json()
    assert set(d.keys()) == set(g.keys()), "response shapes diverge"
    assert d["scope"] == g["scope"] == "tenant"


@pytest.mark.asyncio
async def test_concept_generic_write_no_longer_405(async_client: AsyncClient):
    """The meta-layer dispatches concept writes to ConceptService; promote has
    no concept semantics (scope is derived at create) and stays 405."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    create = await async_client.post(
        "/api/v1/catalogs/concept",
        json={
            "slug": f"{PREFIX}-no405-{s}",
            "name": "No 405",
            "kinds": ["symptom"],
            "tenant_scoped": True,
        },
        headers=admin,
    )
    assert create.status_code == 201, create.text

    promote = await async_client.post(
        f"/api/v1/catalogs/concept/{create.json()['id']}/promote",
        json={"scope": "system"},
        headers=admin,
    )
    assert promote.status_code == 405


# ---------------------------------------------------------------------------
# Contract errors: 422 / 409 / 403 / 404 / 401
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("generic_type", "base_payload"),
    [
        ("medication", {"name": "X"}),
        ("allergy", {"name": "X", "category": "FOOD"}),
        ("vaccine", {"name": "X"}),
        ("biomarker", {"slug": "x", "name": "X"}),
        ("concept", {"slug": "x", "name": "X", "kinds": ["symptom"], "tenant_scoped": True}),
        ("anatomy", {"name": "X", "slug": "x"}),
    ],
)
@pytest.mark.asyncio
async def test_readonly_and_unknown_fields_are_422(
    async_client: AsyncClient, generic_type, base_payload
):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    base_payload = {**base_payload, "name": f"X {s}"}
    if generic_type == "concept":
        base_payload["slug"] = f"{PREFIX}-422-{s}"
    if generic_type == "biomarker":
        base_payload["slug"] = f"{PREFIX}-422-{s}"
    if generic_type == "anatomy":
        base_payload["slug"] = f"{PREFIX}-422-{s}"
    for forbidden in ("scope", "tenant_id", "seed_key", "created_by", "bogus_field"):
        payload = {**base_payload, forbidden: "system"}
        resp = await async_client.post(
            f"/api/v1/catalogs/{generic_type}", json=payload, headers=admin
        )
        assert resp.status_code == 422, (forbidden, resp.text)
        detail = str(resp.json()["detail"])
        assert forbidden in detail


@pytest.mark.asyncio
async def test_domain_route_rejects_readonly_fields(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    resp = await async_client.post(
        "/api/v1/medications/catalog",
        json={"name": f"X {s}", "scope": "system", "seed_key": "sneaky"},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_vaccine_client_slug_is_422_both_doors(async_client: AsyncClient):
    """Slug is derived server-side (ADR 0055 D4) — a client-sent slug is a
    contract violation, not a silently ignored field."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    body = {"name": f"Slugless {s}", "slug": f"client-slug-{s}"}
    domain = await async_client.post("/api/v1/vaccines/catalog", json=body, headers=admin)
    generic = await async_client.post("/api/v1/catalogs/vaccine", json=body, headers=admin)
    assert domain.status_code == generic.status_code == 422


@pytest.mark.asyncio
async def test_medication_duplicate_code_409_both_doors(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    first = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"First {s}", "coding_system": "RxNorm", "code": f"dup-{s}"},
        headers=admin,
    )
    assert first.status_code == 201, first.text

    # Same code, same tenant — via the generic door and the domain door.
    generic = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"Second {s}", "coding_system": "RxNorm", "code": f"dup-{s}"},
        headers=admin,
    )
    assert generic.status_code == 409, generic.text
    assert generic.json()["code"] == "catalog_conflict"
    assert generic.json()["field"] == "code"

    domain = await async_client.post(
        "/api/v1/medications/catalog",
        json={"name": f"Third {s}", "coding_system": "RxNorm", "code": f"dup-{s}"},
        headers=admin,
    )
    assert domain.status_code == 409, domain.text

    # Updating the first row onto the colliding code of a sibling row → 409.
    second = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"Fourth {s}", "coding_system": "RxNorm", "code": f"dup2-{s}"},
        headers=admin,
    )
    conflict_update = await async_client.put(
        f"/api/v1/catalogs/medication/{second.json()['id']}",
        json={"code": f"dup-{s}"},
        headers=admin,
    )
    assert conflict_update.status_code == 409, conflict_update.text


@pytest.mark.asyncio
async def test_vaccine_duplicate_derived_slug_409(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    first = await async_client.post(
        "/api/v1/catalogs/vaccine", json={"name": f"Same Name {s}"}, headers=admin
    )
    assert first.status_code == 201, first.text
    second = await async_client.post(
        "/api/v1/catalogs/vaccine", json={"name": f"Same Name {s}"}, headers=admin
    )
    assert second.status_code == 409, second.text
    assert second.json()["field"] == "slug"


@pytest.mark.asyncio
async def test_concept_duplicate_slug_409_both_doors(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    slug = f"{PREFIX}-dup-{s}"
    first = await async_client.post(
        "/api/v1/catalogs/concept",
        json={"slug": slug, "name": "First", "kinds": ["symptom"], "tenant_scoped": True},
        headers=admin,
    )
    assert first.status_code == 201, first.text
    generic = await async_client.post(
        "/api/v1/catalogs/concept",
        json={"slug": slug, "name": "Second", "kinds": ["symptom"], "tenant_scoped": True},
        headers=admin,
    )
    assert generic.status_code == 409, generic.text
    domain = await async_client.post(
        "/api/v1/concepts",
        json={"slug": slug, "name": "Third", "kinds": ["symptom"], "tenant_scoped": True},
        headers=admin,
    )
    assert domain.status_code == 409, domain.text


@pytest.mark.asyncio
async def test_anatomy_duplicate_slug_409_both_doors(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    slug = f"{PREFIX}-anat-{s}"
    first = await async_client.post(
        "/api/v1/catalogs/anatomy", json={"name": "First", "slug": slug}, headers=admin
    )
    assert first.status_code == 201, first.text
    generic = await async_client.post(
        "/api/v1/catalogs/anatomy", json={"name": "Second", "slug": slug}, headers=admin
    )
    assert generic.status_code == 409, generic.text
    domain = await async_client.post(
        "/api/v1/anatomy", json={"name": "Third", "slug": slug}, headers=admin
    )
    assert domain.status_code == 409, domain.text


@pytest.mark.asyncio
async def test_biomarker_unknown_unit_and_state_400(async_client: AsyncClient):
    """Unit/state resolution failures are domain-value errors (400), never a
    silent drop and never a 500."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    bad_unit = await async_client.post(
        "/api/v1/catalogs/biomarker",
        json={"slug": f"{PREFIX}-bu-{s}", "name": "B", "preferred_unit_symbol": f"nope-{s}"},
        headers=admin,
    )
    assert bad_unit.status_code == 400, bad_unit.text
    assert "nope" in bad_unit.json()["detail"]

    bad_state = await async_client.post(
        "/api/v1/catalogs/biomarker",
        json={
            "slug": f"{PREFIX}-bs-{s}",
            "name": "B",
            "value_type": "state",
            "allowed_states": [{"state_slug": f"missing-{s}"}],
        },
        headers=admin,
    )
    assert bad_state.status_code == 400, bad_state.text
    assert "missing" in bad_state.json()["detail"]


@pytest.mark.asyncio
async def test_policy_403_on_modify(async_client: AsyncClient):
    """Same 403 semantics from both doors: an ADMIN may manage a tenant's
    user-scope rows but not system rows; a USER can modify their own."""
    tenant_id, admin = await _tenant_and_headers("ADMIN")
    _, sysadmin = await _tenant_and_headers("SYSTEM_ADMIN")
    s = _suffix()

    from tests._auth_helpers import headers_for_claims as _h

    user = await _h({"sub": f"user@{PREFIX}.test", "tenant_id": str(tenant_id), "role": "USER"})

    user_created = await async_client.post(
        "/api/v1/catalogs/medication", json={"name": f"User Row {s}"}, headers=user
    )
    assert user_created.status_code == 201
    assert user_created.json()["scope"] == "user"
    row_id = user_created.json()["id"]

    own_edit = await async_client.put(
        f"/api/v1/catalogs/medication/{row_id}", json={"name": "Self edit"}, headers=user
    )
    assert own_edit.status_code == 200  # creator may edit their own user-scope row

    admin_edit_of_user_row = await async_client.put(
        f"/api/v1/catalogs/medication/{row_id}", json={"name": "Managed"}, headers=admin
    )
    assert admin_edit_of_user_row.status_code == 200  # ADMIN/MANAGER manage the tenant

    system_row = await async_client.post(
        "/api/v1/catalogs/medication", json={"name": f"System Row {s}"}, headers=sysadmin
    )
    assert system_row.status_code == 201
    assert system_row.json()["scope"] == "system"
    sys_row_id = system_row.json()["id"]

    admin_forbidden = await async_client.put(
        f"/api/v1/catalogs/medication/{sys_row_id}", json={"name": "Hijack"}, headers=admin
    )
    assert admin_forbidden.status_code == 403

    user_forbidden = await async_client.put(
        f"/api/v1/catalogs/medication/{sys_row_id}", json={"name": "Hijack"}, headers=user
    )
    assert user_forbidden.status_code == 403

    user_delete_forbidden = await async_client.delete(
        f"/api/v1/catalogs/medication/{sys_row_id}", headers=user
    )
    assert user_delete_forbidden.status_code == 403


@pytest.mark.asyncio
async def test_missing_row_404_both_doors(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    missing = uuid.uuid4()
    put = await async_client.put(
        f"/api/v1/catalogs/medication/{missing}", json={"name": "x"}, headers=admin
    )
    assert put.status_code == 404
    delete = await async_client.delete(f"/api/v1/catalogs/medication/{missing}", headers=admin)
    assert delete.status_code == 404
    domain_put = await async_client.put(
        f"/api/v1/medications/catalog/{missing}", json={"name": "x"}, headers=admin
    )
    assert domain_put.status_code == 404
    domain_delete = await async_client.delete(
        f"/api/v1/medications/catalog/{missing}", headers=admin
    )
    assert domain_delete.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_writes_are_401(async_client: AsyncClient):
    no_auth = await async_client.post("/api/v1/catalogs/medication", json={"name": "x"})
    assert no_auth.status_code == 401
    domain_no_auth = await async_client.post("/api/v1/medications/catalog", json={"name": "x"})
    assert domain_no_auth.status_code == 401


@pytest.mark.asyncio
async def test_unknown_catalog_type_404(async_client: AsyncClient):
    _, admin = await _tenant_and_headers("ADMIN")
    resp = await async_client.post("/api/v1/catalogs/dragon", json={"name": "x"}, headers=admin)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tenant isolation (Phase-2 carry-over: tenant-explicit resolutions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_collision_is_per_tenant_and_rows_invisible_cross_tenant(
    async_client: AsyncClient,
):
    _, admin_a = await _tenant_and_headers("ADMIN")
    _, admin_b = await _tenant_and_headers("ADMIN")
    s = _suffix()

    a = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"A {s}", "coding_system": "RxNorm", "code": f"iso-{s}"},
        headers=admin_a,
    )
    assert a.status_code == 201
    b = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"B {s}", "coding_system": "RxNorm", "code": f"iso-{s}"},
        headers=admin_b,
    )
    assert b.status_code == 201, "another tenant's code must not block a create"

    # A's row is invisible to B (404, not a leak) — both doors.
    got = await async_client.get(f"/api/v1/catalogs/medication/{a.json()['id']}", headers=admin_b)
    assert got.status_code == 404
    domain = await async_client.get(
        f"/api/v1/medications/catalog/{a.json()['id']}", headers=admin_b
    )
    assert domain.status_code == 404


@pytest.mark.asyncio
async def test_class_concept_resolution_is_tenant_scoped(async_client: AsyncClient):
    """One tenant's class concept must never satisfy another tenant's slug
    resolution — and an unresolvable slug is now a typed 400, not a silent
    drop of the link (Phase-3 hardening)."""
    tenant_b, _ = await _tenant_and_headers("ADMIN")
    _, admin_a = await _tenant_and_headers("ADMIN")
    s = _suffix()
    shadow_slug = f"{PREFIX}-shadow-{s}"
    await _create_concept(
        shadow_slug, "Tenant B Shadow", ConceptKind.MEDICATION_CLASS, tenant_id=tenant_b
    )

    # Another tenant's slug → 400 (the slug exists, but not for this caller).
    resp = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"Shadowed {s}", "class_concept_slug": shadow_slug},
        headers=admin_a,
    )
    assert resp.status_code == 400, resp.text
    assert "Tenant B Shadow" not in resp.json()["detail"]
    assert shadow_slug not in resp.json()["detail"]

    # A purely unknown slug → 400 as well (never silently dropped).
    unknown = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"Unknown Slug {s}", "class_concept_slug": f"nope-{s}"},
        headers=admin_a,
    )
    assert unknown.status_code == 400, unknown.text

    # A system concept with the same slug resolves (system rows are global).
    await _create_concept(shadow_slug, "System Canonical", ConceptKind.MEDICATION_CLASS)
    resp2 = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"Canonical {s}", "class_concept_slug": shadow_slug},
        headers=admin_a,
    )
    assert resp2.status_code == 201, resp2.text
    assert resp2.json()["class_concept_name"] == "System Canonical"


@pytest.mark.asyncio
async def test_system_tenant_code_overlay_is_allowed(async_client: AsyncClient):
    """A tenant overlay may reuse a system row's code (per-scope uniqueness,
    ADR 0055) — both stay visible to the tenant."""
    _, sysadmin = await _tenant_and_headers("SYSTEM_ADMIN")
    _, admin_a = await _tenant_and_headers("ADMIN")
    s = _suffix()
    code = f"overlay-{s}"

    system_row = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"System {s}", "coding_system": "RxNorm", "code": code},
        headers=sysadmin,
    )
    assert system_row.status_code == 201, system_row.text
    overlay = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"Overlay {s}", "coding_system": "RxNorm", "code": code},
        headers=admin_a,
    )
    assert overlay.status_code == 201, overlay.text
    assert overlay.json()["scope"] == "tenant"
    assert system_row.json()["id"] != overlay.json()["id"]

    listed = await async_client.get(
        f"/api/v1/catalogs/medication?search=Overlay {s}", headers=admin_a
    )
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["items"]}
    assert overlay.json()["id"] in ids


@pytest.mark.asyncio
async def test_vaccine_slug_collision_is_per_tenant(async_client: AsyncClient):
    _, admin_a = await _tenant_and_headers("ADMIN")
    _, admin_b = await _tenant_and_headers("ADMIN")
    s = _suffix()
    a = await async_client.post(
        "/api/v1/catalogs/vaccine", json={"name": f"Shared Name {s}"}, headers=admin_a
    )
    assert a.status_code == 201
    b = await async_client.post(
        "/api/v1/catalogs/vaccine", json={"name": f"Shared Name {s}"}, headers=admin_b
    )
    assert b.status_code == 201, "same derived slug in another tenant is allowed"


@pytest.mark.asyncio
async def test_biomarker_slug_collision_is_per_tenant(async_client: AsyncClient):
    """Biomarker twin of the vaccine per-tenant test: two tenants derive the
    same slug from the same slugless name — both answer 201 with the identical
    derived slug and no ``-2`` suffix (the uniqueness loop is per scope, so a
    sibling tenant's row can never push a suffix or 409)."""
    _, admin_a = await _tenant_and_headers("ADMIN")
    _, admin_b = await _tenant_and_headers("ADMIN")
    s = _suffix()
    name = f"Shared Bio Marker {s}"
    a = await async_client.post("/api/v1/biomarkers/", json={"name": name}, headers=admin_a)
    assert a.status_code == 201, a.text
    assert a.json()["slug"] == f"shared-bio-marker-{s}"
    b = await async_client.post("/api/v1/catalogs/biomarker", json={"name": name}, headers=admin_b)
    assert b.status_code == 201, "same derived slug in another tenant is allowed"
    assert b.json()["slug"] == f"shared-bio-marker-{s}", (
        "the suffix loop is per-scope — a sibling tenant must not push a suffix"
    )


@pytest.mark.asyncio
async def test_promote_requires_system_admin_and_audit_trail_preserved(async_client):
    """ADMIN cannot promote into system scope (403); successful writes keep
    the audit trail (create is recorded)."""
    from app.services.catalog.catalog_audit_service import list_history

    tenant_a, admin_a = await _tenant_and_headers("ADMIN")
    s = _suffix()
    created = await async_client.post(
        "/api/v1/catalogs/vaccine",
        json={"name": f"Promote A {s}"},
        headers=admin_a,
    )
    assert created.status_code == 201, created.text

    promote = await async_client.post(
        f"/api/v1/catalogs/vaccine/{created.json()['id']}/promote",
        json={"scope": "system"},
        headers=admin_a,
    )
    assert promote.status_code == 403

    async with AsyncSessionLocal() as db:
        rows = await list_history(
            db,
            tenant_id=tenant_a,
            catalog_type="vaccine",
            item_id=uuid.UUID(created.json()["id"]),
        )
    assert "create" in {r.operation for r in rows}


@pytest.mark.asyncio
async def test_promote_to_system_conflict_is_409(async_client: AsyncClient):
    tenant_a, admin_a = await _tenant_and_headers("ADMIN")
    from tests._auth_helpers import headers_for_claims as _h

    sysadmin_same_tenant = await _h(
        {"sub": f"sa@{PREFIX}.test", "tenant_id": str(tenant_a), "role": "SYSTEM_ADMIN"}
    )
    s = _suffix()
    name = f"Promo Clash {s}"
    # A system-scope row with a derived slug exists...
    system_row = await async_client.post(
        "/api/v1/catalogs/vaccine", json={"name": name}, headers=sysadmin_same_tenant
    )
    assert system_row.status_code == 201
    assert system_row.json()["scope"] == "system"
    # ...and a tenant row with the same name (same derived slug)...
    tenant_row = await async_client.post(
        "/api/v1/catalogs/vaccine", json={"name": name}, headers=admin_a
    )
    assert tenant_row.status_code == 201, tenant_row.text
    # ...cannot be promoted into the occupied system namespace.
    promote = await async_client.post(
        f"/api/v1/catalogs/vaccine/{tenant_row.json()['id']}/promote",
        json={"scope": "system"},
        headers=sysadmin_same_tenant,
    )
    assert promote.status_code == 409, promote.text


@pytest.mark.asyncio
async def test_medication_update_round_trip_preserves_audit(async_client: AsyncClient):
    from app.services.catalog.catalog_audit_service import list_history

    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    created = await async_client.post(
        "/api/v1/medications/catalog", json={"name": f"Audited {s}"}, headers=admin
    )
    assert created.status_code == 201
    updated = await async_client.put(
        f"/api/v1/medications/catalog/{created.json()['id']}",
        json={"description": "touched"},
        headers=admin,
    )
    assert updated.status_code == 200

    async with AsyncSessionLocal() as db:
        rows = await list_history(
            db,
            tenant_id=uuid.UUID(created.json()["tenant_id"]),
            catalog_type="medication",
            item_id=uuid.UUID(created.json()["id"]),
        )
    assert {r.operation for r in rows} >= {"create", "update"}


# ---------------------------------------------------------------------------
# Phase-3 hardening (REVISE blockers): explicit reference ids are
# visibility-checked, bogus references are typed 400s, constraint violations
# on promote/update never surface as 500s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_class_concept_id_of_other_tenant_rejected_on_create(
    async_client: AsyncClient,
):
    """B1: a client-supplied ``class_concept_id`` pointing at another tenant's
    private concept is a typed 400 on every catalog type — never stored, and
    the error carries no name/slug of the hidden row."""
    tenant_b, _ = await _tenant_and_headers("ADMIN")
    _, admin_a = await _tenant_and_headers("ADMIN")
    s = _suffix()
    hidden_name = "Hidden Private Class"
    hidden_slug = f"{PREFIX}-hidden-{s}"
    hidden = await _create_concept(
        hidden_slug, hidden_name, ConceptKind.MEDICATION_CLASS, tenant_id=tenant_b
    )
    cases = [
        ("medication", {"name": f"M {s}"}),
        ("allergy", {"name": f"A {s}", "category": "FOOD"}),
        ("vaccine", {"name": f"V {s}"}),
        ("biomarker", {"slug": f"{PREFIX}-xb-{s}", "name": f"B {s}"}),
        ("anatomy", {"name": f"An {s}", "slug": f"{PREFIX}-xa-{s}"}),
    ]
    for type_, base in cases:
        resp = await async_client.post(
            f"/api/v1/catalogs/{type_}",
            json={**base, "class_concept_id": str(hidden)},
            headers=admin_a,
        )
        assert resp.status_code == 400, (type_, resp.text)
        detail = resp.json()["detail"]
        assert hidden_name not in detail and hidden_slug not in detail, (type_, detail)


@pytest.mark.asyncio
async def test_explicit_class_concept_id_of_other_tenant_rejected_on_update(
    async_client: AsyncClient,
):
    """B1: the update path validates the explicit id too — on both doors."""
    tenant_b, _ = await _tenant_and_headers("ADMIN")
    _, admin_a = await _tenant_and_headers("ADMIN")
    s = _suffix()
    hidden = await _create_concept(
        f"{PREFIX}-uh-{s}", "Update Hidden", ConceptKind.MEDICATION_CLASS, tenant_id=tenant_b
    )

    med = await async_client.post(
        "/api/v1/catalogs/medication", json={"name": f"Med {s}"}, headers=admin_a
    )
    assert med.status_code == 201, med.text
    med_put = await async_client.put(
        f"/api/v1/catalogs/medication/{med.json()['id']}",
        json={"class_concept_id": str(hidden)},
        headers=admin_a,
    )
    assert med_put.status_code == 400, med_put.text
    domain_put = await async_client.put(
        f"/api/v1/medications/catalog/{med.json()['id']}",
        json={"class_concept_id": str(hidden)},
        headers=admin_a,
    )
    assert domain_put.status_code == 400, domain_put.text

    bio = await async_client.post(
        "/api/v1/catalogs/biomarker",
        json={"slug": f"{PREFIX}-ub-{s}", "name": f"Bio {s}"},
        headers=admin_a,
    )
    assert bio.status_code == 201, bio.text
    bio_patch = await async_client.patch(
        f"/api/v1/biomarkers/{bio.json()['id']}",
        json={"class_concept_id": str(hidden)},
        headers=admin_a,
    )
    assert bio_patch.status_code == 400, bio_patch.text

    anat = await async_client.post(
        "/api/v1/catalogs/anatomy",
        json={"name": f"An {s}", "slug": f"{PREFIX}-ua-{s}"},
        headers=admin_a,
    )
    assert anat.status_code == 201, anat.text
    anat_patch = await async_client.patch(
        f"/api/v1/anatomy/{anat.json()['id']}",
        json={"class_concept_id": str(hidden)},
        headers=admin_a,
    )
    assert anat_patch.status_code == 400, anat_patch.text

    # Nothing was stored: the rows still have no class link.
    got = await async_client.get(f"/api/v1/catalogs/medication/{med.json()['id']}", headers=admin_a)
    assert got.json()["class_concept_id"] is None


@pytest.mark.asyncio
async def test_concept_parent_id_of_other_tenant_rejected(async_client: AsyncClient):
    """B1: ``ConceptCreate.parent_id`` / ``ConceptUpdate.parent_id`` are
    visibility-checked — a tenant cannot parent onto another tenant's
    private concept (create and update, both doors)."""
    tenant_b, _ = await _tenant_and_headers("ADMIN")
    _, admin_a = await _tenant_and_headers("ADMIN")
    s = _suffix()
    hidden = await _create_concept(
        f"{PREFIX}-ph-{s}", "Hidden Parent", ConceptKind.SYMPTOM, tenant_id=tenant_b
    )

    create = await async_client.post(
        "/api/v1/catalogs/concept",
        json={
            "slug": f"{PREFIX}-cp-{s}",
            "name": f"Child {s}",
            "kinds": ["symptom"],
            "tenant_scoped": True,
            "parent_id": str(hidden),
        },
        headers=admin_a,
    )
    assert create.status_code == 400, create.text
    assert "Hidden Parent" not in create.json()["detail"]

    own = await async_client.post(
        "/api/v1/catalogs/concept",
        json={
            "slug": f"{PREFIX}-co-{s}",
            "name": f"Own {s}",
            "kinds": ["symptom"],
            "tenant_scoped": True,
        },
        headers=admin_a,
    )
    assert own.status_code == 201, own.text
    generic_put = await async_client.put(
        f"/api/v1/catalogs/concept/{own.json()['id']}",
        json={"parent_id": str(hidden)},
        headers=admin_a,
    )
    assert generic_put.status_code == 400, generic_put.text
    domain_put = await async_client.put(
        f"/api/v1/concepts/{own.json()['id']}",
        json={"parent_id": str(hidden)},
        headers=admin_a,
    )
    assert domain_put.status_code == 400, domain_put.text
    got = await async_client.get(f"/api/v1/catalogs/concept/{own.json()['id']}", headers=admin_a)
    assert got.json()["parent_id"] is None, "the hidden parent must not be stored"


@pytest.mark.asyncio
async def test_bogus_reference_ids_are_400(async_client: AsyncClient):
    """A well-formed but nonexistent reference id is a typed 400 (never an FK
    500) on every reference-bearing write."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    bogus = str(uuid.uuid4())
    cases = [
        ("medication", {"name": f"M {s}"}),
        ("allergy", {"name": f"A {s}", "category": "FOOD"}),
        ("vaccine", {"name": f"V {s}"}),
        ("biomarker", {"slug": f"{PREFIX}-bg-{s}", "name": f"B {s}"}),
        ("anatomy", {"name": f"An {s}", "slug": f"{PREFIX}-bg-a-{s}"}),
    ]
    for type_, base in cases:
        resp = await async_client.post(
            f"/api/v1/catalogs/{type_}",
            json={**base, "class_concept_id": bogus},
            headers=admin,
        )
        assert resp.status_code == 400, (type_, resp.text)
    parent = await async_client.post(
        "/api/v1/catalogs/concept",
        json={
            "slug": f"{PREFIX}-bgp-{s}",
            "name": f"P {s}",
            "kinds": ["symptom"],
            "tenant_scoped": True,
            "parent_id": bogus,
        },
        headers=admin,
    )
    assert parent.status_code == 400, parent.text
    # …and on the update path (bogus parent → 400, the B3 regression pin).
    own = await async_client.post(
        "/api/v1/catalogs/concept",
        json={
            "slug": f"{PREFIX}-bgo-{s}",
            "name": f"O {s}",
            "kinds": ["symptom"],
            "tenant_scoped": True,
        },
        headers=admin,
    )
    assert own.status_code == 201, own.text
    upd = await async_client.put(
        f"/api/v1/concepts/{own.json()['id']}",
        json={"parent_id": bogus},
        headers=admin,
    )
    assert upd.status_code == 400, upd.text


@pytest.mark.asyncio
async def test_biomarker_bogus_unit_and_class_id_never_500_both_doors(async_client: AsyncClient):
    """B2: bogus ``preferred_unit_id`` / ``class_concept_id`` never escape the
    flush as a raw 500 — typed 400s on the generic and the domain door."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    bogus_unit = str(uuid.uuid4())
    bogus_class = str(uuid.uuid4())
    for idx, path in enumerate(("/api/v1/catalogs/biomarker", "/api/v1/biomarkers/")):
        bad_unit = await async_client.post(
            path,
            json={
                "slug": f"{PREFIX}-bu2-{s}-{idx}",
                "name": f"B {s}",
                "preferred_unit_id": bogus_unit,
            },
            headers=admin,
        )
        assert bad_unit.status_code == 400, (path, bad_unit.text)
        bad_class = await async_client.post(
            path,
            json={
                "slug": f"{PREFIX}-bc2-{s}-{idx}",
                "name": f"B {s}",
                "class_concept_id": bogus_class,
            },
            headers=admin,
        )
        assert bad_class.status_code == 400, (path, bad_class.text)


@pytest.mark.asyncio
async def test_demote_with_colliding_code_is_409_field_code(async_client: AsyncClient):
    """B3: demoting a system medication into a tenant whose rows already own
    the code translates the unique violation into 409 ``field=code``."""
    tenant_a, admin_a = await _tenant_and_headers("ADMIN")
    from tests._auth_helpers import headers_for_claims as _h

    sysadmin_same_tenant = await _h(
        {"sub": f"sa2@{PREFIX}.test", "tenant_id": str(tenant_a), "role": "SYSTEM_ADMIN"}
    )
    s = _suffix()
    code = f"demote-{s}"
    tenant_row = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"Tenant Row {s}", "coding_system": "RxNorm", "code": code},
        headers=admin_a,
    )
    assert tenant_row.status_code == 201, tenant_row.text
    system_row = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": f"System Row {s}", "coding_system": "RxNorm", "code": code},
        headers=sysadmin_same_tenant,
    )
    assert system_row.status_code == 201, system_row.text
    assert system_row.json()["scope"] == "system"

    promote = await async_client.post(
        f"/api/v1/catalogs/medication/{system_row.json()['id']}/promote",
        json={"scope": "tenant"},
        headers=sysadmin_same_tenant,
    )
    assert promote.status_code == 409, promote.text
    assert promote.json()["field"] == "code", promote.text
    # The system row is untouched (rolled back).
    got = await async_client.get(
        f"/api/v1/catalogs/medication/{system_row.json()['id']}", headers=sysadmin_same_tenant
    )
    assert got.json()["scope"] == "system"


@pytest.mark.asyncio
async def test_promote_with_colliding_seed_key_is_409_field_seed_key(async_client: AsyncClient):
    """B3: promoting into system scope when the seed_key is already taken
    there translates the unique violation into 409 ``field=seed_key``."""
    from app.models.enums import CatalogScope
    from app.models.fhir.medication import MedicationCatalog

    tenant_a, _admin_a = await _tenant_and_headers("ADMIN")
    from tests._auth_helpers import headers_for_claims as _h

    sysadmin_same_tenant = await _h(
        {"sub": f"sa3@{PREFIX}.test", "tenant_id": str(tenant_a), "role": "SYSTEM_ADMIN"}
    )
    s = _suffix()
    seed_key = f"medication:wc-seedclash-{s}"
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                MedicationCatalog(
                    name=f"Seeded System {s}",
                    seed_key=seed_key,
                    tenant_id=None,
                    scope=CatalogScope.SYSTEM,
                ),
                MedicationCatalog(
                    name=f"Seeded Tenant {s}",
                    seed_key=seed_key,
                    tenant_id=tenant_a,
                    scope=CatalogScope.TENANT,
                ),
            ]
        )
        await db.commit()

    rows = await async_client.get(
        f"/api/v1/catalogs/medication?search=Seeded Tenant {s}", headers=sysadmin_same_tenant
    )
    assert rows.status_code == 200, rows.text
    items = rows.json()["items"]
    assert len(items) == 1, items
    tenant_row = items[0]
    assert tenant_row["scope"] == "tenant", tenant_row

    promote = await async_client.post(
        f"/api/v1/catalogs/medication/{tenant_row['id']}/promote",
        json={"scope": "system"},
        headers=sysadmin_same_tenant,
    )
    assert promote.status_code == 409, promote.text
    assert promote.json()["field"] == "seed_key", promote.text


@pytest.mark.asyncio
async def test_biomarker_update_explicit_null_clears_references(async_client: AsyncClient):
    """S1: ``preferred_unit_id: null`` / ``class_concept_id: null`` actually
    clear the links (model_fields_set distinguishes "not sent" from "explicit
    null"); untouched keys keep their values."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    unit_id = await _seed_unit(f"{PREFIX}-cu-{s}")
    class_slug = f"{PREFIX}-cu-class-{s}"
    await _create_concept(class_slug, "Clear Class", ConceptKind.BIOMARKER_CLASS)
    created = await async_client.post(
        "/api/v1/catalogs/biomarker",
        json={
            "slug": f"{PREFIX}-clear-{s}",
            "name": f"Clear {s}",
            "preferred_unit_id": str(unit_id),
            "class_concept_slug": class_slug,
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    row_id = created.json()["id"]

    cleared = await async_client.put(
        f"/api/v1/catalogs/biomarker/{row_id}",
        json={"preferred_unit_id": None, "class_concept_id": None},
        headers=admin,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["preferred_unit_id"] is None
    assert cleared.json()["class_concept_id"] is None
    reread = await async_client.get(f"/api/v1/catalogs/biomarker/{row_id}", headers=admin)
    assert reread.json()["preferred_unit_id"] is None
    assert reread.json()["class_concept_slug"] is None

    # An explicit null via the symbol convenience clears too.
    symbol_set = await async_client.put(
        f"/api/v1/catalogs/biomarker/{row_id}",
        json={"preferred_unit_symbol": f"{PREFIX}-cu-{s}"},
        headers=admin,
    )
    assert symbol_set.status_code == 200, symbol_set.text
    assert symbol_set.json()["preferred_unit_id"] == str(unit_id)
    symbol_clear = await async_client.put(
        f"/api/v1/catalogs/biomarker/{row_id}",
        json={"preferred_unit_symbol": None},
        headers=admin,
    )
    assert symbol_clear.status_code == 200, symbol_clear.text
    assert symbol_clear.json()["preferred_unit_id"] is None

    # "Not sent" stays untouched: a patch without reference keys neither
    # restores nor re-clears the (still empty) links.
    untouched = await async_client.put(
        f"/api/v1/catalogs/biomarker/{row_id}",
        json={"description": "touched"},
        headers=admin,
    )
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["preferred_unit_id"] is None
    assert untouched.json()["class_concept_id"] is None


@pytest.mark.asyncio
async def test_anatomy_update_slug_duplicate_409_both_doors(async_client: AsyncClient):
    """S4: ``AnatomyStructureUpdate.slug`` is pre-checked for collisions (the
    table has no unique index) — the same typed 409 the create path returns,
    from both doors; renaming to the row's own slug stays a no-op."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    first = await async_client.post(
        "/api/v1/catalogs/anatomy",
        json={"name": f"First {s}", "slug": f"{PREFIX}-us1-{s}"},
        headers=admin,
    )
    assert first.status_code == 201, first.text
    second = await async_client.post(
        "/api/v1/catalogs/anatomy",
        json={"name": f"Second {s}", "slug": f"{PREFIX}-us2-{s}"},
        headers=admin,
    )
    assert second.status_code == 201, second.text

    generic_put = await async_client.put(
        f"/api/v1/catalogs/anatomy/{second.json()['id']}",
        json={"slug": f"{PREFIX}-us1-{s}"},
        headers=admin,
    )
    assert generic_put.status_code == 409, generic_put.text
    assert generic_put.json()["field"] == "slug", generic_put.text
    domain_patch = await async_client.patch(
        f"/api/v1/anatomy/{second.json()['id']}",
        json={"slug": f"{PREFIX}-us1-{s}"},
        headers=admin,
    )
    assert domain_patch.status_code == 409, domain_patch.text
    assert domain_patch.json()["field"] == "slug", domain_patch.text

    self_rename = await async_client.put(
        f"/api/v1/catalogs/anatomy/{second.json()['id']}",
        json={"slug": f"{PREFIX}-us2-{s}"},
        headers=admin,
    )
    assert self_rename.status_code == 200, self_rename.text
    clean_rename = await async_client.put(
        f"/api/v1/catalogs/anatomy/{second.json()['id']}",
        json={"slug": f"{PREFIX}-us3-{s}"},
        headers=admin,
    )
    assert clean_rename.status_code == 200, clean_rename.text
    assert clean_rename.json()["slug"] == f"{PREFIX}-us3-{s}"


def test_derive_slug_non_latin_names_are_deterministic():
    """A non-Latin name must not collapse everything onto the bare
    ``"vaccine"`` fallback — a deterministic digest suffix keeps distinct
    names distinct (and the same name colliding predictably)."""
    from app.services.catalog.catalog_write_service import _derive_slug

    assert _derive_slug("Hepatitis B Vaccine") == "hepatitis-b-vaccine"
    assert _derive_slug("疫苗") == _derive_slug("疫苗")
    assert _derive_slug("疫苗") != _derive_slug("組み換えワクチン")
    assert _derive_slug("疫苗").startswith("vaccine-")
    assert _derive_slug("") == _derive_slug("")
    assert _derive_slug("疫苗 B") != _derive_slug("疫苗 C")


def test_derive_slug_biomarker_prefix_is_namespaced_and_deterministic():
    """The biomarker derivation shares the vaccine algorithm but namespaces
    the non-Latin digest fallback — equal non-Latin names across catalog
    types never share a slug (each type's fallback is deterministic)."""
    from app.services.catalog.catalog_write_service import _derive_slug

    assert _derive_slug("C Reactive Protein", prefix="biomarker") == "c-reactive-protein"
    assert _derive_slug("血糖", prefix="biomarker") == _derive_slug("血糖", prefix="biomarker")
    assert _derive_slug("血糖", prefix="biomarker").startswith("biomarker-")
    # Same non-Latin name, different catalog prefix → different slugs.
    assert _derive_slug("血糖") != _derive_slug("血糖", prefix="biomarker")
    # Distinct non-Latin names stay distinct within one prefix.
    assert _derive_slug("血糖", prefix="biomarker") != _derive_slug("白血球", prefix="biomarker")


@pytest.mark.asyncio
async def test_user_role_cannot_create_concepts_via_generic_door(async_client: AsyncClient):
    """RBAC parity: the curated-taxonomy rule (USER read-only) holds through
    the generic door too — 403, identical to the domain door."""
    tenant_id, _admin = await _tenant_and_headers("ADMIN")
    from tests._auth_helpers import headers_for_claims as _h

    user = await _h({"sub": f"user@{PREFIX}.test", "tenant_id": str(tenant_id), "role": "USER"})
    s = _suffix()
    body = {
        "slug": f"{PREFIX}-user-{s}",
        "name": f"User Concept {s}",
        "kinds": ["symptom"],
        "tenant_scoped": True,
    }
    generic = await async_client.post("/api/v1/catalogs/concept", json=body, headers=user)
    assert generic.status_code == 403, generic.text
    domain = await async_client.post("/api/v1/concepts", json=body, headers=user)
    assert domain.status_code == 403, domain.text


# ---------------------------------------------------------------------------
# Phase-3 re-check follow-ups: read-path visibility projection on legacy
# links, soft-deleted reference rejection, uniform id-over-slug precedence,
# envelope / 404 pins, migration-enqueue ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_hidden_class_concept_projects_to_null_two_tenants(async_client):
    """Read-path visibility projection: a legacy row (written before 08a22381)
    may carry a class link to another tenant's private concept. A tenant that
    cannot see the referenced concept gets ``class_concept_id`` kept but
    slug/name nulled (get + list doors); the concept's owner still sees them
    (the row here is system-scope, so both tenants can read it)."""
    tenant_owner, admin_owner = await _tenant_and_headers("ADMIN")
    _, admin_other = await _tenant_and_headers("ADMIN")
    s = _suffix()
    hidden_slug = f"{PREFIX}-legacy-{s}"
    hidden_id = await _create_concept(
        hidden_slug, "Legacy Hidden Class", ConceptKind.MEDICATION_CLASS, tenant_id=tenant_owner
    )
    async with AsyncSessionLocal() as db:
        from app.models.enums import CatalogScope
        from app.models.fhir.medication import MedicationCatalog

        row = MedicationCatalog(
            name=f"Legacy Med {s}",
            tenant_id=None,
            scope=CatalogScope.SYSTEM,
            class_concept_id=hidden_id,
        )
        db.add(row)
        await db.commit()
        row_id = row.id

    other = await async_client.get(f"/api/v1/catalogs/medication/{row_id}", headers=admin_other)
    assert other.status_code == 200, other.text
    body = other.json()
    assert body["class_concept_id"] == str(hidden_id)
    assert body["class_concept_slug"] is None
    assert body["class_concept_name"] is None

    listed = await async_client.get(
        f"/api/v1/catalogs/medication?search=Legacy Med {s}", headers=admin_other
    )
    assert listed.status_code == 200
    items = [it for it in listed.json()["items"] if it["id"] == str(row_id)]
    assert items and items[0]["class_concept_slug"] is None

    owner = await async_client.get(f"/api/v1/catalogs/medication/{row_id}", headers=admin_owner)
    assert owner.status_code == 200, owner.text
    assert owner.json()["class_concept_slug"] == hidden_slug
    assert owner.json()["class_concept_name"] == "Legacy Hidden Class"


@pytest.mark.asyncio
async def test_legacy_soft_deleted_class_concept_projects_to_null(async_client):
    """A legacy link to a soft-deleted concept projects to ``None`` for every
    viewer — the id survives, the slug/name never echo a retired row."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    gone_id = await _create_concept(
        f"{PREFIX}-gone-legacy-{s}",
        "Retired Class",
        ConceptKind.MEDICATION_CLASS,
        deleted_at=datetime.now(UTC),
    )
    async with AsyncSessionLocal() as db:
        from app.models.enums import CatalogScope
        from app.models.fhir.medication import MedicationCatalog

        row = MedicationCatalog(
            name=f"Retired Link Med {s}",
            tenant_id=None,
            scope=CatalogScope.SYSTEM,
            class_concept_id=gone_id,
        )
        db.add(row)
        await db.commit()
        row_id = row.id

    got = await async_client.get(f"/api/v1/catalogs/medication/{row_id}", headers=admin)
    assert got.status_code == 200, got.text
    assert got.json()["class_concept_id"] == str(gone_id)
    assert got.json()["class_concept_slug"] is None
    assert got.json()["class_concept_name"] is None


@pytest.mark.asyncio
async def test_legacy_hidden_parent_slug_projects_to_null_two_tenants(async_client):
    """Same projection for the concept ``parent_slug``: a system concept whose
    legacy ``parent_id`` points at tenant B's private concept shows
    ``parent_id`` kept + ``parent_slug`` None to tenant A, and the real slug
    to tenant B (get + list doors)."""
    tenant_owner, admin_owner = await _tenant_and_headers("ADMIN")
    _, admin_other = await _tenant_and_headers("ADMIN")
    s = _suffix()
    parent_slug = f"{PREFIX}-legacy-parent-{s}"
    parent_id = await _create_concept(
        parent_slug, "Legacy Hidden Parent", ConceptKind.SYMPTOM, tenant_id=tenant_owner
    )
    async with AsyncSessionLocal() as db:
        from app.models.enums import CatalogScope

        child = Concept(
            slug=f"{PREFIX}-legacy-child-{s}",
            name=f"Legacy Child {s}",
            primary_kind=ConceptKind.SYMPTOM,
            tenant_id=None,
            scope=CatalogScope.SYSTEM,
            parent_id=parent_id,
        )
        db.add(child)
        await db.flush()
        db.add(ConceptKindTag(concept_id=child.id, kind=ConceptKind.SYMPTOM))
        await db.commit()
        child_id = child.id

    other = await async_client.get(f"/api/v1/catalogs/concept/{child_id}", headers=admin_other)
    assert other.status_code == 200, other.text
    assert other.json()["parent_id"] == str(parent_id)
    assert other.json()["parent_slug"] is None

    listed = await async_client.get(
        f"/api/v1/catalogs/concept?search=Legacy Child {s}", headers=admin_other
    )
    assert listed.status_code == 200
    items = [it for it in listed.json()["items"] if it["id"] == str(child_id)]
    assert items and items[0]["parent_slug"] is None

    owner = await async_client.get(f"/api/v1/catalogs/concept/{child_id}", headers=admin_owner)
    assert owner.status_code == 200, owner.text
    assert owner.json()["parent_slug"] == parent_slug


@pytest.mark.parametrize(
    ("generic_type", "base_factory"),
    [
        ("medication", lambda s: {"name": f"SD Med {s}"}),
        ("allergy", lambda s: {"name": f"SD Allergen {s}", "category": "FOOD"}),
        ("vaccine", lambda s: {"name": f"SD Vaccine {s}"}),
        ("biomarker", lambda s: {"slug": f"{PREFIX}-sd-bio-{s}", "name": f"SD Bio {s}"}),
        ("anatomy", lambda s: {"name": f"SD Organ {s}", "slug": f"{PREFIX}-sd-an-{s}"}),
    ],
)
@pytest.mark.asyncio
async def test_soft_deleted_class_concept_id_rejected_on_create_and_update(
    async_client, generic_type, base_factory
):
    """An explicit ``class_concept_id`` pointing at a soft-deleted concept is
    a typed 400 on create AND update — the target must be live, on every
    catalog type."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    gone_id = await _create_concept(
        f"{PREFIX}-sd-concept-{generic_type}-{s}",
        "Soft Deleted Class",
        ConceptKind.MEDICATION_CLASS,
        deleted_at=datetime.now(UTC),
    )

    create = await async_client.post(
        f"/api/v1/catalogs/{generic_type}",
        json={**base_factory(s), "class_concept_id": str(gone_id)},
        headers=admin,
    )
    assert create.status_code == 400, (generic_type, create.text)

    made = await async_client.post(
        f"/api/v1/catalogs/{generic_type}", json=base_factory(s), headers=admin
    )
    assert made.status_code == 201, (generic_type, made.text)
    update = await async_client.put(
        f"/api/v1/catalogs/{generic_type}/{made.json()['id']}",
        json={"class_concept_id": str(gone_id)},
        headers=admin,
    )
    assert update.status_code == 400, (generic_type, update.text)


@pytest.mark.asyncio
async def test_soft_deleted_parent_id_rejected_on_create_and_update(async_client):
    """A concept ``parent_id`` pointing at a soft-deleted concept is a typed
    400 on create and update, both doors."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    gone_id = await _create_concept(
        f"{PREFIX}-sd-parent-{s}",
        "Soft Deleted Parent",
        ConceptKind.SYMPTOM,
        deleted_at=datetime.now(UTC),
    )

    create = await async_client.post(
        "/api/v1/catalogs/concept",
        json={
            "slug": f"{PREFIX}-sd-child-{s}",
            "name": f"SD Child {s}",
            "kinds": ["symptom"],
            "tenant_scoped": True,
            "parent_id": str(gone_id),
        },
        headers=admin,
    )
    assert create.status_code == 400, create.text

    own = await async_client.post(
        "/api/v1/catalogs/concept",
        json={
            "slug": f"{PREFIX}-sd-own-{s}",
            "name": f"SD Own {s}",
            "kinds": ["symptom"],
            "tenant_scoped": True,
        },
        headers=admin,
    )
    assert own.status_code == 201, own.text
    generic_put = await async_client.put(
        f"/api/v1/catalogs/concept/{own.json()['id']}",
        json={"parent_id": str(gone_id)},
        headers=admin,
    )
    assert generic_put.status_code == 400, generic_put.text
    domain_put = await async_client.put(
        f"/api/v1/concepts/{own.json()['id']}",
        json={"parent_id": str(gone_id)},
        headers=admin,
    )
    assert domain_put.status_code == 400, domain_put.text


@pytest.mark.parametrize(
    ("generic_type", "kind", "create_factory", "update_path"),
    [
        (
            "medication",
            ConceptKind.MEDICATION_CLASS,
            lambda s, a: {"name": f"Prece Med {s}"},
            lambda s, row: f"/api/v1/catalogs/medication/{row['id']}",
        ),
        (
            "allergy",
            ConceptKind.MEDICATION_CLASS,
            lambda s, a: {"name": f"Prece Allergen {s}", "category": "FOOD"},
            lambda s, row: f"/api/v1/catalogs/allergy/{row['id']}",
        ),
        (
            "vaccine",
            ConceptKind.VACCINE_CLASS,
            lambda s, a: {"name": f"Prece Vaccine {s}"},
            lambda s, row: f"/api/v1/catalogs/vaccine/{row['id']}",
        ),
        (
            "anatomy",
            ConceptKind.ANATOMY_CLASS,
            lambda s, a: {"name": f"Prece Organ {s}", "slug": f"{PREFIX}-prece-an-{s}"},
            lambda s, row: f"/api/v1/catalogs/anatomy/{row['id']}",
        ),
    ],
)
@pytest.mark.asyncio
async def test_class_concept_update_id_wins_over_slug_including_null(
    async_client, generic_type, kind, create_factory, update_path
):
    """Precedence is uniform on updates: a sent ``class_concept_id`` wins over
    a ``class_concept_slug`` sent alongside — including an explicit ``null``
    id, which clears the link instead of resolving the slug (the biomarker
    semantics, now mirrored by medication/allergy/vaccine/anatomy)."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()
    slug_a = f"{PREFIX}-prece-a-{generic_type}-{s}"
    slug_b = f"{PREFIX}-prece-b-{generic_type}-{s}"
    id_a = await _create_concept(slug_a, f"Prece A {generic_type}", kind)
    await _create_concept(slug_b, f"Prece B {generic_type}", kind)

    created = await async_client.post(
        f"/api/v1/catalogs/{generic_type}",
        json={**create_factory(s, admin), "class_concept_slug": slug_a},
        headers=admin,
    )
    assert created.status_code == 201, (generic_type, created.text)
    assert created.json()["class_concept_slug"] == slug_a
    row = created.json()

    # Explicit null id + slug: the null wins → cleared, NOT slug_b.
    cleared = await async_client.put(
        update_path(s, row),
        json={"class_concept_id": None, "class_concept_slug": slug_b},
        headers=admin,
    )
    assert cleared.status_code == 200, (generic_type, cleared.text)
    assert cleared.json()["class_concept_id"] is None, generic_type
    assert cleared.json()["class_concept_slug"] is None, generic_type

    # Explicit id + other slug: the id wins → slug_a, never slug_b.
    id_won = await async_client.put(
        update_path(s, row),
        json={"class_concept_id": str(id_a), "class_concept_slug": slug_b},
        headers=admin,
    )
    assert id_won.status_code == 200, (generic_type, id_won.text)
    assert id_won.json()["class_concept_id"] == str(id_a), generic_type
    assert id_won.json()["class_concept_slug"] == slug_a, generic_type


@pytest.mark.asyncio
async def test_update_path_unknown_or_foreign_class_slug_400(async_client):
    """Pins the update path for every class-concept catalog: a bare
    ``class_concept_slug`` that is unknown or another tenant's private slug is
    a typed 400 (never a silent drop), on both doors."""
    tenant_b, _ = await _tenant_and_headers("ADMIN")
    _, admin_a = await _tenant_and_headers("ADMIN")
    s = _suffix()
    foreign = {
        "medication": await _create_concept(
            f"{PREFIX}-upx-med-{s}",
            "Foreign Med Class",
            ConceptKind.MEDICATION_CLASS,
            tenant_id=tenant_b,
        ),
        "biomarker": await _create_concept(
            f"{PREFIX}-upx-bio-{s}",
            "Foreign Bio Class",
            ConceptKind.BIOMARKER_CLASS,
            tenant_id=tenant_b,
        ),
        "anatomy": await _create_concept(
            f"{PREFIX}-upx-an-{s}",
            "Foreign Anatomy Class",
            ConceptKind.ANATOMY_CLASS,
            tenant_id=tenant_b,
        ),
    }

    async def _update(path, payload):
        return await async_client.put(path, json=payload, headers=admin_a)

    med = await async_client.post(
        "/api/v1/catalogs/medication", json={"name": f"UpX Med {s}"}, headers=admin_a
    )
    assert med.status_code == 201, med.text
    unknown = await _update(
        f"/api/v1/catalogs/medication/{med.json()['id']}",
        {"class_concept_slug": f"nope-{s}"},
    )
    assert unknown.status_code == 400, unknown.text
    foreign_med = await _update(
        f"/api/v1/medications/catalog/{med.json()['id']}",
        {"class_concept_slug": f"{PREFIX}-upx-med-{s}"},
    )
    assert foreign_med.status_code == 400, foreign_med.text

    bio = await async_client.post(
        "/api/v1/catalogs/biomarker",
        json={"slug": f"{PREFIX}-upx-biorow-{s}", "name": f"UpX Bio {s}"},
        headers=admin_a,
    )
    assert bio.status_code == 201, bio.text
    unknown_bio = await _update(
        f"/api/v1/catalogs/biomarker/{bio.json()['id']}",
        {"class_concept_slug": f"nope-{s}"},
    )
    assert unknown_bio.status_code == 400, unknown_bio.text
    foreign_bio = await async_client.patch(
        f"/api/v1/biomarkers/{bio.json()['id']}",
        json={"class_concept_slug": f"{PREFIX}-upx-bio-{s}"},
        headers=admin_a,
    )
    assert foreign_bio.status_code == 400, foreign_bio.text

    an = await async_client.post(
        "/api/v1/catalogs/anatomy",
        json={"name": f"UpX Organ {s}", "slug": f"{PREFIX}-upx-anrow-{s}"},
        headers=admin_a,
    )
    assert an.status_code == 201, an.text
    unknown_an = await _update(
        f"/api/v1/catalogs/anatomy/{an.json()['id']}",
        {"class_concept_slug": f"nope-{s}"},
    )
    assert unknown_an.status_code == 400, unknown_an.text
    foreign_an = await async_client.patch(
        f"/api/v1/anatomy/{an.json()['id']}",
        json={"class_concept_slug": f"{PREFIX}-upx-an-{s}"},
        headers=admin_a,
    )
    assert foreign_an.status_code == 400, foreign_an.text

    assert str(foreign["medication"]) not in foreign_med.text


@pytest.mark.asyncio
async def test_delete_envelope_and_concept_missing_404(async_client):
    """Deletes answer the ``{"status", "message"}`` envelope on both doors;
    ``PUT``/``DELETE`` on a missing concept id is a 404 on both doors (the
    typed ``ConceptNotFoundError`` path), never a substring-matched error."""
    _, admin = await _tenant_and_headers("ADMIN")
    s = _suffix()

    generic_row = await async_client.post(
        "/api/v1/catalogs/medication", json={"name": f"Env Med {s}"}, headers=admin
    )
    assert generic_row.status_code == 201, generic_row.text
    generic_delete = await async_client.delete(
        f"/api/v1/catalogs/medication/{generic_row.json()['id']}", headers=admin
    )
    assert generic_delete.status_code == 200, generic_delete.text
    assert set(generic_delete.json()) == {"status", "message"}
    assert generic_delete.json()["status"] == "success"

    domain_row = await async_client.post(
        "/api/v1/medications/catalog", json={"name": f"Env Med 2 {s}"}, headers=admin
    )
    assert domain_row.status_code == 201, domain_row.text
    domain_delete = await async_client.delete(
        f"/api/v1/medications/catalog/{domain_row.json()['id']}", headers=admin
    )
    assert domain_delete.status_code == 200, domain_delete.text
    assert set(domain_delete.json()) == {"status", "message"}
    assert domain_delete.json()["status"] == "success"

    missing = uuid.uuid4()
    generic_put = await async_client.put(
        f"/api/v1/catalogs/concept/{missing}", json={"name": "x"}, headers=admin
    )
    assert generic_put.status_code == 404
    generic_delete = await async_client.delete(f"/api/v1/catalogs/concept/{missing}", headers=admin)
    assert generic_delete.status_code == 404
    domain_put = await async_client.put(
        f"/api/v1/concepts/{missing}", json={"name": "x"}, headers=admin
    )
    assert domain_put.status_code == 404
    domain_delete = await async_client.delete(f"/api/v1/concepts/{missing}", headers=admin)
    assert domain_delete.status_code == 404


def test_biomarker_migration_enqueue_follows_commit():
    """The telemetry migration task is enqueued only AFTER the guarded commit
    in ``update_biomarker`` — a rolled-back update must never leave a
    dangling migration task behind."""
    import inspect

    from app.services.catalog import catalog_write_service as w

    src = inspect.getsource(w.update_biomarker)
    assert src.index("await db.commit()") < src.index("migrate_biomarker_data.delay")


def test_biomarker_retry_migration_enqueue_follows_commit():
    """The ``retry-migration`` endpoint commits the in-progress stamp before
    enqueuing ``migrate_biomarker_data`` — a rolled-back retry must never
    leave a dangling migration task behind (same order as update)."""
    import inspect

    from app.api.v1.endpoints import biomarkers

    src = inspect.getsource(biomarkers.retry_biomarker_migration)
    assert src.index("await db.commit()") < src.index("migrate_biomarker_data.delay")


# ---------------------------------------------------------------------------
# Domain GET doors: legacy hidden-reference echo (two tenants + soft-deleted)
# ---------------------------------------------------------------------------


async def _seed_domain_row(catalog_type: str, s: str, class_concept_id) -> tuple[str, str]:
    """Seed one system-scope catalog row linked to ``class_concept_id`` and
    return ``(id, name)``. System scope so every tenant can read the row
    itself while only the concept's owner can see the referenced concept."""
    from app.models.anatomy_model import AnatomyStructure
    from app.models.enums import CatalogScope
    from app.models.fhir.allergy import AllergyCatalog
    from app.models.fhir.medication import MedicationCatalog
    from app.models.fhir.vaccine import VaccineCatalog

    async with AsyncSessionLocal() as db:
        if catalog_type == "medication":
            row = MedicationCatalog(
                name=f"Domain Med {s}",
                tenant_id=None,
                scope=CatalogScope.SYSTEM,
                class_concept_id=class_concept_id,
            )
        elif catalog_type == "allergy":
            from app.models.enums import AllergyCategory

            row = AllergyCatalog(
                name=f"Domain Allergen {s}",
                category=AllergyCategory.FOOD,
                tenant_id=None,
                scope=CatalogScope.SYSTEM,
                class_concept_id=class_concept_id,
            )
        elif catalog_type == "vaccine":
            row = VaccineCatalog(
                slug=f"{PREFIX}-dom-{catalog_type}-{s}",
                name=f"Domain Vaccine {s}",
                tenant_id=None,
                scope=CatalogScope.SYSTEM,
                class_concept_id=class_concept_id,
            )
        else:
            row = AnatomyStructure(
                slug=f"{PREFIX}-dom-{catalog_type}-{s}",
                name=f"Domain Organ {s}",
                tenant_id=None,
                scope=CatalogScope.SYSTEM,
                class_concept_id=class_concept_id,
            )
        db.add(row)
        await db.commit()
        return str(row.id), row.name


_DOMAIN_DOORS = {
    "medication": (ConceptKind.MEDICATION_CLASS, "/api/v1/medications/catalog"),
    "allergy": (ConceptKind.MEDICATION_CLASS, "/api/v1/allergies/catalog"),
    "vaccine": (ConceptKind.VACCINE_CLASS, "/api/v1/vaccines/catalog"),
    "anatomy": (ConceptKind.ANATOMY_CLASS, "/api/v1/anatomy"),
}


def _items_of(payload) -> list[dict]:
    """Domain list doors: a bare JSON array (medication/allergy/vaccine) or
    the ``{"items": [...]}`` envelope (anatomy)."""
    return payload if isinstance(payload, list) else payload["items"]


@pytest.mark.parametrize("catalog_type", ["medication", "allergy", "vaccine", "anatomy"])
@pytest.mark.asyncio
async def test_domain_get_doors_project_hidden_class_concept(async_client, catalog_type):
    """The domain GET doors apply the same visibility projection as the
    generic ones: a system-scope row whose legacy class link points at
    another tenant's private concept shows ``class_concept_id`` kept with
    slug/name null to every other viewer (GET by id AND list), and the real
    slug/name to the concept's owning tenant."""
    tenant_owner, admin_owner = await _tenant_and_headers("ADMIN")
    _, admin_other = await _tenant_and_headers("ADMIN")
    kind, list_path = _DOMAIN_DOORS[catalog_type]
    s = _suffix()
    hidden_slug = f"{PREFIX}-dom-hidden-{catalog_type}-{s}"
    hidden_name = f"Hidden {catalog_type} Class {s}"
    hidden_id = await _create_concept(hidden_slug, hidden_name, kind, tenant_id=tenant_owner)
    row_id, row_name = await _seed_domain_row(catalog_type, s, hidden_id)

    other = await async_client.get(f"{list_path}/{row_id}", headers=admin_other)
    assert other.status_code == 200, (catalog_type, other.text)
    body = other.json()
    assert body["id"] == row_id, catalog_type
    assert body["class_concept_id"] == str(hidden_id), catalog_type
    assert body["class_concept_slug"] is None, catalog_type
    assert body["class_concept_name"] is None, catalog_type
    if catalog_type == "anatomy":
        assert body["slug"] == f"{PREFIX}-dom-anatomy-{s}", catalog_type

    listed = await async_client.get(f"{list_path}?search={row_name}", headers=admin_other)
    assert listed.status_code == 200, (catalog_type, listed.text)
    mine = [it for it in _items_of(listed.json()) if it["id"] == row_id]
    assert mine, (catalog_type, "row missing from domain list door")
    assert mine[0]["class_concept_id"] == str(hidden_id), catalog_type
    assert mine[0]["class_concept_slug"] is None, catalog_type
    assert mine[0]["class_concept_name"] is None, catalog_type

    owner = await async_client.get(f"{list_path}/{row_id}", headers=admin_owner)
    assert owner.status_code == 200, (catalog_type, owner.text)
    assert owner.json()["class_concept_slug"] == hidden_slug, catalog_type
    assert owner.json()["class_concept_name"] == hidden_name, catalog_type

    owner_listed = await async_client.get(f"{list_path}?search={row_name}", headers=admin_owner)
    assert owner_listed.status_code == 200, (catalog_type, owner_listed.text)
    owner_mine = [it for it in _items_of(owner_listed.json()) if it["id"] == row_id]
    assert owner_mine, (catalog_type, "row missing from owner's domain list door")
    assert owner_mine[0]["class_concept_slug"] == hidden_slug, catalog_type
    assert owner_mine[0]["class_concept_name"] == hidden_name, catalog_type


@pytest.mark.parametrize("catalog_type", ["medication", "allergy", "vaccine", "anatomy"])
@pytest.mark.asyncio
async def test_domain_get_doors_project_soft_deleted_class_concept(async_client, catalog_type):
    """A legacy domain-row link to a soft-deleted concept projects to null
    slug/name for every viewer on both domain doors — the id survives, the
    retired row's slug/name never echo."""
    _, admin = await _tenant_and_headers("ADMIN")
    kind, list_path = _DOMAIN_DOORS[catalog_type]
    s = _suffix()
    gone_id = await _create_concept(
        f"{PREFIX}-dom-gone-{catalog_type}-{s}",
        f"Retired {catalog_type} Class {s}",
        kind,
        deleted_at=datetime.now(UTC),
    )
    row_id, row_name = await _seed_domain_row(catalog_type, s, gone_id)

    got = await async_client.get(f"{list_path}/{row_id}", headers=admin)
    assert got.status_code == 200, (catalog_type, got.text)
    assert got.json()["class_concept_id"] == str(gone_id), catalog_type
    assert got.json()["class_concept_slug"] is None, catalog_type
    assert got.json()["class_concept_name"] is None, catalog_type

    listed = await async_client.get(f"{list_path}?search={row_name}", headers=admin)
    assert listed.status_code == 200, (catalog_type, listed.text)
    mine = [it for it in _items_of(listed.json()) if it["id"] == row_id]
    assert mine, (catalog_type, "row missing from domain list door")
    assert mine[0]["class_concept_slug"] is None, catalog_type
    assert mine[0]["class_concept_name"] is None, catalog_type


# ---------------------------------------------------------------------------
# Domain AI-reprocess doors: the same hidden-reference projection on POST
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_nlp(monkeypatch):
    """Make the reprocess doors deterministic: the NLP extractor resolves to
    a no-op pass-2 (empty enrichment) for both catalogs. Medication resolves
    the extractor through its module-level import; allergy imports it inside
    the function, so the source module is patched instead."""
    import app.ai.processors.nlp as nlp_mod
    import app.services.clinical.medication_service as med_mod
    from app.ai.schemas.nlp import NewMedicationDefinitions

    class _NoopExtractor:
        async def parse_document_pass_2_medications(self, _wrapped):
            return NewMedicationDefinitions(definitions=[])

    async def _no_extractor(*_a, **_kw):
        raise RuntimeError("no NLP configured")

    async def _noop_extractor(*_a, **_kw):
        return _NoopExtractor()

    monkeypatch.setattr(med_mod, "get_nlp_extractor_from_db", _noop_extractor)
    monkeypatch.setattr(nlp_mod, "get_nlp_extractor_from_db", _no_extractor)


@pytest.mark.parametrize("catalog_type", ["medication", "allergy"])
@pytest.mark.asyncio
async def test_reprocess_door_projects_hidden_class_concept(async_client, _no_nlp, catalog_type):
    """``POST /{medications|allergies}/catalog/{id}/reprocess`` answers through
    the same ``serialize_catalog_row`` projection as the GET doors: a system
    row whose legacy class link points at another tenant's private concept
    shows ``class_concept_id`` kept with slug/name null to every other viewer,
    and the real slug/name to the concept's owning tenant."""
    tenant_owner, admin_owner = await _tenant_and_headers("ADMIN")
    _, admin_other = await _tenant_and_headers("ADMIN")
    s = _suffix()
    hidden_slug = f"{PREFIX}-rp-hidden-{catalog_type}-{s}"
    hidden_name = f"Hidden {catalog_type} Reprocess Class {s}"
    hidden_id = await _create_concept(
        hidden_slug,
        hidden_name,
        ConceptKind.MEDICATION_CLASS,
        tenant_id=tenant_owner,
    )
    row_id, row_name = await _seed_domain_row(catalog_type, s, hidden_id)
    path = {
        "medication": "/api/v1/medications/catalog",
        "allergy": "/api/v1/allergies/catalog",
    }[catalog_type]

    other = await async_client.post(f"{path}/{row_id}/reprocess", headers=admin_other)
    assert other.status_code == 200, (catalog_type, other.text)
    body = other.json()
    assert body["id"] == row_id, catalog_type
    assert body["name"] == row_name, catalog_type
    assert body["class_concept_id"] == str(hidden_id), catalog_type
    assert body["class_concept_slug"] is None, catalog_type
    assert body["class_concept_name"] is None, catalog_type

    owner = await async_client.post(f"{path}/{row_id}/reprocess", headers=admin_owner)
    assert owner.status_code == 200, (catalog_type, owner.text)
    assert owner.json()["class_concept_slug"] == hidden_slug, catalog_type
    assert owner.json()["class_concept_name"] == hidden_name, catalog_type
