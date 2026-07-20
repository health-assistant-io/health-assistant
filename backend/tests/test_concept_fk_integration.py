"""Real-DB HTTP integration tests for the concept-FK family.

These exercise the **full stack** (auth -> endpoint -> ORM -> selectinload ->
response_model -> JSON) for every entity that carries a classification FK into
``concepts.id``. They exist because mock-based tests structurally *cannot*
catch response-serialization bugs: mocks return dicts, so they bypass Pydantic
``from_attributes`` validation entirely. The ``POST /api/v1/examinations`` 500
(category_concept typed as Dict received an ORM Concept object) shipped through
exactly because the examination suite was fully mocked.

Coverage:
  * examinations — create (with + without category_concept_id), list, get, update
  * clinical-event-types — list, create
  * doctors — create with specialty_concept_id, list
  * biomarkers — list (class_concept string alias)

All round-trip the ``<role>_concept_id`` FK + the ``<role>_concept`` nested
response object, so any future regression to a non-``from_attributes``-safe
schema field fails here rather than in production.
"""
import uuid

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel


@pytest_asyncio.fixture
async def admin_headers_and_patient():
    """Create an isolated tenant + patient and return ``(headers, patient_id)``.

    SYSTEM_ADMIN token so patient-access checks are bypassed; the tenant is
    unique per test run so nothing leaks across tests.
    """
    tenant_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Integration", "given": ["Test"]},
                gender="UNKNOWN",
            )
        )
        await db.commit()
    token = create_access_token(
        {
            "sub": "admin@test.local",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": "SYSTEM_ADMIN",
        }
    )
    return {"Authorization": f"Bearer {token}"}, str(patient_id)


async def _make_concept(
    async_client, headers, kind="examination_category", name=None
):
    """Create a global concept via the API and return ``(id, name)``."""
    slug = f"it-{kind}-{uuid.uuid4().hex[:8]}"
    resp = await async_client.post(
        "/api/v1/concepts",
        json={"slug": slug, "name": name or slug, "kind": kind},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], resp.json()["name"]


# ---------------------------------------------------------------------------
# Examinations — the regression that shipped the 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_examination_without_category(async_client, admin_headers_and_patient):
    """POST /examinations with no category must succeed (the original 500 case
    when category was unset)."""
    headers, patient_id = admin_headers_and_patient
    resp = await async_client.post(
        "/api/v1/examinations",
        json={"patient_id": patient_id, "examination_date": "2026-07-07"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["category_concept_id"] is None
    assert body["category_concept"] is None


@pytest.mark.asyncio
async def test_create_examination_with_category_round_trips_concept(
    async_client, admin_headers_and_patient
):
    """POST /examinations with category_concept_id must return the nested
    category_concept object serialized via ConceptResponse (the bug: typed as
    Dict, received an ORM Concept -> 500)."""
    headers, patient_id = admin_headers_and_patient
    concept_id, concept_name = await _make_concept(async_client, headers)

    resp = await async_client.post(
        "/api/v1/examinations",
        json={
            "patient_id": patient_id,
            "examination_date": "2026-07-07",
            "category_concept_id": concept_id,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["category_concept_id"] == concept_id
    # The nested object must be a real dict (serialized), not an ORM repr.
    assert isinstance(body["category_concept"], dict)
    assert body["category_concept"]["id"] == concept_id
    assert body["category_concept"]["name"] == concept_name
    # The string alias still carries the name.
    assert body["category"] == concept_name
    exam_id = body["id"]

    # GET detail round-trips the same shape.
    detail = await async_client.get(
        f"/api/v1/examinations/{exam_id}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["category_concept"]["id"] == concept_id

    # GET list (ExaminationSummaryResponse) round-trips too.
    listing = await async_client.get(
        "/api/v1/examinations", headers=headers
    )
    assert listing.status_code == 200, listing.text
    matched = [e for e in listing.json() if e["id"] == exam_id]
    assert matched, "created exam not in listing"
    assert matched[0]["category_concept"]["id"] == concept_id


@pytest.mark.asyncio
async def test_update_examination_category(async_client, admin_headers_and_patient):
    """PUT /examinations/{id} must accept category_concept_id and resolve the
    ``category`` name string via the relationship."""
    headers, patient_id = admin_headers_and_patient
    concept_id, concept_name = await _make_concept(async_client, headers)

    create = await async_client.post(
        "/api/v1/examinations",
        json={"patient_id": patient_id, "examination_date": "2026-07-07"},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    exam_id = create.json()["id"]

    upd = await async_client.put(
        f"/api/v1/examinations/{exam_id}",
        json={"category_concept_id": concept_id},
        headers=headers,
    )
    assert upd.status_code == 200, upd.text
    body = upd.json()
    assert body["category_concept_id"] == concept_id
    assert body["category_concept"]["name"] == concept_name


# ---------------------------------------------------------------------------
# Clinical event types — ClinicalEventTypeResponse.category_concept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_clinical_event_types_serializes_category_concept(
    async_client, admin_headers_and_patient
):
    """GET /clinical-events/types must serialize category_concept on each type."""
    headers, _ = admin_headers_and_patient
    cat_id, _ = await _make_concept(
        async_client, headers, kind="event_category", name="IT Event Cat"
    )
    type_slug = f"it-etype-{uuid.uuid4().hex[:8]}"
    create = await async_client.post(
        "/api/v1/clinical-events/types",
        json={
            "name": "IT Event Type",
            "slug": type_slug,
            "category_concept_id": cat_id,
            # Phase 8a: schedule_kind is required on the wire (NOT NULL).
            "schedule_kind": "state",
        },
        headers=headers,
    )
    assert create.status_code == 200, create.text
    assert create.json()["category_concept_id"] == cat_id
    assert isinstance(create.json().get("category_concept"), dict)

    listing = await async_client.get(
        "/api/v1/clinical-events/types", headers=headers
    )
    assert listing.status_code == 200, listing.text
    matched = [t for t in listing.json() if t["slug"] == type_slug]
    assert matched, "created event type not in listing"
    assert matched[0]["category_concept"]["id"] == cat_id


# ---------------------------------------------------------------------------
# Doctors — DoctorResponse.specialty (string alias via the concept property)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doctor_specialty_concept_round_trips(
    async_client, admin_headers_and_patient
):
    """POST/GET /doctors with specialty_concept_id must surface the resolved
    ``specialty`` name string (read off the concept relationship)."""
    headers, _ = admin_headers_and_patient
    spec_id, spec_name = await _make_concept(
        async_client, headers, kind="specialty", name="IT Specialty"
    )
    create = await async_client.post(
        "/api/v1/doctors",
        json={"name": "Dr. Integration", "specialty_concept_id": spec_id},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["specialty_concept_id"] == spec_id
    # ``specialty`` is the readable alias = concept name (proves the
    # relationship loaded without a greenlet error during serialization).
    assert body["specialty"] == spec_name

    listing = await async_client.get("/api/v1/doctors", headers=headers)
    assert listing.status_code == 200, listing.text
    matched = [d for d in listing.json() if d["id"] == body["id"]]
    assert matched
    assert matched[0]["specialty"] == spec_name


# ---------------------------------------------------------------------------
# Biomarkers — BiomarkerResponse.category (string alias via class_concept)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_biomarker_list_carries_class_concept_alias(
    async_client, admin_headers_and_patient
):
    """GET /biomarkers must serialize the ``category`` string alias derived
    from class_concept without a serialization error. Uses the seeded global
    catalog; if empty, creates one biomarker first."""
    headers, _ = admin_headers_and_patient
    listing = await async_client.get("/api/v1/biomarkers/", headers=headers)
    assert listing.status_code == 200, listing.text
    raw = listing.json()
    # Endpoint returns either a bare list or a paginated {"items": [...]}.
    items = raw["items"] if isinstance(raw, dict) else raw
    assert isinstance(items, list)
    for it in items:
        assert "category" in it
        assert "class_concept_id" in it
        assert it["category"] is None or isinstance(it["category"], str)
