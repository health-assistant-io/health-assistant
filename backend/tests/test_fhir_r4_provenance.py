"""Tests for Provenance model + service.

Covers:
- ProvenanceModel.to_fhir_dict() emits valid FHIR R4 Provenance
- Round-trip via fhir_to_provenance_orm
- record_provenance() service records on a target
- agent block construction (F12: resolved Practitioner/Device or display-only fallback)
- Provenance is immutable (no soft-delete mixin)
"""
import asyncio
import datetime as _dt
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.fhir.provenance import (
    ACTIVITY_CREATE,
    ACTIVITY_DELETE,
    ACTIVITY_UPDATE,
    ProvenanceModel,
)
from app.services.fhir_converter import fhir_to_provenance_orm, validate_resource
from app.services.fhir_helpers import parse_fhir_resource
from app.services.provenance_service import (
    _agent_block,
    record_provenance,
    RECORD_CREATE,
    RECORD_DELETE,
    RECORD_UPDATE,
)


def _synthetic_agent_block(*, who_ref: str = None, display: str = None):
    """Build a synthetic agent block for tests that don't need DB resolution."""
    who = {}
    if who_ref:
        who["reference"] = who_ref
    if display:
        who["display"] = display
    return [
        {
            "type": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                        "code": "author",
                        "display": "Author",
                    }
                ]
            },
            "who": who,
        }
    ]


def _make_provenance(**overrides) -> ProvenanceModel:
    defaults = dict(
        id=str(uuid4()),
        target=[{"reference": "Condition/abc"}],
        recorded=_dt.datetime(2024, 1, 1, 12, 0, tzinfo=_dt.timezone.utc),
        activity={"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ProvenanceEventType", "code": "CREATE"}]},
        agent=_synthetic_agent_block(who_ref=f"Practitioner/{uuid4()}"),
    )
    defaults.update(overrides)
    return ProvenanceModel(**defaults)


# ---------------------------------------------------------------------------
# to_fhir_dict
# ---------------------------------------------------------------------------

def test_provenance_minimal_to_fhir_dict():
    p = _make_provenance()
    fhir = p.to_fhir_dict()
    assert fhir["resourceType"] == "Provenance"
    assert "target" in fhir
    assert "recorded" in fhir
    assert "agent" in fhir


def test_provenance_validates_against_fhir_resources():
    p = _make_provenance()
    fhir = p.to_fhir_dict()
    parsed = parse_fhir_resource("Provenance", fhir)
    assert parsed.__resource_type__ == "Provenance"


def test_provenance_recorded_iso_format():
    p = _make_provenance(recorded=_dt.datetime(2024, 6, 1, 12, 30, 45, tzinfo=_dt.timezone.utc))
    fhir = p.to_fhir_dict()
    assert fhir["recorded"].startswith("2024-06-01T12:30:45")
    assert fhir["recorded"].endswith("Z")  # UTC normalized


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

def test_provenance_no_soft_delete_mixin():
    """Provenance is immutable per spec — no SoftDeleteMixin columns."""
    from app.models.base import SoftDeleteMixin

    # ProvenanceModel should not have deleted_at.
    assert not hasattr(ProvenanceModel, "deleted_at")


# ---------------------------------------------------------------------------
# Agent block — F12 resolution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_block_user_resolved_to_practitioner():
    """F12: user_id resolves to Practitioner/<doctor.id> via DoctorModel.user_id.
    The agent.who reference is a real FHIR resource type — external clients
    can resolve it."""
    uid = uuid4()
    doctor_id = uuid4()
    db = AsyncMock()

    with patch(
        "app.services.provenance_service._resolve_practitioner_ref",
        new=AsyncMock(return_value=f"Practitioner/{doctor_id}"),
    ) as mock_resolve:
        agents, degraded = await _agent_block(
            db, user_id=uid, tenant_id=uuid4()
        )

    assert len(agents) == 1
    assert agents[0]["who"]["reference"] == f"Practitioner/{doctor_id}"
    assert agents[0]["type"]["coding"][0]["code"] == "author"
    assert degraded is False
    mock_resolve.assert_called_once()


@pytest.mark.asyncio
async def test_agent_block_user_without_doctor_falls_back_to_display():
    """F12: when the user has no Doctor row (admin/manager), the agent.who is
    a display-only Reference (spec-compliant — no `reference` key). The
    block is marked degraded."""
    uid = uuid4()
    db = AsyncMock()

    with patch(
        "app.services.provenance_service._resolve_practitioner_ref",
        new=AsyncMock(return_value=None),
    ):
        agents, degraded = await _agent_block(
            db, user_id=uid, tenant_id=uuid4()
        )

    assert len(agents) == 1
    # No `reference` key — only `display`.
    assert "reference" not in agents[0]["who"]
    assert "display" in agents[0]["who"]
    assert str(uid) in agents[0]["who"]["display"]
    assert degraded is True


@pytest.mark.asyncio
async def test_agent_block_integration_resolved_to_device():
    """F12: integration_id resolves to Device/<device.id> via
    DeviceModel.owner_integration_id."""
    iid = uuid4()
    device_id = uuid4()
    db = AsyncMock()

    with patch(
        "app.services.provenance_service._resolve_device_ref",
        new=AsyncMock(return_value=f"Device/{device_id}"),
    ):
        agents, degraded = await _agent_block(
            db, user_id=None, tenant_id=None, integration_id=iid
        )

    assert agents[0]["who"]["reference"] == f"Device/{device_id}"
    assert degraded is False


@pytest.mark.asyncio
async def test_agent_block_integration_without_device_falls_back_to_display():
    iid = uuid4()
    db = AsyncMock()

    with patch(
        "app.services.provenance_service._resolve_device_ref",
        new=AsyncMock(return_value=None),
    ):
        agents, degraded = await _agent_block(
            db, user_id=None, tenant_id=None, integration_id=iid
        )

    assert "reference" not in agents[0]["who"]
    assert "display" in agents[0]["who"]
    assert str(iid) in agents[0]["who"]["display"]
    assert degraded is True


@pytest.mark.asyncio
async def test_agent_block_anonymous_when_no_user_no_integration():
    """When neither user_id nor integration_id is provided, the agent.who is
    display-only (anonymous)."""
    db = AsyncMock()
    agents, degraded = await _agent_block(
        db, user_id=None, tenant_id=None, integration_id=None
    )
    assert len(agents) == 1
    assert "reference" not in agents[0]["who"]
    assert "display" in agents[0]["who"]
    assert degraded is True


@pytest.mark.asyncio
async def test_agent_block_user_and_integration_both_emitted():
    """When both user_id and integration_id are provided, BOTH agents are
    recorded (integration first, then user)."""
    iid = uuid4()
    uid = uuid4()
    db = AsyncMock()

    with patch(
        "app.services.provenance_service._resolve_device_ref",
        new=AsyncMock(return_value=f"Device/{uuid4()}"),
    ), patch(
        "app.services.provenance_service._resolve_practitioner_ref",
        new=AsyncMock(return_value=f"Practitioner/{uuid4()}"),
    ):
        agents, degraded = await _agent_block(
            db, user_id=uid, tenant_id=uuid4(), integration_id=iid
        )

    assert len(agents) == 2
    # Integration agent first, user agent second.
    assert "Device/" in agents[0]["who"]["reference"]
    assert "Practitioner/" in agents[1]["who"]["reference"]
    assert degraded is False


@pytest.mark.asyncio
async def test_agent_block_never_emits_user_or_integration_resource_type():
    """F12 headline check: the agent.who reference must NEVER use the
    non-FHIR resource types 'User/<id>' or 'Integration/<id>'. Either it
    resolves to Practitioner/Device, or it's display-only."""
    uid = uuid4()
    iid = uuid4()
    db = AsyncMock()

    # Resolved path.
    with patch(
        "app.services.provenance_service._resolve_practitioner_ref",
        new=AsyncMock(return_value=f"Practitioner/{uuid4()}"),
    ), patch(
        "app.services.provenance_service._resolve_device_ref",
        new=AsyncMock(return_value=f"Device/{uuid4()}"),
    ):
        agents, _ = await _agent_block(
            db, user_id=uid, tenant_id=uuid4(), integration_id=iid
        )
        for agent in agents:
            ref = agent["who"].get("reference", "")
            assert not ref.startswith("User/"), f"emitted User/ reference: {ref}"
            assert not ref.startswith("Integration/"), f"emitted Integration/ reference: {ref}"

    # Degraded path.
    with patch(
        "app.services.provenance_service._resolve_practitioner_ref",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.provenance_service._resolve_device_ref",
        new=AsyncMock(return_value=None),
    ):
        agents, _ = await _agent_block(
            db, user_id=uid, tenant_id=uuid4(), integration_id=iid
        )
        for agent in agents:
            # Display-only — no reference at all.
            assert "reference" not in agent["who"]


# ---------------------------------------------------------------------------
# Reverse converter
# ---------------------------------------------------------------------------

def _canonical_provenance(**overrides) -> dict:
    base = {
        "resourceType": "Provenance",
        "id": str(uuid4()),
        "target": [{"reference": "Patient/p1"}],
        "recorded": "2024-01-01T00:00:00Z",
        "activity": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ProvenanceEventType",
                    "code": "CREATE",
                }
            ]
        },
        "agent": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                            "code": "author",
                        }
                    ]
                },
                "who": {"reference": "User/u1"},
            }
        ],
    }
    base.update(overrides)
    return base


def test_fhir_to_provenance_orm_basic():
    fhir = _canonical_provenance()
    orm = fhir_to_provenance_orm(fhir)
    assert orm["target"] == fhir["target"]
    assert orm["activity"] == fhir["activity"]
    assert orm["agent"] == fhir["agent"]
    assert orm["recorded"].year == 2024


def test_canonical_provenance_validates():
    fhir = _canonical_provenance()
    ok, errs = validate_resource(fhir)
    assert ok, f"Expected valid Provenance: {errs}"


# ---------------------------------------------------------------------------
# record_provenance service
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_provenance_creates_row():
    """record_provenance should add a ProvenanceModel to the session and flush."""
    fake_db = AsyncMock()
    fake_db.add = MagicMock()
    fake_db.flush = AsyncMock()

    tid = uuid4()
    uid = uuid4()
    # Patch the resolvers so _agent_block doesn't hit the AsyncMock db.execute
    # (avoids the spurious "coroutine never awaited" warning from AsyncMock).
    with patch(
        "app.services.provenance_service._resolve_practitioner_ref",
        new=AsyncMock(return_value=f"Practitioner/{uuid4()}"),
    ):
        result = await record_provenance(
            fake_db,
            target_resource_type="Condition",
            target_id=tid,
            activity=RECORD_CREATE,
            tenant_id=uuid4(),
            user_id=uid,
        )

    assert result is not None
    assert result.target == [{"reference": f"Condition/{tid}"}]
    fake_db.add.assert_called_once()
    fake_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_record_provenance_best_effort_on_validation_failure():
    """If FHIR validation fails, record_provenance should still persist a
    degraded Provenance (entity dropped) and return the model — not abort
    the parent write."""
    fake_db = AsyncMock()
    fake_db.add = MagicMock()
    fake_db.flush = AsyncMock()

    with patch(
        "app.services.provenance_service._resolve_practitioner_ref",
        new=AsyncMock(return_value=f"Practitioner/{uuid4()}"),
    ):
        result = await record_provenance(
            fake_db,
            target_resource_type="Condition",
            target_id=uuid4(),
            activity=RECORD_CREATE,
            tenant_id=uuid4(),
            user_id=uuid4(),
            # Pass an entity that will fail FHIR validation.
            entity_inputs=[{"role": "garbage", "what": "garbage"}],
        )

    assert result is not None
    # Validation failure should drop entity to None.
    assert result.entity is None


@pytest.mark.asyncio
async def test_record_provenance_returns_none_on_db_failure():
    """If the DB flush fails, record_provenance should return None (best-effort)
    without raising — the parent write must not be affected."""
    fake_db = AsyncMock()
    fake_db.add = MagicMock()
    fake_db.flush = AsyncMock(side_effect=Exception("DB down"))

    with patch(
        "app.services.provenance_service._resolve_practitioner_ref",
        new=AsyncMock(return_value=f"Practitioner/{uuid4()}"),
    ):
        result = await record_provenance(
            fake_db,
            target_resource_type="Condition",
            target_id=uuid4(),
            activity=RECORD_CREATE,
            tenant_id=uuid4(),
            user_id=uuid4(),
        )

    assert result is None  # best-effort: don't raise


# ---------------------------------------------------------------------------
# Activity constants
# ---------------------------------------------------------------------------

def test_activity_constants():
    assert RECORD_CREATE == "CREATE"
    assert RECORD_UPDATE == "UPDATE"
    assert RECORD_DELETE == "DELETE"
