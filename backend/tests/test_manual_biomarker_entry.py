"""Tests for standalone manual biomarker entry.

The manual-entry flow lands at ``POST /api/v1/observations`` without an
``examination_id`` — see ``LogBiomarkerReadingModal`` on the frontend. These
tests pin the new fields the form sends (``method``, ``comment``/``note``,
``effective_datetime``) without requiring a real database:

1. The endpoint forwards a non-exam payload to ``create_observation`` and
   still audits the action.
2. ``_extract_comment_text`` normalizes the FHIR ``note`` array shape and
   plain-string inputs to a single string for the ORM column.
3. ``_parse_datetime`` accepts the local datetime-local string the frontend
   form emits (``YYYY-MM-DDTHH:MM``).
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.services.fhir_service import _extract_comment_text, _parse_datetime


# ---------------------------------------------------------------------------
# Helper unit tests (no DB)
# ---------------------------------------------------------------------------


def test_extract_comment_text_from_fhir_note_array():
    """Frontend ``AddBiomarkerForm`` sends ``note: [{text: "..."}]``."""
    assert _extract_comment_text([{"text": "Fasting"}]) == "Fasting"


def test_extract_comment_text_from_plain_string():
    assert _extract_comment_text("After lunch") == "After lunch"
    assert _extract_comment_text("  trimmed  ") == "trimmed"


def test_extract_comment_text_empty_returns_none():
    assert _extract_comment_text(None) is None
    assert _extract_comment_text("") is None
    assert _extract_comment_text("   ") is None
    assert _extract_comment_text([]) is None


def test_extract_comment_text_defensive_on_unknown_types():
    """Never raise — bad input becomes ``None`` so persistence stays alive."""
    assert _extract_comment_text(42) is None
    assert _extract_comment_text({"text": "wrong shape"}) is None
    assert _extract_comment_text([{"no_text": "x"}]) is None


def test_parse_datetime_accepts_local_datetime_local_string():
    """The frontend form emits ``<input type="datetime-local">`` strings —
    ``YYYY-MM-DDTHH:MM`` with no timezone. ``_parse_datetime`` should
    accept it and stamp UTC."""
    parsed = _parse_datetime("2026-08-05T14:30")
    assert parsed is not None
    assert isinstance(parsed, datetime)
    assert parsed.tzinfo is not None
    assert parsed == datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc)


def test_parse_datetime_none_passthrough():
    assert _parse_datetime(None) is None
    assert _parse_datetime("") is None


# ---------------------------------------------------------------------------
# Endpoint integration (mocked service — no DB)
# ---------------------------------------------------------------------------


def _make_token(role="ADMIN"):
    token = MagicMock()
    token.role = role
    token.user_id = uuid4()
    token.tenant_id = uuid4()
    return token


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.observations.log_audit_action", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.observations.create_observation", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.observations.check_patient_access", new_callable=AsyncMock)
async def test_create_observation_standalone_manual_payload_passes_through(
    mock_access,
    mock_create,
    mock_audit,
    async_client: AsyncClient,
):
    """Manual entry sends ``effective_datetime`` + ``method`` + ``note[]``
    and no ``examination_id``. The endpoint must forward these to
    ``create_observation`` verbatim."""
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _make_token(role="ADMIN")
    created = MagicMock()
    created.id = uuid4()
    mock_create.return_value = created

    patient_id = uuid4()
    payload = {
        "patient_id": str(patient_id),
        "biomarker_id": str(uuid4()),
        "status": "final",
        "code": {"text": "Glucose"},
        "value_quantity": {"value": 95, "unit": "mg/dL"},
        "effective_datetime": "2026-08-05T14:30",
        "method": "Fingerstick",
        "note": [{"text": "After lunch"}],
    }

    response = await async_client.post("/api/v1/observations", json=payload)
    assert response.status_code == 200, response.text

    # The endpoint must forward the payload (with subject derived from
    # patient_id) to the service layer.
    forwarded = mock_create.call_args.args[0]
    assert forwarded["method"] == "Fingerstick"
    assert forwarded["effective_datetime"] == "2026-08-05T14:30"
    assert forwarded["note"] == [{"text": "After lunch"}]
    # No examination_id in a standalone payload.
    assert "examination_id" not in forwarded or forwarded.get("examination_id") is None

    app.dependency_overrides = {}
