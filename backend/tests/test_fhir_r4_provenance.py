"""Tests for Provenance model + service (audit C10).

Covers:
- ProvenanceModel.to_fhir_dict() emits valid FHIR R4 Provenance
- Round-trip via fhir_to_provenance_orm
- record_provenance() service records on a target
- agent block construction (user vs integration vs anonymous)
- Provenance is immutable (no soft-delete mixin)
"""
import datetime as _dt
from unittest.mock import AsyncMock, MagicMock
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


def _make_provenance(**overrides) -> ProvenanceModel:
    defaults = dict(
        id=str(uuid4()),
        target=[{"reference": "Condition/abc"}],
        recorded=_dt.datetime(2024, 1, 1, 12, 0, tzinfo=_dt.timezone.utc),
        activity={"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ProvenanceEventType", "code": "CREATE"}]},
        agent=_agent_block(uuid4()),
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
# Agent block
# ---------------------------------------------------------------------------

def test_agent_block_user():
    uid = uuid4()
    block = _agent_block(user_id=uid)
    assert len(block) == 1
    assert block[0]["who"]["reference"] == f"User/{uid}"
    assert block[0]["type"]["coding"][0]["code"] == "author"


def test_agent_block_integration():
    iid = uuid4()
    block = _agent_block(user_id=None, integration_id=iid)
    assert block[0]["who"]["reference"] == f"Integration/{iid}"


def test_agent_block_anonymous():
    block = _agent_block(user_id=None, integration_id=None)
    assert "display" in block[0]["who"]  # no reference, just a display string


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
