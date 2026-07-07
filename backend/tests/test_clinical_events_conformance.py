"""Phase 1 conformance tests for Clinical Events.

Pins the correctness fixes from the clinical-events architecture plan
(``dev/plans/clinical-events-architecture-2026-07-07.md``):

1. **Soft-delete** — ``DELETE`` tombstones (sets ``deleted_at``); subsequent
   ``GET`` returns 404 and ``GET /clinical-events`` excludes it. The row must
   still exist in the DB (the FHIR facade relies on this for ``410 Gone``).
2. **Lifecycle notifications** — create / resolve / delete each emit a
   notification through the single emit_event_notification chokepoint.
3. **FHIR Condition search-param conformance** — the F8 contract: every param
   advertised in ``facade.search_params.RESOURCE_PARAMS['Condition']`` must
   actually filter results. Previously 5 of 8 silently no-op'd.
"""
import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventType,
    EventExaminationLink,
)
from app.models.concept_model import Concept
from app.models.enums import ClinicalEventStatus, CodingSystem, Gender
from app.models.examination_model import ExaminationModel
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _tenant_and_headers():
    """Create an isolated tenant + SYSTEM_ADMIN JWT headers for it."""
    tid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tid, name="CE Conf", slug=f"ce-conf-{tid}"))
        await db.commit()
    token = create_access_token(
        {
            "sub": "ceconf@test.local",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tid),
            "role": "SYSTEM_ADMIN",
        }
    )
    return tid, {"Authorization": f"Bearer {token}"}


async def _make_patient(tenant_id):
    pid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=pid,
                tenant_id=tenant_id,
                name={"family": "Conf", "given": ["Test"]},
                gender=Gender.UNKNOWN,
            )
        )
        await db.commit()
    return pid


async def _make_type(
    tenant_id, *, slug, name, category_concept_id=None, **template_fields
):
    tid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            ClinicalEventType(
                id=tid,
                slug=slug,
                name=name,
                tenant_id=tenant_id,
                category_concept_id=category_concept_id,
                **template_fields,
            )
        )
        await db.commit()
    return tid


async def _make_concept(tenant_id, *, slug, name):
    cid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(Concept(id=cid, slug=slug, name=name, tenant_id=tenant_id))
        await db.commit()
    return cid


def _bundle_ids(bundle):
    return {e["resource"]["id"] for e in (bundle.get("entry") or [])}


# ---------------------------------------------------------------------------
# 1. Soft-delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_delete_tombstones_and_excludes(async_client):
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)

    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "title": "Soft-delete me",
            "status": "ACTIVE",
        },
    )
    assert create.status_code == 200, create.text
    eid = create.json()["id"]

    dele = await async_client.delete(
        f"/api/v1/clinical-events/{eid}", headers=headers
    )
    assert dele.status_code == 200

    # GET returns 404 (check_event_access filters deleted_at).
    got = await async_client.get(
        f"/api/v1/clinical-events/{eid}", headers=headers
    )
    assert got.status_code == 404

    # List excludes the tombstoned event.
    listed = await async_client.get(
        f"/api/v1/clinical-events?patient_id={patient_id}", headers=headers
    )
    assert listed.status_code == 200
    assert all(ev["id"] != eid for ev in listed.json())

    # The row still physically exists with deleted_at set (facade 410 Gone).
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(ClinicalEvent).where(ClinicalEvent.id == uuid.UUID(eid))
            )
        ).scalar_one_or_none()
        assert row is not None, "soft-delete must not remove the row"
        assert row.deleted_at is not None


# ---------------------------------------------------------------------------
# 2. Lifecycle notifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_notifications_emitted(async_client):
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)

    with patch(
        "app.services.clinical_event_service.emit_event_notification",
        new=AsyncMock(),
    ) as mock_emit:
        create = await async_client.post(
            "/api/v1/clinical-events",
            headers=headers,
            json={
                "patient_id": str(patient_id),
                "title": "Lifecycle",
                "status": "ACTIVE",
            },
        )
        assert create.status_code == 200, create.text
        eid = create.json()["id"]

        # Non-resolve edit → "updated".
        await async_client.put(
            f"/api/v1/clinical-events/{eid}",
            headers=headers,
            json={"title": "Lifecycle (edited)"},
        )
        # Resolve transition → "resolved".
        await async_client.put(
            f"/api/v1/clinical-events/{eid}",
            headers=headers,
            json={"status": "RESOLVED"},
        )
        # Delete → "deleted".
        await async_client.delete(
            f"/api/v1/clinical-events/{eid}", headers=headers
        )

    actions = [call.args[1] for call in mock_emit.call_args_list]
    assert "created" in actions
    assert "updated" in actions
    assert "resolved" in actions
    assert "deleted" in actions


@pytest.mark.asyncio
async def test_non_resolve_update_emits_updated_not_resolved(async_client):
    """An update that doesn't flip status to RESOLVED must not emit 'resolved'."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)

    with patch(
        "app.services.clinical_event_service.emit_event_notification",
        new=AsyncMock(),
    ) as mock_emit:
        create = await async_client.post(
            "/api/v1/clinical-events",
            headers=headers,
            json={
                "patient_id": str(patient_id),
                "title": "Stay active",
                "status": "ACTIVE",
            },
        )
        eid = create.json()["id"]
        await async_client.put(
            f"/api/v1/clinical-events/{eid}",
            headers=headers,
            json={"description": "still going"},
        )
    actions = [call.args[1] for call in mock_emit.call_args_list]
    assert "resolved" not in actions
    assert "updated" in actions


# ---------------------------------------------------------------------------
# 3. FHIR Condition search-param conformance
# ---------------------------------------------------------------------------


async def _seed_two_conditions(tenant_id, patient_id):
    """Seed an ACTIVE pregnancy + a RESOLVED pain event for search tests.

    ``clinical_event_types.slug`` is globally unique (no tenant scoping), so
    the slugs are namespaced with the tenant's short suffix to stay isolated
    across tests in the same session.
    """
    suffix = str(tenant_id)[:8]
    cat_id = await _make_concept(
        tenant_id, slug=f"reproductive-health-{suffix}", name="Reproductive"
    )
    preg_type = await _make_type(
        tenant_id,
        slug=f"pregnancy-{suffix}",
        name="Pregnancy",
        category_concept_id=cat_id,
    )
    pain_type = await _make_type(
        tenant_id, slug=f"pain-{suffix}", name="Pain", category_concept_id=None
    )

    preg_id = uuid.uuid4()
    pain_id = uuid.uuid4()
    exam_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            ClinicalEvent(
                id=preg_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                type_id=preg_type,
                status=ClinicalEventStatus.ACTIVE,
                title="Pregnancy",
                coding_system=CodingSystem.SNOMED,
                code="SNOMED-123",
                onset_date=datetime.datetime(
                    2026, 1, 15, tzinfo=datetime.timezone.utc
                ),
            )
        )
        db.add(
            ClinicalEvent(
                id=pain_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                type_id=pain_type,
                status=ClinicalEventStatus.RESOLVED,
                title="Back pain",
                onset_date=datetime.datetime(
                    2025, 6, 1, tzinfo=datetime.timezone.utc
                ),
            )
        )
        # An examination linked to the pregnancy for the `encounter` param.
        db.add(
            ExaminationModel(
                id=exam_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                examination_date=datetime.date(2026, 2, 1),
            )
        )
        db.add(
            EventExaminationLink(
                event_id=preg_id, examination_id=exam_id, reason="Booking visit"
            )
        )
        await db.commit()
    return preg_id, pain_id, exam_id


@pytest.mark.asyncio
async def test_condition_search_patient_and_subject(async_client):
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    preg_id, pain_id, _ = await _seed_two_conditions(tenant_id, patient_id)

    for param in ("patient", "subject"):
        bundle = await async_client.get(
            f"/api/v1/fhir/R4/Condition?{param}={patient_id}", headers=headers
        )
        assert bundle.status_code == 200, bundle.text
        ids = _bundle_ids(bundle.json())
        assert ids == {str(preg_id), str(pain_id)}


@pytest.mark.asyncio
async def test_condition_search_clinical_status(async_client):
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    preg_id, pain_id, _ = await _seed_two_conditions(tenant_id, patient_id)

    bundle = await async_client.get(
        f"/api/v1/fhir/R4/Condition?patient={patient_id}&clinical-status=active",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(preg_id)}

    bundle = await async_client.get(
        f"/api/v1/fhir/R4/Condition?patient={patient_id}&clinical-status=resolved",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(pain_id)}


@pytest.mark.asyncio
async def test_condition_search_code(async_client):
    """Condition.code is a String column on ClinicalEvent — must use direct
    equality, not the JSONB CodeableConcept containment path."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    preg_id, pain_id, _ = await _seed_two_conditions(tenant_id, patient_id)

    bundle = await async_client.get(
        f"/api/v1/fhir/R4/Condition?patient={patient_id}&code=SNOMED-123",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(preg_id)}

    # system|code form — code segment is honored.
    bundle = await async_client.get(
        f"/api/v1/fhir/R4/Condition?patient={patient_id}"
        "&code=http://snomed.info/sct|SNOMED-123",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(preg_id)}


@pytest.mark.asyncio
async def test_condition_search_category(async_client):
    """Condition.category maps to the event type's category concept slug."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    preg_id, pain_id, _ = await _seed_two_conditions(tenant_id, patient_id)
    cat_slug = f"reproductive-health-{str(tenant_id)[:8]}"

    bundle = await async_client.get(
        f"/api/v1/fhir/R4/Condition?patient={patient_id}&category={cat_slug}",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(preg_id)}


@pytest.mark.asyncio
async def test_condition_search_onset_date(async_client):
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    preg_id, pain_id, _ = await _seed_two_conditions(tenant_id, patient_id)

    bundle = await async_client.get(
        f"/api/v1/fhir/R4/Condition?patient={patient_id}&onset-date=gt2025-12-01",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(preg_id)}


@pytest.mark.asyncio
async def test_condition_search_encounter(async_client):
    """Condition.encounter maps through EventExaminationLink."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    preg_id, pain_id, exam_id = await _seed_two_conditions(tenant_id, patient_id)

    bundle = await async_client.get(
        f"/api/v1/fhir/R4/Condition?patient={patient_id}&encounter={exam_id}",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(preg_id)}

    # Encounter/<id> reference form also honored.
    bundle = await async_client.get(
        f"/api/v1/fhir/R4/Condition?patient={patient_id}"
        f"&encounter=Encounter/{exam_id}",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(preg_id)}


@pytest.mark.asyncio
async def test_condition_advertised_params_all_honored():
    """F8 contract: every Condition param in RESOURCE_PARAMS must have a real
    handler. We assert the set is exactly the implemented one (no silent
    no-ops). ``subject`` is a synonym for ``patient``; both route through the
    same generic handler."""
    from app.facade.search_params import RESOURCE_PARAMS

    advertised = set(RESOURCE_PARAMS["Condition"])
    # Each of these has a verified handler in crud._build_resource_filter or the
    # Condition param_filter (category/encounter). verification-status was
    # removed because ClinicalEvent has no equivalent.
    expected = {
        "patient",
        "subject",
        "code",
        "clinical-status",
        "onset-date",
        "category",
        "encounter",
    }
    assert advertised == expected, (
        f"Condition advertised params drifted: got {sorted(advertised)}, "
        f"expected {sorted(expected)}. Add a handler or remove from the list."
    )
    assert "verification-status" not in advertised, (
        "verification-status has no ClinicalEvent equivalent and must not be "
        "advertised (silently no-op-ing violates the F8 contract)."
    )


# ---------------------------------------------------------------------------
# 4. link-observation endpoint + pagination (Phase 2)
# ---------------------------------------------------------------------------


async def _make_observation(tenant_id, patient_id):
    from app.models.fhir.patient import Observation

    oid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Observation(
                id=oid,
                tenant_id=tenant_id,
                status="final",
                code={"text": "Test biomarker"},
                subject={"reference": f"Patient/{patient_id}"},
            )
        )
        await db.commit()
    return oid


@pytest.mark.asyncio
async def test_link_observation_endpoint(async_client):
    """POST /clinical-events/{id}/link-observation adds a single observation
    link (closes the asymmetry with link-examination) and rejects duplicates."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    observation_id = await _make_observation(tenant_id, patient_id)

    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "title": "Observation-linkable",
            "status": "ACTIVE",
        },
    )
    assert create.status_code == 200, create.text
    eid = create.json()["id"]

    link = await async_client.post(
        f"/api/v1/clinical-events/{eid}/link-observation",
        headers=headers,
        json={"observation_id": str(observation_id), "notes": "baseline reading"},
    )
    assert link.status_code == 200, link.text
    obs_links = link.json()["observations"]
    assert len(obs_links) == 1
    assert obs_links[0]["observation_id"] == str(observation_id)
    assert obs_links[0]["notes"] == "baseline reading"

    # Duplicate → 400.
    dup = await async_client.post(
        f"/api/v1/clinical-events/{eid}/link-observation",
        headers=headers,
        json={"observation_id": str(observation_id)},
    )
    assert dup.status_code == 400


@pytest.mark.asyncio
async def test_list_events_pagination(async_client):
    """list_events honors limit/offset (default 50, max 200)."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)

    # Seed 3 events directly.
    async with AsyncSessionLocal() as db:
        for i in range(3):
            db.add(
                ClinicalEvent(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    patient_id=patient_id,
                    title=f"Page {i}",
                    status=ClinicalEventStatus.ACTIVE,
                )
            )
        await db.commit()

    full = await async_client.get(
        f"/api/v1/clinical-events?patient_id={patient_id}", headers=headers
    )
    assert full.status_code == 200
    assert len(full.json()) == 3

    page1 = await async_client.get(
        f"/api/v1/clinical-events?patient_id={patient_id}&limit=2&offset=0",
        headers=headers,
    )
    assert len(page1.json()) == 2

    page2 = await async_client.get(
        f"/api/v1/clinical-events?patient_id={patient_id}&limit=2&offset=2",
        headers=headers,
    )
    assert len(page2.json()) == 1

    # limit is clamped to MAX (200); a huge limit must not error.
    big = await async_client.get(
        f"/api/v1/clinical-events?patient_id={patient_id}&limit=99999",
        headers=headers,
    )
    assert big.status_code == 200
    assert len(big.json()) == 3


# ---------------------------------------------------------------------------
# 5. Occurrence CRUD (Phase 3a) — episodes promoted from JSONB to a model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_occurrence_crud(async_client):
    """POST/DELETE /clinical-events/{id}/occurrences manage discrete episodes.

    Occurrences are now first-class rows; to_dict().occurrences sources from
    the model when loaded, preserving the legacy {date,intensity,notes} keys
    while adding the richer fields.
    """
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)

    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "title": "Migraine journey",
            "status": "ACTIVE",
        },
    )
    eid = create.json()["id"]

    occurred = "2026-07-01T08:00:00Z"
    add = await async_client.post(
        f"/api/v1/clinical-events/{eid}/occurrences",
        headers=headers,
        json={
            "occurred_at": occurred,
            "intensity": 8,
            "severity": "severe",
            "notes": "aura then throbbing",
        },
    )
    assert add.status_code == 200, add.text
    occs = add.json()["occurrences"]
    assert len(occs) == 1
    occ = occs[0]
    # Legacy-compatible keys + richer keys both present.
    assert occ["intensity"] == 8
    assert occ["notes"] == "aura then throbbing"
    assert occ["severity"] == "severe"
    assert occ["occurred_at"].startswith("2026-07-01")
    assert occ["date"] == occ["occurred_at"]  # legacy alias
    occurrence_id = occ["id"]

    # Delete the occurrence → list shrinks.
    dele = await async_client.delete(
        f"/api/v1/clinical-events/{eid}/occurrences/{occurrence_id}",
        headers=headers,
    )
    assert dele.status_code == 200, dele.text
    assert dele.json()["occurrences"] == []

    # Deleting a missing occurrence → 404.
    missing = await async_client.delete(
        f"/api/v1/clinical-events/{eid}/occurrences/{occurrence_id}",
        headers=headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_occurrence_intensity_validated(async_client):
    """intensity is constrained to 1..10 (Field ge/le)."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={"patient_id": str(patient_id), "title": "T", "status": "ACTIVE"},
    )
    eid = create.json()["id"]

    bad = await async_client.post(
        f"/api/v1/clinical-events/{eid}/occurrences",
        headers=headers,
        json={"occurred_at": "2026-07-01T08:00:00Z", "intensity": 99},
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_occurrence_jsonb_backfill_and_readback(async_client):
    """A row created with the legacy ``occurrences`` JSONB payload round-trips
    through to_dict() via the JSONB fallback (occurrence_links empty)."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)

    # Create with the legacy occurrences JSONB payload (no model rows written).
    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "title": "Legacy JSONB",
            "status": "ACTIVE",
            "occurrences": [
                {"date": "2026-06-01T00:00:00Z", "intensity": 5, "notes": "old"}
            ],
        },
    )
    assert create.status_code == 200, create.text
    # to_dict falls back to the JSONB column (occurrence_links not populated).
    assert len(create.json()["occurrences"]) == 1
    assert create.json()["occurrences"][0]["notes"] == "old"


# ---------------------------------------------------------------------------
# 6. Anatomy links (Phase 3b) — EventAnatomyLink promoted from dead code
# ---------------------------------------------------------------------------


async def _make_anatomy(tenant_id, *, slug, name):
    from app.models.anatomy_model import AnatomyStructure

    aid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            AnatomyStructure(
                id=aid,
                tenant_id=tenant_id,
                slug=slug,
                name=name,
                is_custom=True,
            )
        )
        await db.commit()
    return aid


@pytest.mark.asyncio
async def test_anatomy_link_crud(async_client):
    """link-anatomy / unlink-anatomy manage structured body-site links, and
    the linked anatomy name is resolved in to_dict().anatomy_links."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    suffix = str(tenant_id)[:8]
    anatomy_id = await _make_anatomy(
        tenant_id, slug=f"lower-back-{suffix}", name="Lower Back"
    )

    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "title": "Pain journey",
            "status": "ACTIVE",
        },
    )
    eid = create.json()["id"]

    link = await async_client.post(
        f"/api/v1/clinical-events/{eid}/link-anatomy",
        headers=headers,
        json={"anatomy_id": str(anatomy_id), "relation_type": "primary_site"},
    )
    assert link.status_code == 200, link.text
    alinks = link.json()["anatomy_links"]
    assert len(alinks) == 1
    assert alinks[0]["anatomy_id"] == str(anatomy_id)
    assert alinks[0]["name"] == "Lower Back"  # resolved via eager-loaded anatomy
    assert alinks[0]["relation_type"] == "primary_site"

    # Duplicate → 400 (unique (event_id, anatomy_id)).
    dup = await async_client.post(
        f"/api/v1/clinical-events/{eid}/link-anatomy",
        headers=headers,
        json={"anatomy_id": str(anatomy_id)},
    )
    assert dup.status_code == 400

    # Unlink → removed.
    dele = await async_client.delete(
        f"/api/v1/clinical-events/{eid}/unlink-anatomy/{anatomy_id}",
        headers=headers,
    )
    assert dele.status_code == 200, dele.text
    assert dele.json()["anatomy_links"] == []

    # Unlink missing → 404.
    missing = await async_client.delete(
        f"/api/v1/clinical-events/{eid}/unlink-anatomy/{anatomy_id}",
        headers=headers,
    )
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# 7. EpisodeOfCare projection + VersionedMixin (Phase 3c)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_episode_of_care_facade_search(async_client):
    """GET /fhir/R4/EpisodeOfCare projects ClinicalEvent rows as journeys and
    honors patient + status search."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    preg_id, pain_id, _ = await _seed_two_conditions(tenant_id, patient_id)

    # patient filter — both journeys returned.
    bundle = await async_client.get(
        f"/api/v1/fhir/R4/EpisodeOfCare?patient={patient_id}", headers=headers
    )
    assert bundle.status_code == 200, bundle.text
    entries = bundle.json().get("entry") or []
    ids = {e["resource"]["id"] for e in entries}
    assert ids == {str(preg_id), str(pain_id)}
    # Each entry is an EpisodeOfCare with a diagnosis referencing Condition/{id}.
    for e in entries:
        res = e["resource"]
        assert res["resourceType"] == "EpisodeOfCare"
        assert res["patient"]["reference"] == f"Patient/{patient_id}"
        assert res["diagnosis"][0]["condition"]["reference"].startswith(
            "Condition/"
        )

    # status filter: 'active' (FHIR) → ACTIVE rows (pregnancy is ACTIVE).
    bundle = await async_client.get(
        f"/api/v1/fhir/R4/EpisodeOfCare?patient={patient_id}&status=active",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(preg_id)}

    # status filter: 'finished' (FHIR) → RESOLVED rows (pain is RESOLVED).
    bundle = await async_client.get(
        f"/api/v1/fhir/R4/EpisodeOfCare?patient={patient_id}&status=finished",
        headers=headers,
    )
    assert bundle.status_code == 200, bundle.text
    assert _bundle_ids(bundle.json()) == {str(pain_id)}


@pytest.mark.asyncio
async def test_episode_of_care_projection_round_trip():
    """to_fhir_episode_of_care_dict() ↔ fhir_to_episode_of_care_orm() round-trip."""
    from app.models.clinical_event import ClinicalEvent
    from app.models.enums import ClinicalEventStatus
    from app.services.fhir_converter import fhir_to_episode_of_care_orm

    event = ClinicalEvent(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        status=ClinicalEventStatus.RESOLVED,
        title="Some journey",
        onset_date=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        resolved_date=datetime.datetime(2026, 4, 1, tzinfo=datetime.timezone.utc),
        version=3,
    )
    eoc = event.to_fhir_episode_of_care_dict()
    assert eoc["resourceType"] == "EpisodeOfCare"
    assert eoc["status"] == "finished"
    assert eoc["diagnosis"][0]["condition"]["reference"] == f"Condition/{event.id}"
    assert eoc["meta"]["versionId"] == "3"

    # Reverse map back to ORM-shape.
    orm = fhir_to_episode_of_care_orm(eoc)
    assert orm["patient_id"] == str(event.patient_id)
    assert orm["status"] == ClinicalEventStatus.RESOLVED


@pytest.mark.asyncio
async def test_version_advances_on_update(async_client):
    """VersionedMixin is now active: each PUT bumps version, and the FHIR
    meta.versionId advances with it (previously always '1')."""
    from app.services.fhir_converter import fhir_to_condition_orm

    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={"patient_id": str(patient_id), "title": "V0", "status": "ACTIVE"},
    )
    eid = create.json()["id"]

    # Initial version is 1.
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(ClinicalEvent).where(ClinicalEvent.id == uuid.UUID(eid))
            )
        ).scalar_one()
        assert row.version == 1

    await async_client.put(
        f"/api/v1/clinical-events/{eid}",
        headers=headers,
        json={"title": "V1"},
    )
    await async_client.put(
        f"/api/v1/clinical-events/{eid}",
        headers=headers,
        json={"title": "V2"},
    )

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(ClinicalEvent).where(ClinicalEvent.id == uuid.UUID(eid))
            )
        ).scalar_one()
        assert row.version == 3  # 1 + two updates

        # FHIR meta.versionId reflects the bumped version.
        cond = row.to_fhir_dict()
        assert cond["meta"]["versionId"] == "3"
        # Reverse converter still parses.
        assert fhir_to_condition_orm(cond)["title"] == "V2"


# ---------------------------------------------------------------------------
# 8. Journey insights endpoint (Phase 4a) — behavior-driving types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insights_endpoint(async_client):
    """GET /clinical-events/{id}/insights computes phase + milestones +
    overdue flag from the event type's template fields."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    type_id = await _make_type(
        tenant_id,
        slug=f"tpl-{str(tenant_id)[:8]}",
        name="Templated",
        default_duration_days=30,
        phases=[
            {"name": "Early", "start_offset_days": 0, "end_offset_days": 14},
            {"name": "Late", "start_offset_days": 14, "end_offset_days": 60},
        ],
        milestones=[
            {
                "name": "Follow-up",
                "date": (
                    datetime.date.today() + datetime.timedelta(days=5)
                ).isoformat(),
                "alert_before_days": 14,
            }
        ],
    )

    # Onset 20 days ago → "Late" phase.
    onset = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=20)
    ).isoformat()
    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "type_id": str(type_id),
            "title": "Templated journey",
            "status": "ACTIVE",
            "onset_date": onset,
        },
    )
    assert create.status_code == 200, create.text
    eid = create.json()["id"]

    resp = await async_client.get(
        f"/api/v1/clinical-events/{eid}/insights", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_phase"]["name"] == "Late"
    assert body["days_since_onset"] == 20
    assert body["is_overdue"] is False  # 20 < 30-day default duration
    assert len(body["upcoming_milestones"]) == 1
    assert body["upcoming_milestones"][0]["name"] == "Follow-up"
    assert body["recommended_biomarkers"] == []  # no correlations seeded


@pytest.mark.asyncio
async def test_insights_overdue_flag(async_client):
    """An ACTIVE journey past its type's default_duration_days is flagged overdue."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    type_id = await _make_type(
        tenant_id,
        slug=f"short-{str(tenant_id)[:8]}",
        name="Short",
        default_duration_days=10,
    )
    onset = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=25)
    ).isoformat()
    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "type_id": str(type_id),
            "title": "Lingering",
            "status": "ACTIVE",
            "onset_date": onset,
        },
    )
    eid = create.json()["id"]

    resp = await async_client.get(
        f"/api/v1/clinical-events/{eid}/insights", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["is_overdue"] is True


@pytest.mark.asyncio
async def test_type_response_includes_template_fields(async_client):
    """The new template fields round-trip through ClinicalEventTypeResponse."""
    tenant_id, headers = await _tenant_and_headers()
    type_id = await _make_type(
        tenant_id,
        slug=f"roundtrip-{str(tenant_id)[:8]}",
        name="Roundtrip",
        default_duration_days=42,
        severity_scale={"type": "numeric", "min": 1, "max": 10},
        phases=[{"name": "Only", "start_offset_days": 0, "end_offset_days": 42}],
    )
    resp = await async_client.get(
        "/api/v1/clinical-events/types", headers=headers
    )
    assert resp.status_code == 200
    matched = [t for t in resp.json() if t["id"] == str(type_id)]
    assert matched, "templated type not returned"
    t = matched[0]
    assert t["default_duration_days"] == 42
    assert t["severity_scale"] == {"type": "numeric", "min": 1, "max": 10}
    assert t["phases"][0]["name"] == "Only"


# ---------------------------------------------------------------------------
# 9. Biomarker ↔ event-type correlation CRUD (Phase 4b)
# ---------------------------------------------------------------------------


async def _make_biomarker(tenant_id, *, slug, name):
    from app.models.biomarker_model import BiomarkerDefinition

    bid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            BiomarkerDefinition(
                id=bid,
                tenant_id=tenant_id,
                slug=slug,
                name=name,
                coding_system="custom",
            )
        )
        await db.commit()
    return bid


@pytest.mark.asyncio
async def test_correlation_crud(async_client):
    """POST/DELETE /clinical-events/types/{id}/biomarkers manage correlations
    (previously seed-script-only). Add is idempotent on the (type, biomarker) pair."""
    tenant_id, headers = await _tenant_and_headers()
    type_id = await _make_type(
        tenant_id, slug=f"corr-{str(tenant_id)[:8]}", name="CorrType"
    )
    bio_id = await _make_biomarker(
        tenant_id, slug=f"hr-{str(tenant_id)[:8]}", name="Heart Rate"
    )

    add = await async_client.post(
        f"/api/v1/clinical-events/types/{type_id}/biomarkers",
        headers=headers,
        json={
            "biomarker_id": str(bio_id),
            "correlation_type": "monitoring",
            "description": "track during journey",
        },
    )
    assert add.status_code == 200, add.text
    body = add.json()
    assert body["biomarker_id"] == str(bio_id)
    assert body["correlation_type"] == "monitoring"
    assert body["description"] == "track during journey"
    assert body["biomarker"]["slug"].startswith("hr-")

    # Idempotent: re-adding updates in place (no duplicate, no 409).
    add2 = await async_client.post(
        f"/api/v1/clinical-events/types/{type_id}/biomarkers",
        headers=headers,
        json={"biomarker_id": str(bio_id), "correlation_type": "diagnostic"},
    )
    assert add2.status_code == 200
    assert add2.json()["correlation_type"] == "diagnostic"

    # GET /types/{id}/biomarkers lists it.
    listed = await async_client.get(
        f"/api/v1/clinical-events/types/{type_id}/biomarkers", headers=headers
    )
    assert listed.status_code == 200
    assert any(b["id"] == str(bio_id) for b in listed.json())

    # DELETE removes it.
    dele = await async_client.delete(
        f"/api/v1/clinical-events/types/{type_id}/biomarkers/{bio_id}",
        headers=headers,
    )
    assert dele.status_code == 200
    listed2 = await async_client.get(
        f"/api/v1/clinical-events/types/{type_id}/biomarkers", headers=headers
    )
    assert listed2.json() == []

    # Deleting again → 404.
    missing = await async_client.delete(
        f"/api/v1/clinical-events/types/{type_id}/biomarkers/{bio_id}",
        headers=headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_correlation_recommendation_in_insights(async_client):
    """A correlation created via the API surfaces in the insights endpoint's
    recommended_biomarkers (the engine wires them together)."""
    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)
    type_id = await _make_type(
        tenant_id, slug=f"rec-{str(tenant_id)[:8]}", name="RecType"
    )
    bio_id = await _make_biomarker(
        tenant_id, slug=f"bp-{str(tenant_id)[:8]}", name="Blood Pressure"
    )
    await async_client.post(
        f"/api/v1/clinical-events/types/{type_id}/biomarkers",
        headers=headers,
        json={"biomarker_id": str(bio_id), "correlation_type": "monitoring"},
    )

    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "type_id": str(type_id),
            "title": "With recs",
            "status": "ACTIVE",
        },
    )
    eid = create.json()["id"]
    resp = await async_client.get(
        f"/api/v1/clinical-events/{eid}/insights", headers=headers
    )
    assert resp.status_code == 200
    recs = resp.json()["recommended_biomarkers"]
    assert len(recs) == 1
    assert recs[0]["slug"].startswith("bp-")


# ---------------------------------------------------------------------------
# 10. Bidirectional exam surfacing (Phase 4c)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_examination_surfaces_linked_clinical_events_and_condition_ref(async_client):
    """An examination linked to a clinical event (via EventExaminationLink):
    (a) surfaces the journey in its REST response ``clinical_events``, and
    (b) its FHIR Encounter projection emits a real ``diagnosis[].condition.
    reference = Condition/{id}`` (closing the documented-but-unimplemented gap)."""
    from sqlalchemy.orm import selectinload

    from app.models.clinical_event import EventExaminationLink
    from app.models.examination_model import ExaminationModel

    tenant_id, headers = await _tenant_and_headers()
    patient_id = await _make_patient(tenant_id)

    # Create exam + event, then link them.
    exam_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            ExaminationModel(
                id=exam_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                examination_date=datetime.date(2026, 3, 1),
            )
        )
        await db.commit()

    create = await async_client.post(
        "/api/v1/clinical-events",
        headers=headers,
        json={
            "patient_id": str(patient_id),
            "title": "Linked journey",
            "status": "ACTIVE",
            "examinations": [{"examination_id": str(exam_id), "reason": "visit"}],
        },
    )
    assert create.status_code == 200, create.text
    eid = create.json()["id"]

    # (a) REST examination detail surfaces the journey (event_links eager-loaded).
    detail = await async_client.get(
        f"/api/v1/examinations/{exam_id}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    ces = detail.json().get("clinical_events", [])
    assert any(c["id"] == eid for c in ces)
    matched = next(c for c in ces if c["id"] == eid)
    assert matched["title"] == "Linked journey"
    assert matched["reason"] == "visit"

    # (b) FHIR Encounter diagnosis references Condition/{id}.
    async with AsyncSessionLocal() as db:
        loaded = (
            await db.execute(
                select(ExaminationModel)
                .where(ExaminationModel.id == exam_id)
                .options(
                    selectinload(ExaminationModel.event_links).selectinload(
                        EventExaminationLink.event
                    )
                )
            )
        ).scalar_one()
        encounter = loaded.to_fhir_dict()
    refs = [
        d["condition"].get("reference")
        for d in (encounter.get("diagnosis") or [])
        if isinstance(d.get("condition"), dict)
    ]
    assert f"Condition/{eid}" in refs
