"""Phase A — telemetry-aware bridge reads.

DB-backed tests for the biomarker-read alignment: ``GET /observations?biomarker=``
and ``GET /observations/latest`` must return telemetry rows (heart rate / steps /
SpO2 — which live in the TimescaleDB hypertable) alongside FHIR observations,
patient-scoped to the bound instance. Mirrors the ``test_bridge_documents_phase4.py``
DB seeding pattern.

Contract assertions (plan ``mobile-biomarker-read-alignment-2026-08-10.md``):
  * telemetry rows map to the same ObservationPoint shape as FHIR rows,
  * ``/observations/latest`` is **latest-per-biomarker** (merged FHIR + telemetry)
    and the merged list obeys ``limit``,
  * every row carries a flat ``{low, high}`` ``reference_range`` (a FHIR-list
    shaped row normalizes),
  * cross-patient + cross-tenant isolation hold.
"""

import datetime
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.patient import Observation, Patient
from app.models.telemetry_model import TelemetryDataModel
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from sqlalchemy import delete

from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider

LOINC = "http://loinc.org"


async def _purge_tenant_data(*tenant_ids: uuid.UUID) -> None:
    """Delete every tenant-scoped row created for the given tenants.

    ``conftest`` truncates only once at session start, so fixtures that commit
    pollute later tests (notably the seed-export / planner-index suite).
    ``telemetry_data`` has no tenant FK (TimescaleDB limitation) so it must be
    swept manually; the rest are deleted explicitly in dependency order so the
    cleanup does not rely on per-model ON DELETE behaviour.
    """
    tids = [t for t in tenant_ids if t]
    if not tids:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(TelemetryDataModel).where(TelemetryDataModel.tenant_id.in_(tids))
        )
        await db.execute(delete(Observation).where(Observation.tenant_id.in_(tids)))
        await db.execute(
            delete(UserIntegration).where(UserIntegration.tenant_id.in_(tids))
        )
        await db.execute(
            delete(BiomarkerDefinition).where(BiomarkerDefinition.tenant_id.in_(tids))
        )
        await db.execute(delete(Patient).where(Patient.tenant_id.in_(tids)))
        await db.execute(delete(UserModel).where(UserModel.tenant_id.in_(tids)))
        await db.execute(delete(TenantModel).where(TenantModel.id.in_(tids)))
        await db.commit()


def _dt(day: int, hour: int = 12) -> datetime.datetime:
    return datetime.datetime(2026, 8, day, hour, tzinfo=datetime.timezone.utc)


def _get_request(params: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.query_params = params or {}
    return req


@pytest_asyncio.fixture
async def bridge_with_telemetry():
    """Tenant + ADMIN + PatientA/PatientB + bridge integration bound to PatientA.

    Seeds:
      * a telemetry-flagged def ``heart-rate`` (LOINC 8867-4) + 3 PatientA rows
        in ``telemetry_data`` (+ a PatientB row and a second-tenant row),
      * two FHIR defs ``glucose-fasting`` (2345-7, FHIR-list reference_range)
        and ``cholesterol-total`` (2093-3, flat reference_range) each with a
        PatientA Observation.
    """
    tenant_id = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()
    hr_def_id = uuid.uuid4()
    gluc_def_id = uuid.uuid4()
    chol_def_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id, name="Bridge Read T.", slug=f"brt-{tenant_id.hex[:8]}"
            )
        )
        db.add(
            TenantModel(
                id=tenant_b, name="Bridge Read T2", slug=f"brt2-{tenant_b.hex[:8]}"
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"brt-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_a,
                tenant_id=tenant_id,
                name={"family": "A", "given": ["Bound"]},
                gender="UNKNOWN",
            )
        )
        db.add(
            Patient(
                id=patient_b,
                tenant_id=tenant_id,
                name={"family": "B", "given": ["Other"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_a,
                provider="health_assistant_bridge",
                status="ACTIVE",
                user_config={},
            )
        )
        db.add(
            BiomarkerDefinition(
                id=hr_def_id,
                tenant_id=tenant_id,
                slug="heart-rate",
                name="Heart Rate",
                coding_system="loinc",
                code="8867-4",
                is_telemetry=True,
                reference_range_min=60,
                reference_range_max=100,
            )
        )
        # Tenant-scoped overrides (fresh per test, so no collision with the
        # seeded global catalog or a previous test's rows).
        db.add(
            BiomarkerDefinition(
                id=gluc_def_id,
                tenant_id=tenant_id,
                slug="glucose-fasting",
                name="Fasting Glucose",
                coding_system="loinc",
                code="2345-7",
                is_telemetry=False,
                reference_range_min=70,
                reference_range_max=99,
            )
        )
        db.add(
            BiomarkerDefinition(
                id=chol_def_id,
                tenant_id=tenant_id,
                slug="cholesterol-total",
                name="Total Cholesterol",
                coding_system="loinc",
                code="2093-3",
                is_telemetry=False,
                reference_range_min=125,
                reference_range_max=200,
            )
        )
        await db.flush()

        # PatientA heart-rate telemetry (dense series: 3 rows).
        db.add(
            TelemetryDataModel(
                tenant_id=tenant_id,
                device_id="watch-1",
                timestamp=_dt(1, 8),
                slug="heart-rate",
                value=72,
                unit="bpm",
                patient_id=patient_a,
            )
        )
        db.add(
            TelemetryDataModel(
                tenant_id=tenant_id,
                device_id="watch-1",
                timestamp=_dt(2, 8),
                slug="heart-rate",
                value=88,
                unit="bpm",
                patient_id=patient_a,
            )
        )
        db.add(
            TelemetryDataModel(
                tenant_id=tenant_id,
                device_id="watch-1",
                timestamp=_dt(3, 8),
                slug="heart-rate",
                value=95,
                unit="bpm",
                patient_id=patient_a,
            )
        )
        # PatientB heart-rate — must be invisible to the bridge bound to PatientA.
        db.add(
            TelemetryDataModel(
                tenant_id=tenant_id,
                device_id="watch-2",
                timestamp=_dt(1, 9),
                slug="heart-rate",
                value=130,
                unit="bpm",
                patient_id=patient_b,
            )
        )
        # Second-tenant heart-rate (NULL patient_id, single-patient tenant) — cross-tenant isolation.
        db.add(
            TelemetryDataModel(
                tenant_id=tenant_b,
                device_id="watch-3",
                timestamp=_dt(1, 9),
                slug="heart-rate",
                value=60,
                unit="bpm",
                patient_id=None,
            )
        )

        # FHIR observations: glucose (FHIR-list reference_range) + cholesterol (flat).
        db.add(
            Observation(
                tenant_id=tenant_id,
                patient_id=patient_a,
                biomarker_id=gluc_def_id,
                status="final",
                code={
                    "coding": [{"system": LOINC, "code": "2345-7"}],
                    "text": "Fasting Glucose",
                },
                subject={"reference": f"Patient/{patient_a}"},
                effective_datetime=_dt(2, 7),
                value_quantity={"value": 85, "unit": "mg/dL"},
                raw_value=85,
                normalized_value=85,
                relative_score=0.52,
                reference_range=[
                    {
                        "low": {"value": 70, "unit": "mg/dL"},
                        "high": {"value": 99, "unit": "mg/dL"},
                    }
                ],
            )
        )
        db.add(
            Observation(
                tenant_id=tenant_id,
                patient_id=patient_a,
                biomarker_id=gluc_def_id,
                status="final",
                code={
                    "coding": [{"system": LOINC, "code": "2345-7"}],
                    "text": "Fasting Glucose",
                },
                subject={"reference": f"Patient/{patient_a}"},
                effective_datetime=_dt(6, 7),
                value_quantity={"value": 92, "unit": "mg/dL"},
                raw_value=92,
                normalized_value=92,
                reference_range={"low": 70, "high": 99},
            )
        )
        db.add(
            Observation(
                tenant_id=tenant_id,
                patient_id=patient_a,
                biomarker_id=chol_def_id,
                status="final",
                code={
                    "coding": [{"system": LOINC, "code": "2093-3"}],
                    "text": "Total Cholesterol",
                },
                subject={"reference": f"Patient/{patient_a}"},
                effective_datetime=_dt(4, 7),
                value_quantity={"value": 180, "unit": "mg/dL"},
                raw_value=180,
                normalized_value=180,
                reference_range={"low": 125, "high": 200},
            )
        )
        await db.commit()

    try:
        yield {
            "tenant_id": tenant_id,
            "tenant_b": tenant_b,
            "integration_id": integration_id,
            "patient_a": patient_a,
            "patient_b": patient_b,
            "hr_def_id": hr_def_id,
            "gluc_def_id": gluc_def_id,
            "chol_def_id": chol_def_id,
        }
    finally:
        await _purge_tenant_data(tenant_id, tenant_b)


async def _load_integration(integration_id) -> UserIntegration:
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(UserIntegration).where(UserIntegration.id == integration_id)
        )
        return res.scalar_one()


@pytest.mark.asyncio
async def test_observations_by_telemetry_code_returns_series(bridge_with_telemetry):
    """GET /observations?biomarker=8867-4 returns PatientA's telemetry rows
    mapped to the ObservationPoint shape, patient + tenant scoped."""
    ctx = bridge_with_telemetry
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="observations",
        method="GET",
        request=_get_request({"biomarker": "8867-4"}),
    )

    rows = result["data"]
    assert len(rows) == 3, (
        "only PatientA's heart-rate rows (PatientB + other tenant must be invisible)"
    )
    assert all(row["biomarker_slug"] == "heart-rate" for row in rows)
    assert all(row["biomarker_id"] == str(ctx["hr_def_id"]) for row in rows)
    assert all(row["patient_id"] == str(ctx["patient_a"]) for row in rows)

    row = rows[0]  # newest first
    assert row["code"]["coding"][0]["code"] == "8867-4"
    assert row["code"]["coding"][0]["system"] == LOINC
    assert row["raw_value"] == 95
    assert row["normalized_value"] == 95
    assert row["normalized_unit"] == "bpm"
    assert row["value_quantity"] == {"value": 95, "unit": "bpm"}
    assert row["effective_datetime"] == _dt(3, 8).isoformat()
    assert row["reference_range"] == {"low": 60, "high": 100}, (
        "flat {low, high} from the def range"
    )
    assert row["relative_score"] == 0.875, "(95 - 60) / (100 - 60) clamped"


@pytest.mark.asyncio
async def test_observations_by_telemetry_since_until(bridge_with_telemetry):
    """The telemetry series honours since/until like the FHIR path."""
    ctx = bridge_with_telemetry
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="observations",
        method="GET",
        request=_get_request(
            {
                "biomarker": "8867-4",
                "since": "2026-08-02T00:00:00Z",
                "until": "2026-08-02T23:59:59Z",
            }
        ),
    )

    rows = result["data"]
    assert len(rows) == 1
    assert rows[0]["raw_value"] == 88


@pytest.mark.asyncio
async def test_observations_resolves_by_slug(bridge_with_telemetry):
    """`biomarker=` also accepts the slug (telemetry identity)."""
    ctx = bridge_with_telemetry
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="observations",
        method="GET",
        request=_get_request({"biomarker": "heart-rate"}),
    )

    assert len(result["data"]) == 3
    assert all(row["biomarker_slug"] == "heart-rate" for row in result["data"])


@pytest.mark.asyncio
async def test_observations_fhir_path_flattens_reference_range(bridge_with_telemetry):
    """Non-telemetry biomarkers keep the FHIR path; the FHIR-list reference_range
    shape normalizes to flat {low, high}."""
    ctx = bridge_with_telemetry
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="observations",
        method="GET",
        request=_get_request({"biomarker": "2345-7"}),
    )

    rows = result["data"]
    assert len(rows) == 2, "both glucose observations, patient-scoped"
    assert all(row["biomarker_slug"] == "glucose-fasting" for row in rows)
    assert all(row["reference_range"] == {"low": 70, "high": 99} for row in rows), (
        "both the FHIR-list-shaped and the flat row normalize to {low, high}"
    )


@pytest.mark.asyncio
async def test_latest_is_per_biomarker_and_merges_telemetry(bridge_with_telemetry):
    """GET /observations/latest returns one row per biomarker (telemetry + FHIR
    merged), not the latest N rows overall."""
    ctx = bridge_with_telemetry
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="observations/latest",
        method="GET",
        request=_get_request({"limit": "50"}),
    )

    rows = result["data"]
    slugs = sorted(row["biomarker_slug"] for row in rows)
    assert slugs == ["cholesterol-total", "glucose-fasting", "heart-rate"], (
        "one row per biomarker despite the dense heart-rate series"
    )
    assert all(row["reference_range"] is not None for row in rows)
    assert all(row["patient_id"] == str(ctx["patient_a"]) for row in rows), (
        "PatientB's heart-rate (130) must not leak"
    )

    hr = next(row for row in rows if row["biomarker_slug"] == "heart-rate")
    assert hr["raw_value"] == 95, "telemetry latest (Aug 3)"
    assert hr["code"]["coding"][0]["code"] == "8867-4"
    glucose = next(row for row in rows if row["biomarker_slug"] == "glucose-fasting")
    assert glucose["raw_value"] == 92, "FHIR latest (Aug 6)"


@pytest.mark.asyncio
async def test_latest_respects_merged_limit(bridge_with_telemetry):
    """The merged FHIR + telemetry list is capped at the requested limit."""
    ctx = bridge_with_telemetry
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="observations/latest",
        method="GET",
        request=_get_request({"limit": "2"}),
    )

    assert len(result["data"]) == 2


# --------------------------------------------------------------------------- #
#  Legacy NULL-patient telemetry + single-patient-tenant fallback             #
# --------------------------------------------------------------------------- #


async def _seed_patient_telemetry(n_patients: int) -> dict:
    """Seed a tenant with ``n_patients`` Patient rows, a bridge integration
    bound to the first patient, a telemetry ``heart-rate`` def, and two
    telemetry rows for the bound patient's slug: one attributed to the bound
    patient, one legacy (``patient_id=NULL``). Used to exercise the
    single-patient-tenant fallback in ``_patient_scope_predicate``.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    hr_def_id = uuid.uuid4()
    slug = f"heart-rate-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id, name="Legacy T.", slug=f"lgcy-{tenant_id.hex[:8]}"
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"lgcy-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        patient_ids: list[uuid.UUID] = []
        for i in range(n_patients):
            pid = uuid.uuid4()
            patient_ids.append(pid)
            db.add(
                Patient(
                    id=pid,
                    tenant_id=tenant_id,
                    name={"family": f"P{i}", "given": ["X"]},
                    gender="UNKNOWN",
                )
            )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_ids[0],
                provider="health_assistant_bridge",
                status="ACTIVE",
                user_config={},
            )
        )
        db.add(
            BiomarkerDefinition(
                id=hr_def_id,
                tenant_id=tenant_id,
                slug=slug,
                name="Heart Rate",
                coding_system="loinc",
                code="8867-4",
                is_telemetry=True,
                reference_range_min=60,
                reference_range_max=100,
            )
        )
        await db.flush()
        # Attributed to the bound patient.
        db.add(
            TelemetryDataModel(
                tenant_id=tenant_id,
                device_id="watch-1",
                timestamp=_dt(1, 8),
                slug=slug,
                value=72,
                unit="bpm",
                patient_id=patient_ids[0],
            )
        )
        # Legacy NULL-patient row (same tenant) — the fallback candidate.
        db.add(
            TelemetryDataModel(
                tenant_id=tenant_id,
                device_id="watch-0",
                timestamp=_dt(1, 9),
                slug=slug,
                value=60,
                unit="bpm",
                patient_id=None,
            )
        )
        await db.commit()
    return {
        "tenant_id": tenant_id,
        "integration_id": integration_id,
        "slug": slug,
        "bound_patient": patient_ids[0],
        "n_patients": n_patients,
    }


@pytest.mark.asyncio
async def test_legacy_null_patient_telemetry_visible_in_single_patient_tenant():
    """In a tenant with exactly one patient, the legacy NULL-patient_id row is
    matched by the single-patient fallback (mirrors migrate_biomarker_data)."""
    ctx = await _seed_patient_telemetry(n_patients=1)
    try:
        integration = await _load_integration(ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        result = await provider.handle_api_request(
            integration=integration,
            path="observations",
            method="GET",
            request=_get_request({"biomarker": ctx["slug"]}),
        )

        assert len(result["data"]) == 2, "attributed + legacy NULL row (fallback)"
    finally:
        await _purge_tenant_data(ctx["tenant_id"])


@pytest.mark.asyncio
async def test_legacy_null_patient_telemetry_hidden_in_multi_patient_tenant():
    """In a multi-patient tenant the fallback does NOT apply: only the row
    attributed to the bound patient is visible (cross-patient isolation)."""
    ctx = await _seed_patient_telemetry(n_patients=2)
    try:
        integration = await _load_integration(ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        result = await provider.handle_api_request(
            integration=integration,
            path="observations",
            method="GET",
            request=_get_request({"biomarker": ctx["slug"]}),
        )

        assert len(result["data"]) == 1, (
            "legacy NULL row excluded in multi-patient tenant"
        )
        assert result["data"][0]["patient_id"] == str(ctx["bound_patient"])
    finally:
        await _purge_tenant_data(ctx["tenant_id"])


@pytest.mark.asyncio
async def test_latest_dedups_across_sources(bridge_with_telemetry):
    """A biomarker with rows in BOTH stores appears once in /observations/latest
    (the newer row wins). We simulate residue by adding a FHIR observation for
    the heart-rate biomarker alongside its telemetry rows."""
    ctx = bridge_with_telemetry
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    # Plant a FHIR observation for the heart-rate def (normally telemetry-only)
    # to mimic migration residue: both stores now hold heart-rate for PatientA.
    async with AsyncSessionLocal() as db:
        db.add(
            Observation(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_a"],
                biomarker_id=ctx["hr_def_id"],
                status="final",
                code={
                    "coding": [{"system": LOINC, "code": "8867-4"}],
                    "text": "Heart Rate",
                },
                subject={"reference": f"Patient/{ctx['patient_a']}"},
                effective_datetime=_dt(5, 8),
                value_quantity={"value": 80, "unit": "bpm"},
                raw_value=80,
                normalized_value=80,
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration,
        path="observations/latest",
        method="GET",
        request=_get_request({"limit": "50"}),
    )

    hr_rows = [r for r in result["data"] if r["biomarker_slug"] == "heart-rate"]
    assert len(hr_rows) == 1, "deduped across FHIR + telemetry, no double-count"
    # The FHIR row (Aug 5, value 80) is newer than the telemetry latest (Aug 3, 95).
    assert hr_rows[0]["raw_value"] == 80
