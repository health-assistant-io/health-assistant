"""Round-trip tests for the taxonomy + anatomy export/import sidecars.

These exercise the work in `dev/plans/export-import-taxonomy-anatomy-2026-07-07.md`:
the unified-taxonomy refactor repointed entity FKs at ``concepts.id`` and the
backup pipeline had to learn to carry concepts / concept_edges / anatomy +
repair the biomarker class and examination category round-trips.

Two layers:
- **Mocked dispatch tests** (fast, no DB) — verify ``restore_sidecar`` routes
  the three new names and ``build_nonfhir_sidecars`` emits them in order.
- **Real-DB integration tests** — build rows in a throwaway tenant, export via
  ``ExportService``, import into a *second* throwaway tenant via
  ``ImportService``, and assert fidelity. These catch upsert / id-remap /
  slug-resolution regressions that mock-only tests miss.

Test isolation: the test DB persists across runs, so every slug (concept,
anatomy, biomarker — all globally or per-tenant unique) is derived from a uuid
via ``_uslug``. Restored rows are looked up via the ``id_remap`` the restore
fills, never by a bare slug that may exist in another tenant from a prior run.
"""

import copy
import datetime as _dt
from datetime import date
from typing import Any, Dict, List
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.anatomy_model import AnatomyRelation, AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
from app.models.enums import (
    AnatomyRelationType,
    CodingSystem,
    ConceptKind,
    ConceptProvenance,
    ConceptRelationType,
    ConceptStatus,
    EdgeApprovalStatus,
    EdgeEndpointType,
    Gender,
    JobStatus,
    QuantityType,
)
from app.models.examination_model import ExaminationModel
from app.models.export_import_job import ExportJobModel, ImportJobModel
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.schemas.biomarker import (
    BiomarkerCreate,
    CatalogImportPayload,
)
from app.services.catalog_import_service import CatalogImportService
from app.services.export_service import ExportService
from app.services.import_service import ImportService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uslug(base: str) -> str:
    """Unique slug (uuid-suffixed) so rows never collide across test runs."""
    return f"{base}-{uuid4().hex[:10]}"


async def _new_tenant(session, slug_prefix: str = "exp-imp-tax") -> UUID:
    tid = uuid4()
    session.add(
        TenantModel(id=tid, name=f"Tenant {tid}", slug=_uslug(slug_prefix))
    )
    await session.commit()
    return tid


async def _new_user(session, tenant_id: UUID) -> UUID:
    """A minimal user row to satisfy export_jobs/import_jobs.user_id FK."""
    from app.models.enums import Role
    from app.models.user_model import UserModel

    uid = uuid4()
    session.add(
        UserModel(
            id=uid,
            tenant_id=tenant_id,
            email=f"user-{uid}@test.local",
            hashed_password="x",
            role=Role.USER,
        )
    )
    await session.commit()
    return uid


def _make_concept(
    slug: str,
    name: str,
    tenant_id: UUID | None,
    *,
    kinds: List[ConceptKind],
    primary_kind: ConceptKind | None = None,
    parent_id: UUID | None = None,
) -> Concept:
    c = Concept(
        tenant_id=tenant_id,
        slug=slug,
        name=name,
        primary_kind=primary_kind or (kinds[0] if kinds else None),
        parent_id=parent_id,
        status=ConceptStatus.ACTIVE,
    )
    for k in kinds:
        c.kind_tags.append(ConceptKindTag(kind=k))
    return c


async def _seed_global_concept(session, slug_base, name, kinds) -> Concept:
    """A global (tenant_id NULL) concept with a unique slug. Read its ``.slug``
    back rather than assuming the base."""
    c = _make_concept(_uslug(slug_base), name, None, kinds=kinds)
    session.add(c)
    await session.flush()
    return c


# ===========================================================================
# Layer 1 — mocked dispatch + wiring (fast, no DB)
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_sidecar_concepts_dispatch():
    """concepts.json routes to _restore_concepts and returns a concepts count."""
    from unittest.mock import AsyncMock

    tid = uuid4()
    db = AsyncMock()
    svc = ImportService(db)
    svc._restore_concepts = AsyncMock(return_value=2)
    payload = {"concepts": [{"id": "x", "slug": "x"}]}
    created, errors, warnings = await svc.restore_sidecar(
        "concepts.json", payload, tid, {}
    )
    assert created == {"concepts": 2}
    assert errors == []
    svc._restore_concepts.assert_awaited_once_with(payload, tid, {})


@pytest.mark.asyncio
async def test_restore_sidecar_anatomy_dispatch():
    from unittest.mock import AsyncMock

    tid = uuid4()
    db = AsyncMock()
    svc = ImportService(db)
    svc._restore_anatomy = AsyncMock(return_value=(3, 1))
    created, errors, _ = await svc.restore_sidecar(
        "anatomy.json", {"structures": [], "relations": []}, tid, {}
    )
    assert created == {"anatomy_structures": 3, "anatomy_relations": 1}
    assert errors == []


@pytest.mark.asyncio
async def test_restore_sidecar_concept_edges_dispatch():
    from unittest.mock import AsyncMock

    tid = uuid4()
    db = AsyncMock()
    svc = ImportService(db)
    svc._restore_concept_edges = AsyncMock(return_value=5)
    created, errors, _ = await svc.restore_sidecar(
        "concept_edges.json", {"edges": []}, tid, {}
    )
    assert created == {"concept_edges": 5}
    assert errors == []


def test_build_nonfhir_sidecars_includes_new_sidecars():
    """The new sidecars are emitted with correct counts; edge goes last."""
    from unittest.mock import MagicMock

    from app.models.enums import ExportScope

    svc = ExportService(MagicMock())
    sidecars, counts, _ = svc.build_nonfhir_sidecars(
        tenant_id=uuid4(),
        patient_ids=None,
        scope=ExportScope.SYSTEM,
        options={},
        examinations=[],
        clinical_events=[],
        clinical_event_types={"types": []},
        biomarker_catalog={"biomarkers": []},
        medication_catalog={"medications": []},
        allergy_catalog={"allergies": []},
        documents=[],
        concepts={"concepts": [{"slug": "a"}, {"slug": "b"}]},
        concept_edges={"edges": [{"id": "e1"}]},
        anatomy={
            "structures": [{"slug": "s1"}],
            "relations": [{"id": "r1"}, {"id": "r2"}],
        },
    )
    assert sidecars["concepts.json"]["concepts"] == [{"slug": "a"}, {"slug": "b"}]
    assert sidecars["anatomy.json"]["structures"] == [{"slug": "s1"}]
    assert sidecars["concept_edges.json"] == {"edges": [{"id": "e1"}]}
    assert counts["concepts"] == 2
    assert counts["anatomy_structures"] == 1
    assert counts["anatomy_relations"] == 2
    assert counts["concept_edges"] == 1
    # Insertion order (Python 3.7+) puts concepts/anatomy before edges.
    keys = list(sidecars.keys())
    assert keys.index("concepts.json") < keys.index("concept_edges.json")
    assert keys.index("anatomy.json") < keys.index("concept_edges.json")


# ===========================================================================
# Layer 2 — real-DB integration
# ===========================================================================


@pytest.fixture
async def db():
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


# ---------- export (gather) ----------


@pytest.mark.asyncio
async def test_gather_concepts_excludes_global(db):
    """gather_concepts returns ONLY tenant-scoped concepts."""
    tid = await _new_tenant(db)
    tenant_slug = _uslug("tenant-panel")
    global_slug = _uslug("global-cat")
    db.add(
        _make_concept(
            tenant_slug, "Tenant Panel", tid, kinds=[ConceptKind.BIOMARKER_PANEL]
        )
    )
    await _seed_global_concept(
        db, "global", "Global", [ConceptKind.BIOMARKER_CLASS]
    )
    await db.commit()

    out = await ExportService(db).gather_concepts(tid)
    slugs = {c["slug"] for c in out["concepts"]}
    assert tenant_slug in slugs
    assert global_slug not in slugs
    panel = next(c for c in out["concepts"] if c["slug"] == tenant_slug)
    assert ConceptKind.BIOMARKER_PANEL.value in panel["kinds"]


@pytest.mark.asyncio
async def test_gather_anatomy_returns_tenant_scoped_only(db):
    """gather_anatomy returns ONLY tenant-scoped structures (global seeded ones
    are excluded — they re-seed on the target), and only the relations between
    exported structures. A custom row in ANOTHER tenant must NOT leak (tenant
    isolation)."""
    tid = await _new_tenant(db)
    other_tid = await _new_tenant(db)
    custom_slug = _uslug("custom-organ")
    tenant_slug = _uslug("tenant-organ")
    global_slug = _uslug("g-organ")
    other_custom_slug = _uslug("other-custom")
    custom = AnatomyStructure(
        tenant_id=tid, name="Custom Organ", slug=custom_slug, is_custom=True
    )
    tenant_only = AnatomyStructure(
        tenant_id=tid, name="Tenant Organ", slug=tenant_slug, is_custom=False
    )
    global_seeded = AnatomyStructure(
        tenant_id=None, name="Global", slug=global_slug, is_custom=False
    )
    other_custom = AnatomyStructure(
        tenant_id=other_tid,
        name="Other Custom",
        slug=other_custom_slug,
        is_custom=True,
    )
    db.add_all([custom, tenant_only, global_seeded, other_custom])
    await db.flush()
    db.add_all(
        [
            AnatomyRelation(
                source_id=custom.id,
                target_id=tenant_only.id,
                relation_type=AnatomyRelationType.PART_OF,
            ),
            AnatomyRelation(
                source_id=custom.id,
                target_id=global_seeded.id,
                relation_type=AnatomyRelationType.PART_OF,
            ),
        ]
    )
    await db.commit()

    out = await ExportService(db).gather_anatomy(tid)
    slugs = {s["slug"] for s in out["structures"]}
    assert {custom_slug, tenant_slug} <= slugs
    assert global_slug not in slugs  # global seeded -> excluded
    assert other_custom_slug not in slugs  # other tenant's custom -> NO leak
    assert len(out["relations"]) == 1  # the cross-to-global rel is dropped


@pytest.mark.asyncio
async def test_gather_biomarker_catalog_emits_class_concept_slug(db):
    """REGRESSION GUARD: export must emit the class concept *slug*."""
    tid = await _new_tenant(db)
    cls = await _seed_global_concept(
        db, "blood-laboratory", "Blood Laboratory", [ConceptKind.BIOMARKER_CLASS]
    )
    bio_slug = _uslug("glucose-fasting")
    db.add(
        BiomarkerDefinition(
            tenant_id=tid,
            slug=bio_slug,
            name="Fasting Glucose",
            coding_system=CodingSystem.LOINC,
            code="2345-7",
            class_concept_id=cls.id,
        )
    )
    await db.commit()

    out = await ExportService(db).gather_biomarker_catalog(tid)
    bio = next(b for b in out["biomarkers"] if b["slug"] == bio_slug)
    assert bio["class_concept_slug"] == cls.slug
    assert bio["category"] == "Blood Laboratory"  # name, for readability


# ---------- import (restore) ----------


@pytest.mark.asyncio
async def test_restore_concepts_upserts_and_remaps(db):
    tid = await _new_tenant(db)
    src_id = uuid4()
    slug = _uslug("imported-panel")
    payload = {
        "concepts": [
            {
                "id": str(src_id),
                "slug": slug,
                "name": "Imported Panel",
                "kinds": [
                    ConceptKind.BIOMARKER_PANEL.value,
                    ConceptKind.DISEASE.value,
                ],
                "primary_kind": ConceptKind.BIOMARKER_PANEL.value,
                "parent_id": None,
                "description": "from backup",
                "coding_system": "custom",
                "code": None,
                "aliases": ["IP"],
                "icon": None,
                "color": "#abc",
                "status": "active",
                "display_order": 0,
                "meta_data": None,
            }
        ]
    }
    id_remap: Dict[str, str] = {}
    count = await ImportService(db)._restore_concepts(payload, tid, id_remap)
    await db.commit()

    assert count == 1
    assert str(src_id) in id_remap
    created = (
        await db.execute(
            select(Concept).where(Concept.id == _uu(id_remap[str(src_id)]))
        )
    ).scalar_one()
    assert created.tenant_id == tid
    assert created.primary_kind == ConceptKind.BIOMARKER_PANEL
    kinds = {t.kind for t in created.kind_tags}
    assert {ConceptKind.BIOMARKER_PANEL, ConceptKind.DISEASE} <= kinds


@pytest.mark.asyncio
async def test_restore_concepts_idempotent(db):
    tid = await _new_tenant(db)
    slug = _uslug("idem-cat")
    base = {
        "concepts": [
            {
                "id": str(uuid4()),
                "slug": slug,
                "name": "Idem Category",
                "kinds": [ConceptKind.EXAMINATION_CATEGORY.value],
                "primary_kind": ConceptKind.EXAMINATION_CATEGORY.value,
            }
        ]
    }
    svc = ImportService(db)
    idr1: Dict[str, str] = {}
    await svc._restore_concepts(base, tid, idr1)
    await db.commit()
    new_id = idr1[base["concepts"][0]["id"]]

    payload2 = copy.deepcopy(base)
    payload2["concepts"][0]["name"] = "Renamed Category"
    idr2: Dict[str, str] = {}
    await svc._restore_concepts(payload2, tid, idr2)
    await db.commit()

    rows = (
        await db.execute(select(Concept).where(Concept.id == _uu(new_id)))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "Renamed Category"
    # remap points at the SAME row (updated, not duplicated)
    assert idr2[payload2["concepts"][0]["id"]] == new_id


@pytest.mark.asyncio
async def test_restore_concepts_defers_parent(db):
    """A child appearing before its parent still links up via the 2nd pass."""
    tid = await _new_tenant(db)
    parent_src = uuid4()
    child_src = uuid4()
    parent_slug = _uslug("parent")
    child_slug = _uslug("child")
    payload = {
        "concepts": [
            {
                "id": str(child_src),
                "slug": child_slug,
                "name": "Child",
                "kinds": [ConceptKind.BIOMARKER_CLASS.value],
                "parent_id": str(parent_src),
            },
            {
                "id": str(parent_src),
                "slug": parent_slug,
                "name": "Parent",
                "kinds": [ConceptKind.BIOMARKER_CLASS.value],
            },
        ]
    }
    id_remap: Dict[str, str] = {}
    await ImportService(db)._restore_concepts(payload, tid, id_remap)
    await db.commit()

    child = (
        await db.execute(select(Concept).where(Concept.id == _uu(id_remap[str(child_src)])))
    ).scalar_one()
    parent = (
        await db.execute(select(Concept).where(Concept.id == _uu(id_remap[str(parent_src)])))
    ).scalar_one()
    assert child.parent_id == parent.id


@pytest.mark.asyncio
async def test_restore_anatomy_relinks_class_concept(db):
    tid = await _new_tenant(db)
    cls = await _seed_global_concept(
        db, "organ", "Organ", [ConceptKind.ANATOMY_CLASS]
    )
    src_struct = uuid4()
    struct_slug = _uslug("widget-organ")
    payload = {
        "structures": [
            {
                "id": str(src_struct),
                "slug": struct_slug,
                "name": "Widget Organ",
                "class_concept_id": str(cls.id),
                "standard_system": None,
                "standard_code": None,
                "description": "custom",
                "is_custom": True,
                "display": None,
            }
        ],
        "relations": [],
    }
    id_remap: Dict[str, str] = {}
    svc = ImportService(db)
    structs, rels = await svc._restore_anatomy(payload, tid, id_remap)
    await db.commit()

    assert structs == 1
    s = (
        await db.execute(
            select(AnatomyStructure).where(
                AnatomyStructure.id == _uu(id_remap[str(src_struct)])
            )
        )
    ).scalar_one()
    assert s.tenant_id == tid
    assert s.class_concept_id == cls.id  # resolved via global existence check


@pytest.mark.asyncio
async def test_restore_concept_edges_remaps_and_skips_dangling(db):
    tid = await _new_tenant(db)
    src_concept = _make_concept(
        _uslug("edge-src"), "Edge Src", tid, kinds=[ConceptKind.SPECIALTY]
    )
    db.add(src_concept)
    await db.flush()
    # a global anatomy structure (the realistic target of a concept edge)
    tgt_organ = AnatomyStructure(
        tenant_id=None,
        name="Heart",
        slug=_uslug("heart"),
        is_custom=False,
    )
    db.add(tgt_organ)
    await db.flush()
    dangling = uuid4()

    id_remap = {str(src_concept.id): str(src_concept.id)}
    payload = {
        "edges": [
            {
                "id": str(uuid4()),
                "src_type": EdgeEndpointType.CONCEPT.value,
                "src_id": str(src_concept.id),
                "dst_type": EdgeEndpointType.ANATOMY.value,
                "dst_id": str(tgt_organ.id),
                "relation": ConceptRelationType.EXAMINES.value,
                "properties": None,
                "evidence": None,
                "source": ConceptProvenance.MANUAL.value,
                "status": EdgeApprovalStatus.APPROVED.value,
            },
            {
                "id": str(uuid4()),
                "src_type": EdgeEndpointType.CONCEPT.value,
                "src_id": str(dangling),
                "dst_type": EdgeEndpointType.ANATOMY.value,
                "dst_id": str(tgt_organ.id),
                "relation": ConceptRelationType.EXAMINES.value,
                "properties": None,
                "evidence": None,
                "source": "manual",
                "status": "approved",
            },
        ]
    }
    count = await ImportService(db)._restore_concept_edges(payload, tid, id_remap)
    await db.commit()

    assert count == 1  # dangling edge skipped
    edges = (
        await db.execute(select(ConceptEdge).where(ConceptEdge.tenant_id == tid))
    ).scalars().all()
    assert len(edges) == 1
    assert edges[0].src_id == src_concept.id
    assert edges[0].dst_id == tgt_organ.id


# ---------- catalog service slug resolution (biomarker regression) ----------


@pytest.mark.asyncio
async def test_catalog_import_resolves_class_concept_slug_not_name(db):
    """Headline regression: a biomarker whose class concept is named
    'Blood Laboratory' must resolve to the concept when the export carries
    class_concept_slug. Without the slug, the legacy name→slug translation
    produced 'blood laboratory' (spaces) and failed."""
    await _new_tenant(db)
    cls = await _seed_global_concept(
        db, "blood-laboratory", "Blood Laboratory", [ConceptKind.BIOMARKER_CLASS]
    )
    unit_sym = _uslug("mgdl")
    db.add(
        Unit(
            symbol=unit_sym,
            name="mg per dL",
            quantity_type=QuantityType.MASS_CONCENTRATION,
        )
    )
    await db.commit()
    bio_slug = _uslug("glucose")
    payload = CatalogImportPayload(
        units=[],
        biomarkers=[
            BiomarkerCreate(
                slug=bio_slug,
                name="Fasting Glucose",
                coding_system=CodingSystem.LOINC,
                code="2345-7",
                category="Blood Laboratory",  # the name (does NOT round-trip)
                class_concept_slug=cls.slug,  # the fix
                preferred_unit_symbol=unit_sym,
            )
        ],
    )
    stats = await CatalogImportService(db).import_catalog(payload)
    assert stats["biomarkers_added"] == 1

    bio = (
        await db.execute(
            select(BiomarkerDefinition).where(BiomarkerDefinition.slug == bio_slug)
        )
    ).scalar_one()
    assert bio.class_concept_id == cls.id


@pytest.mark.asyncio
async def test_catalog_import_legacy_underscore_category_still_works(db):
    """The ontology-catalog-URL path (underscore category, no slug) still works."""
    cls = await _seed_global_concept(
        db, "blood-laboratory", "Blood Laboratory", [ConceptKind.BIOMARKER_CLASS]
    )
    unit_sym = _uslug("mmol")
    db.add(
        Unit(
            symbol=unit_sym,
            name="mmol per L",
            quantity_type=QuantityType.MOLAR_CONCENTRATION,
        )
    )
    await db.commit()
    bio_slug = _uslug("legacy-glucose")
    # biomarker_category_to_concept_slug swaps '_' -> '-'; the concept slug is
    # uuid-suffixed so we pass category = slug with '-' replaced by '_'.
    legacy_category = cls.slug.replace("-", "_")
    payload = CatalogImportPayload(
        units=[],
        biomarkers=[
            BiomarkerCreate(
                slug=bio_slug,
                name="Legacy Glucose",
                category=legacy_category,
                preferred_unit_symbol=unit_sym,
            )
        ],
    )
    stats = await CatalogImportService(db).import_catalog(payload)
    assert stats["biomarkers_added"] == 1
    bio = (
        await db.execute(
            select(BiomarkerDefinition).where(BiomarkerDefinition.slug == bio_slug)
        )
    ).scalar_one()
    assert bio.class_concept_id == cls.id


# ---------- full export -> import round-trip across two tenants ----------


async def _build_source_dataset(session, tid: UUID) -> Dict[str, Any]:
    panel = _make_concept(
        _uslug("my-panel"), "My Panel", tid, kinds=[ConceptKind.BIOMARKER_PANEL]
    )
    exam_cat = _make_concept(
        _uslug("my-exam-cat"),
        "My Exam Category",
        tid,
        kinds=[ConceptKind.EXAMINATION_CATEGORY],
    )
    organ = AnatomyStructure(
        tenant_id=tid,
        name="My Organ",
        slug=_uslug("my-organ"),
        is_custom=True,
    )
    session.add_all([panel, exam_cat, organ])
    await session.flush()
    session.add(
        ConceptEdge(
            tenant_id=tid,
            src_type=EdgeEndpointType.CONCEPT,
            src_id=panel.id,
            dst_type=EdgeEndpointType.ANATOMY,
            dst_id=organ.id,
            relation=ConceptRelationType.MEMBER_OF,
            source=ConceptProvenance.MANUAL,
            status=EdgeApprovalStatus.APPROVED,
        )
    )
    patient = Patient(
        tenant_id=tid,
        name=[{"family": "Round", "given": ["Trip"]}],
        gender=Gender.UNKNOWN,
    )
    session.add(patient)
    await session.flush()
    session.add(
        ExaminationModel(
            tenant_id=tid,
            patient_id=patient.id,
            examination_date=date(2026, 7, 1),
            category_id=exam_cat.id,
            notes="round-trip exam",
        )
    )
    await session.commit()
    return {"panel": panel, "exam_cat": exam_cat, "organ": organ, "patient": patient}


@pytest.mark.asyncio
async def test_full_round_trip_taxonomy_anatomy_exam(db):
    """Export source tenant -> wipe source -> import into a fresh target tenant.
    Simulates the real backup/restore scenario (restore after data loss / onto
    a new install). Source rows are deleted after export so globally-unique
    slugs (anatomy) don't collide with the freshly-created target rows."""
    src_tid = await _new_tenant(db)
    src = await _build_source_dataset(db, src_tid)
    tgt_tid = await _new_tenant(db)

    export_svc = ExportService(db)
    concepts_payload = await export_svc.gather_concepts(src_tid)
    edges_payload = await export_svc.gather_concept_edges(src_tid)
    anatomy_payload = await export_svc.gather_anatomy(src_tid)
    exams_payload = [
        e.to_dict() for e in (await export_svc.gather_examinations(src_tid, None))
    ]
    # capture source ids before wiping
    src_panel_id = src["panel"].id
    src_exam_cat_id = src["exam_cat"].id
    src_organ_id = src["organ"].id
    src_patient_id = src["patient"].id

    # wipe the source tenant's rows (mimics restore-after-data-loss)
    await db.execute(
        ConceptEdge.__table__.delete().where(ConceptEdge.tenant_id == src_tid)
    )
    await db.execute(
        ExaminationModel.__table__.delete().where(ExaminationModel.tenant_id == src_tid)
    )
    await db.execute(
        AnatomyStructure.__table__.delete().where(AnatomyStructure.tenant_id == src_tid)
    )
    await db.execute(Patient.__table__.delete().where(Patient.tenant_id == src_tid))
    await db.execute(Concept.__table__.delete().where(Concept.tenant_id == src_tid))
    await db.commit()

    # In the real pipeline the FHIR bundle restores Patients first and fills
    # id_remap; this test exercises only the sidecar layer, so seed a target
    # patient + the remap entry the exam restore consumes.
    tgt_patient = Patient(
        tenant_id=tgt_tid, name=[{"family": "Round"}], gender=Gender.UNKNOWN
    )
    db.add(tgt_patient)
    await db.flush()

    import_svc = ImportService(db)
    id_remap: Dict[str, str] = {str(src_patient_id): str(tgt_patient.id)}
    n_c = await import_svc._restore_concepts(concepts_payload, tgt_tid, id_remap)
    n_s, _n_r = await import_svc._restore_anatomy(anatomy_payload, tgt_tid, id_remap)
    n_e = await import_svc._restore_examinations(exams_payload, tgt_tid, id_remap)
    n_edges = await import_svc._restore_concept_edges(
        edges_payload, tgt_tid, id_remap
    )
    await db.commit()

    assert n_c == 2
    assert n_s == 1
    assert n_e == 1
    assert n_edges == 1

    tgt_panel = (
        await db.execute(
            select(Concept).where(Concept.id == _uu(id_remap[str(src_panel_id)]))
        )
    ).scalar_one()
    assert tgt_panel.tenant_id == tgt_tid
    tgt_organ = (
        await db.execute(
            select(AnatomyStructure).where(
                AnatomyStructure.id == _uu(id_remap[str(src_organ_id)])
            )
        )
    ).scalar_one()
    assert tgt_organ.tenant_id == tgt_tid
    tgt_edge = (
        await db.execute(
            select(ConceptEdge).where(ConceptEdge.tenant_id == tgt_tid)
        )
    ).scalar_one()
    assert tgt_edge.src_id == tgt_panel.id
    assert tgt_edge.dst_id == tgt_organ.id
    tgt_exam_cat = (
        await db.execute(
            select(Concept).where(Concept.id == _uu(id_remap[str(src_exam_cat_id)]))
        )
    ).scalar_one()
    tgt_exam = (
        await db.execute(
            select(ExaminationModel).where(ExaminationModel.tenant_id == tgt_tid)
        )
    ).scalar_one()
    assert tgt_exam.category_id == tgt_exam_cat.id
    assert tgt_exam.notes == "round-trip exam"


@pytest.mark.asyncio
async def test_full_round_trip_examination_category_global_slug_fallback(db):
    """An exam pointing at a *global* concept (not exported) still resolves on
    import — the global concept is visible to every tenant by id."""
    src_tid = await _new_tenant(db)
    tgt_tid = await _new_tenant(db)
    g_src = await _seed_global_concept(
        db,
        "global-exam-cat",
        "Global Exam Category",
        [ConceptKind.EXAMINATION_CATEGORY],
    )
    patient = Patient(
        tenant_id=src_tid, name=[{"family": "G"}], gender=Gender.UNKNOWN
    )
    db.add(patient)
    await db.flush()
    db.add(
        ExaminationModel(
            tenant_id=src_tid,
            patient_id=patient.id,
            examination_date=date(2026, 7, 2),
            category_id=g_src.id,
        )
    )
    await db.commit()

    export_svc = ExportService(db)
    exams_payload = [
        e.to_dict() for e in (await export_svc.gather_examinations(src_tid, None))
    ]
    # global concepts are deliberately NOT exported
    assert (await export_svc.gather_concepts(src_tid))["concepts"] == []

    n = await ImportService(db)._restore_examinations(exams_payload, tgt_tid, {})
    await db.commit()
    assert n == 1
    tgt_exam = (
        await db.execute(
            select(ExaminationModel).where(ExaminationModel.tenant_id == tgt_tid)
        )
    ).scalar_one()
    assert tgt_exam.category_id == g_src.id  # resolved via global existence


@pytest.mark.asyncio
async def test_full_round_trip_idempotent(db):
    """Two consecutive imports of the same export do not duplicate rows."""
    src_tid = await _new_tenant(db)
    await _build_source_dataset(db, src_tid)
    tgt_tid = await _new_tenant(db)

    export_svc = ExportService(db)
    concepts_payload = await export_svc.gather_concepts(src_tid)
    edges_payload = await export_svc.gather_concept_edges(src_tid)
    anatomy_payload = await export_svc.gather_anatomy(src_tid)

    # wipe source so globally-unique anatomy slug is free for the target
    await db.execute(
        ConceptEdge.__table__.delete().where(ConceptEdge.tenant_id == src_tid)
    )
    await db.execute(
        AnatomyStructure.__table__.delete().where(AnatomyStructure.tenant_id == src_tid)
    )
    await db.execute(Concept.__table__.delete().where(Concept.tenant_id == src_tid))
    await db.commit()

    import_svc = ImportService(db)
    idr1: Dict[str, str] = {}
    await import_svc._restore_concepts(concepts_payload, tgt_tid, idr1)
    await import_svc._restore_anatomy(anatomy_payload, tgt_tid, idr1)
    await import_svc._restore_concept_edges(edges_payload, tgt_tid, idr1)
    await db.commit()

    idr2: Dict[str, str] = {}
    await import_svc._restore_concepts(concepts_payload, tgt_tid, idr2)
    await import_svc._restore_anatomy(anatomy_payload, tgt_tid, idr2)
    n_edges_2 = await import_svc._restore_concept_edges(
        edges_payload, tgt_tid, idr2
    )
    await db.commit()

    concept_rows = (
        await db.execute(
            select(func.count()).select_from(Concept).where(Concept.tenant_id == tgt_tid)
        )
    ).scalar()
    edge_rows = (
        await db.execute(
            select(func.count())
            .select_from(ConceptEdge)
            .where(ConceptEdge.tenant_id == tgt_tid)
        )
    ).scalar()
    struct_rows = (
        await db.execute(
            select(func.count())
            .select_from(AnatomyStructure)
            .where(AnatomyStructure.tenant_id == tgt_tid)
        )
    ).scalar()
    assert concept_rows == 2
    assert struct_rows == 1
    assert edge_rows == 1
    assert n_edges_2 == 1  # second edge import upserts, does not duplicate


# ---------------------------------------------------------------------------
# Regression: completed_at must be a datetime, not an isoformat string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_complete_job_writes_completed_at_datetime(db):
    """REGRESSION: complete_job / fail_job previously passed
    ``datetime.now(...).isoformat()`` (a str) into a ``TIMESTAMP WITH TIME
    ZONE`` column. asyncpg rejects strings, so the export COMPLETED (ZIP
    written) but the job-update crashed — and fail_job crashed the same way,
    leaving the job stuck in PROCESSING forever (UI never showed the error).
    This exercises the real DB write the mocked export tests skip."""
    from app.models.enums import ExportScope, ExportType

    tid = await _new_tenant(db)
    uid = await _new_user(db, tid)
    job = ExportJobModel(
        tenant_id=tid,
        user_id=uid,
        scope=ExportScope.PATIENT,
        export_type=ExportType.FHIR_ONLY,
        status=JobStatus.PENDING,
        progress=0,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    svc = ExportService(db)
    await svc.complete_job(
        job.id,
        file_path="/tmp/x.zip",
        file_size=123,
        counts={"Patient": 1},
    )
    await db.refresh(job)
    assert job.status == JobStatus.COMPLETED
    assert job.progress == 100
    assert job.completed_at is not None
    assert isinstance(job.completed_at, _dt.datetime)


@pytest.mark.asyncio
async def test_export_fail_job_writes_completed_at_datetime(db):
    """fail_job must also persist completed_at as a real datetime so the error
    path actually records the failure (the original bug masked every export
    error by crashing fail_job too)."""
    from app.models.enums import ExportScope, ExportType

    tid = await _new_tenant(db)
    uid = await _new_user(db, tid)
    job = ExportJobModel(
        tenant_id=tid,
        user_id=uid,
        scope=ExportScope.PATIENT,
        export_type=ExportType.FHIR_ONLY,
        status=JobStatus.PROCESSING,
        progress=40,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    svc = ExportService(db)
    await svc.fail_job(job.id, "boom")
    await db.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.error_message == "boom"
    assert isinstance(job.completed_at, _dt.datetime)


@pytest.mark.asyncio
async def test_import_complete_and_fail_job_write_completed_at_datetime(db):
    """Same regression on the import side (_complete_job / _fail_job)."""
    tid = await _new_tenant(db)
    uid = await _new_user(db, tid)
    job = ImportJobModel(
        tenant_id=tid,
        user_id=uid,
        status=JobStatus.PROCESSING,
        progress=50,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    svc = ImportService(db)
    from app.schemas.backup import RestoreResult

    result = RestoreResult(job_id=str(job.id), status=JobStatus.PROCESSING)
    result.created_resources = {"Patient": 1}
    await svc._complete_job(job.id, result)
    await db.refresh(job)
    assert job.status == JobStatus.COMPLETED
    assert isinstance(job.completed_at, _dt.datetime)

    # fail a fresh job
    job2 = ImportJobModel(
        tenant_id=tid, user_id=uid, status=JobStatus.PROCESSING, progress=10
    )
    db.add(job2)
    await db.commit()
    await db.refresh(job2)
    await svc._fail_job(job2.id, "import boom")
    await db.refresh(job2)
    assert job2.status == JobStatus.FAILED
    assert job2.error_message == "import boom"
    assert isinstance(job2.completed_at, _dt.datetime)


# ---------------------------------------------------------------------------
# small util
# ---------------------------------------------------------------------------


def _uu(s: str) -> UUID:
    return UUID(str(s))
