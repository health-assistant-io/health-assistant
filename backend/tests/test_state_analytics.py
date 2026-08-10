"""Tests for the STATE-aware analytics paths (plan Step 7).

Covers:
1. ``_state_observation_status`` — Normal/Abnormal from is_normal set.
2. ``_get_observation_status`` STATE branch (early return, never float()s).
3. ``get_biomarker_state_history`` — chronological state list.
4. ``get_multi_state_history`` — per-component tracks.
"""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import (
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerState,
)
from app.models.enums import BiomarkerValueType, CatalogScope, CodingSystem
from app.models.fhir.patient import Observation, Patient
from app.models.tenant_model import TenantModel
from app.services.analytics_service import (
    _get_observation_status,
    _state_observation_status,
    get_biomarker_state_history,
    get_multi_state_history,
)


V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


# ---------------------------------------------------------------------------
# Pure unit tests for _state_observation_status (no DB)
# ---------------------------------------------------------------------------


def _state(code, system, display):
    return SimpleNamespace(code=code, system=system, display=display)


def _allowed(state, is_normal=False, sort_order=0):
    return SimpleNamespace(state=state, is_normal=is_normal, sort_order=sort_order)


def _bio(allowed, multi=False):
    return SimpleNamespace(
        slug="sars-cov-2-pcr",
        value_type="state",
        supports_multi_state=multi,
        allowed_states=allowed,
    )


def _obs(value_cc=None, component=None):
    return SimpleNamespace(value_codeable_concept=value_cc, component=component)


def _cc(code, system=V3):
    return {"coding": [{"code": code, "system": system}]}


def _comp(code, value_cc):
    return {"code": {"coding": [{"code": code}]}, "valueCodeableConcept": value_cc}


POS = _state("POS", V3, "Positive")
NEG = _state("NEG", V3, "Negative")
POS_NEG_BIO = _bio([_allowed(POS, is_normal=False), _allowed(NEG, is_normal=True)])


def test_state_status_normal_when_in_normal_set():
    obs = _obs(value_cc=_cc("NEG"))
    assert _state_observation_status(POS_NEG_BIO, obs) == "Normal"


def test_state_status_abnormal_when_outside_normal_set():
    obs = _obs(value_cc=_cc("POS"))
    assert _state_observation_status(POS_NEG_BIO, obs) == "Abnormal"


def test_state_status_unknown_when_value_missing():
    obs = _obs()  # no valueCodeableConcept
    assert _state_observation_status(POS_NEG_BIO, obs) == "Unknown"


def test_state_status_defaults_to_normal_when_no_normal_set_configured():
    """A STATE biomarker with no is_normal=True rows returns Normal for any
    allowed value (avoids flagging unknown/neutral states)."""
    bio = _bio([_allowed(POS), _allowed(NEG)])  # neither is_normal
    obs = _obs(value_cc=_cc("POS"))
    assert _state_observation_status(bio, obs) == "Normal"


def test_state_status_multi_returns_abnormal_if_any_component_non_normal():
    bio = _bio(
        [_allowed(POS, is_normal=False), _allowed(NEG, is_normal=True)], multi=True
    )
    obs = _obs(
        component=[
            _comp("staph", _cc("POS")),  # abnormal
            _comp("e-coli", _cc("NEG")),  # normal
        ]
    )
    assert _state_observation_status(bio, obs) == "Abnormal"


def test_state_status_multi_all_normal_returns_normal():
    bio = _bio(
        [_allowed(POS, is_normal=False), _allowed(NEG, is_normal=True)], multi=True
    )
    obs = _obs(
        component=[
            _comp("org1", _cc("NEG")),
            _comp("org2", _cc("NEG")),
        ]
    )
    assert _state_observation_status(bio, obs) == "Normal"


# ---------------------------------------------------------------------------
# _get_observation_status STATE branch (no float() on state values)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_observation_status_state_branch_short_circuits():
    """A STATE biomarker observation never reaches the numeric pipeline —
    no float() attempted on a valueCodeableConcept. Pre-fix this silently
    returned "Normal" via the try/except; post-fix it returns the
    is_normal-set verdict."""
    obs = _obs(value_cc=_cc("POS"))
    obs.biomarker = POS_NEG_BIO
    obs.interpretation = None
    obs.relative_score = None
    obs.reference_range = None

    status = await _get_observation_status("SARS-CoV-2 PCR", "POS", obs)
    assert status == "Abnormal"


@pytest.mark.asyncio
async def test_get_observation_status_quantity_unchanged():
    """A QUANTITY biomarker observation still routes through the numeric
    pipeline — the STATE branch is skipped when value_type != 'state'."""
    obs = _obs(value_cc=None)
    obs.biomarker = SimpleNamespace(
        slug="glucose",
        value_type="quantity",
        allowed_states=[],
    )
    obs.interpretation = None
    obs.relative_score = None
    obs.reference_range = [{"low": {"value": 3.9}, "high": {"value": 5.5}}]

    status = await _get_observation_status("Glucose", 7.5, obs)
    assert status == "High"


# ---------------------------------------------------------------------------
# get_biomarker_state_history / get_multi_state_history (real DB)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _scrub():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "DELETE FROM fhir_observations WHERE code->>'text' LIKE 'PCR-test%'"
            )
        )
        await session.execute(
            text(
                "DELETE FROM biomarker_allowed_states WHERE biomarker_id IN "
                "(SELECT id FROM biomarker_definitions WHERE slug LIKE 'state-an-%')"
            )
        )
        await session.execute(
            text("DELETE FROM biomarker_definitions WHERE slug LIKE 'state-an-%'")
        )
        await session.execute(
            text("DELETE FROM biomarker_states WHERE slug LIKE 'state-an-%'")
        )
        await session.execute(
            text("DELETE FROM fhir_patients WHERE name->>'family' = 'StateAn'")
        )
        await session.execute(
            text("DELETE FROM tenants WHERE slug LIKE 'state-an-%'")
        )
        await session.commit()


async def _seed_world(multi=False):
    """Create a tenant + patient + STATE biomarker + POS/NEG states.

    POS/NEG are looked up first (the canonical seed catalog may already be
    loaded by other tests in the session); falls back to insert under the
    state-an-* slug namespace when absent.
    """
    from sqlalchemy import select as sa_select

    async with AsyncSessionLocal() as session:
        tenant = TenantModel(
            id=uuid4(), name="StateAn", slug=f"state-an-{uuid4().hex[:8]}"
        )
        session.add(tenant)
        await session.flush()
        patient = Patient(
            id=uuid4(),
            tenant_id=tenant.id,
            name={"family": "StateAn", "given": ["T"]},
            gender="UNKNOWN",
        )
        session.add(patient)

        async def _get_or_create_state(code, display):
            existing = (
                await session.execute(
                    sa_select(BiomarkerState).where(
                        BiomarkerState.code == code, BiomarkerState.system == V3
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            state = BiomarkerState(
                slug=f"state-an-{code.lower()}-{uuid4().hex[:6]}",
                code=code,
                system=V3,
                display=display,
            )
            session.add(state)
            await session.flush()
            return state

        pos = await _get_or_create_state("POS", "Positive")
        neg = await _get_or_create_state("NEG", "Negative")
        bio = BiomarkerDefinition(
            slug=f"state-an-bio-{uuid4().hex[:8]}",
            coding_system=CodingSystem.LOINC,
            name="PCR",
            aliases=[],
            is_telemetry=False,
            value_type=BiomarkerValueType.STATE,
            supports_multi_state=multi,
            scope=CatalogScope.SYSTEM,
        )
        session.add(bio)
        await session.flush()
        session.add_all(
            [
                BiomarkerAllowedState(
                    biomarker_id=bio.id, state_id=pos.id, is_normal=False, sort_order=0
                ),
                BiomarkerAllowedState(
                    biomarker_id=bio.id, state_id=neg.id, is_normal=True, sort_order=1
                ),
            ]
        )
        await session.commit()
        return tenant.id, patient.id, bio.id, bio.slug


async def _add_observation(tenant_id, patient_id, bio_id, *, value_cc, ts, component=None):
    async with AsyncSessionLocal() as session:
        obs = Observation(
            tenant_id=tenant_id,
            status="final",
            code={"text": "PCR-test"},
            subject={"reference": f"Patient/{patient_id}"},
            patient_id=patient_id,
            biomarker_id=bio_id,
            value_codeable_concept=value_cc,
            component=component,
            effective_datetime=ts,
        )
        session.add(obs)
        await session.commit()
        return obs.id


@pytest.mark.asyncio
async def test_biomarker_state_history_chronological():
    tenant_id, patient_id, bio_id, slug = await _seed_world()
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await _add_observation(
        tenant_id, patient_id, bio_id,
        value_cc={"coding": [{"code": "POS", "system": V3}]}, ts=base,
    )
    await _add_observation(
        tenant_id, patient_id, bio_id,
        value_cc={"coding": [{"code": "NEG", "system": V3}]},
        ts=base + timedelta(days=30),
    )
    await _add_observation(
        tenant_id, patient_id, bio_id,
        value_cc={"coding": [{"code": "POS", "system": V3}]},
        ts=base + timedelta(days=60),
    )

    history = await get_biomarker_state_history(
        str(tenant_id), str(patient_id), slug
    )
    assert len(history) == 3
    # Chronological order
    assert history[0]["state_code"] == "POS"
    assert history[1]["state_code"] == "NEG"
    assert history[2]["state_code"] == "POS"
    # is_normal resolution
    assert history[0]["is_normal"] is False
    assert history[1]["is_normal"] is True
    assert history[0]["display"] == "Positive"


@pytest.mark.asyncio
async def test_biomarker_state_history_empty_for_quantity():
    """get_biomarker_state_history returns [] for a QUANTITY biomarker
    (numeric trends live in get_biomarker_trends)."""
    async with AsyncSessionLocal() as session:
        bio = BiomarkerDefinition(
            slug=f"state-an-qty-{uuid4().hex[:8]}",
            coding_system=CodingSystem.LOINC,
            name="Q",
            aliases=[],
            value_type=BiomarkerValueType.QUANTITY,
            scope=CatalogScope.SYSTEM,
        )
        session.add(bio)
        await session.commit()
        bio_slug = bio.slug

    try:
        history = await get_biomarker_state_history(
            str(uuid4()), str(uuid4()), bio_slug
        )
        assert history == []
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM biomarker_definitions WHERE slug = :s"),
                {"s": bio_slug},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_multi_state_history_returns_per_component_tracks():
    tenant_id, patient_id, bio_id, slug = await _seed_world(multi=True)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    component = [
        {
            "code": {"coding": [{"code": "staph"}]},
            "valueCodeableConcept": {"coding": [{"code": "POS", "system": V3}]},
        },
        {
            "code": {"coding": [{"code": "e-coli"}]},
            "valueCodeableConcept": {"coding": [{"code": "NEG", "system": V3}]},
        },
    ]
    await _add_observation(
        tenant_id, patient_id, bio_id,
        value_cc=None, ts=base, component=component,
    )

    tracks = await get_multi_state_history(
        str(tenant_id), str(patient_id), slug
    )
    assert set(tracks.keys()) == {"staph", "e-coli"}
    assert tracks["staph"][0]["state_code"] == "POS"
    assert tracks["staph"][0]["is_normal"] is False
    assert tracks["e-coli"][0]["state_code"] == "NEG"
    assert tracks["e-coli"][0]["is_normal"] is True


# ---------------------------------------------------------------------------
# get_biomarker_trends — STATE branch (regression test for the bug where
# every state observation was silently dropped because all numeric columns
# are NULL for STATE biomarkers).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_biomarker_trends_includes_state_observations():
    """STATE observations must appear in the trends response (previously
    they were silently dropped because raw_value/normalized_value/
    value_quantity are all NULL for categorical rows)."""
    from app.services.analytics_service import get_biomarker_trends

    tenant_id, patient_id, bio_id, slug = await _seed_world()
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await _add_observation(
        tenant_id, patient_id, bio_id,
        value_cc={"coding": [{"code": "POS", "system": V3, "display": "Positive"}]},
        ts=base,
    )
    await _add_observation(
        tenant_id, patient_id, bio_id,
        value_cc={"coding": [{"code": "NEG", "system": V3, "display": "Negative"}]},
        ts=base + timedelta(days=30),
    )

    async with AsyncSessionLocal() as session:
        trends = await get_biomarker_trends(
            tenant_id=str(tenant_id),
            patient_id=str(patient_id),
            biomarker_codes=slug,
            period="all-time",
            db=session,
        )
    points = trends.get("biomarkers", {}).get(slug, [])
    assert len(points) == 2, f"expected 2 state points, got {len(points)}"

    # Chronological order (the trends loop preserves DB order which is the
    # SELECT order — verifiable by date).
    points_sorted = sorted(points, key=lambda p: p["date"])
    assert points_sorted[0]["state"] == "POS"
    assert points_sorted[0]["state_display"] == "Positive"
    assert points_sorted[0]["state_is_normal"] is False
    assert points_sorted[0]["status"] == "Abnormal"
    assert points_sorted[0]["value_type"] == "state"

    assert points_sorted[1]["state"] == "NEG"
    assert points_sorted[1]["state_display"] == "Negative"
    assert points_sorted[1]["state_is_normal"] is True
    assert points_sorted[1]["status"] == "Normal"

    # The ``value`` field carries the human-readable display so the existing
    # history-table badge (which reads trendRow.value) shows something useful
    # even before the frontend branches on state_display.
    assert points_sorted[0]["value"] == "Positive"


@pytest.mark.asyncio
async def test_get_biomarker_trends_quantity_unchanged():
    """Sanity: the QUANTITY branch is untouched by the state-handling fix.
    A numeric biomarker still returns numeric ``value`` + status, and the
    new state_* fields are absent/null."""
    from app.services.analytics_service import get_biomarker_trends

    async with AsyncSessionLocal() as session:
        tenant = TenantModel(id=uuid4(), name="Q", slug=f"qty-t-{uuid4().hex[:8]}")
        session.add(tenant)
        await session.flush()
        patient = Patient(
            id=uuid4(),
            tenant_id=tenant.id,
            name={"family": "Q", "given": ["T"]},
            gender="UNKNOWN",
        )
        session.add(patient)
        bio = BiomarkerDefinition(
            slug=f"qty-trends-{uuid4().hex[:8]}",
            coding_system=CodingSystem.LOINC,
            name="Glucose-T",
            aliases=[],
            value_type=BiomarkerValueType.QUANTITY,
            reference_range_min=70,
            reference_range_max=99,
            scope=CatalogScope.SYSTEM,
        )
        session.add(bio)
        await session.flush()
        obs = Observation(
            id=uuid4(),
            tenant_id=tenant.id,
            status="final",
            code={"text": "Glucose"},
            subject={"reference": f"Patient/{patient.id}"},
            patient_id=patient.id,
            biomarker_id=bio.id,
            value_quantity={"value": 95.0, "unit": "mg/dL"},
            raw_value=95.0,
            effective_datetime=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        session.add(obs)
        await session.commit()
        tenant_id, patient_id, slug = tenant.id, patient.id, bio.slug

    try:
        async with AsyncSessionLocal() as session:
            trends = await get_biomarker_trends(
                tenant_id=str(tenant_id),
                patient_id=str(patient_id),
                biomarker_codes=slug,
                period="all-time",
                db=session,
            )
        points = trends.get("biomarkers", {}).get(slug, [])
        assert len(points) == 1
        assert points[0]["value"] == 95.0
        assert points[0]["value_type"] == "quantity"
        # State fields are null on QUANTITY rows.
        assert points[0]["state"] is None
        assert points[0]["state_display"] is None
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM biomarker_definitions WHERE slug = :s"),
                {"s": slug},
            )
            await session.execute(
                text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
            )
            await session.commit()

