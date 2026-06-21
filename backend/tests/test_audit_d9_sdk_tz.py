"""Reproduces the user-reported dev_dummy sync failure (2026-06-21).

The manual sync of dev_dummy was silently dropping all 3 pulled observations
because their `effectiveDateTime` failed the FHIR R4 regex:

    '2026-06-20T22:39:56.471381'   <- missing timezone suffix
    -> "DateTime value string does not match spec regex"
    -> validate_and_filter_observations dropped them
    -> sync endpoint returned "Sync completed successfully" with metrics_synced=0

Root cause: ``ObservationBuilder.build()`` stripped tzinfo "for asyncpg
compat" — asyncpg handles tz-aware datetimes natively for TIMESTAMP WITH
TIME ZONE columns, so the strip was both unnecessary and destructive.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest


TENANT = uuid4()
PATIENT = uuid4()


# ---------------------------------------------------------------------------
# Root cause: ObservationBuilder must produce tz-aware datetimes
# ---------------------------------------------------------------------------


def test_observation_builder_keeps_timezone():
    """Audit D9 regression: builder must NOT strip tzinfo."""
    from integrations.sdk.observation_builder import ObservationBuilder

    tz_aware = datetime(2026, 6, 20, 22, 39, 56, 471381, tzinfo=timezone.utc)
    obs = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72.0, "bpm", "{beats}/min")
        .set_effective_date(tz_aware)
        .build()
    )
    assert obs.effective_datetime is not None
    assert obs.effective_datetime.tzinfo is not None, (
        "ObservationBuilder stripped tzinfo — the resulting isoformat() "
        "fails the FHIR R4 regex and the observation gets silently dropped"
    )


# ---------------------------------------------------------------------------
# Effect: a built Observation must round-trip through assert_valid_fhir
# ---------------------------------------------------------------------------


def test_built_observation_passes_fhir_validation():
    """The whole point: an Observation from the SDK builder must be valid FHIR.

    This is the regression test for the user's reported bug. Before the fix,
    `effective_datetime` lacked tzinfo, `isoformat()` produced no offset,
    and fhir.resources rejected it.
    """
    from integrations.sdk.observation_builder import ObservationBuilder
    from app.models.fhir import Observation
    from app.services.fhir_helpers import assert_valid_fhir

    tz_aware = datetime(2026, 6, 20, 22, 39, 56, 471381, tzinfo=timezone.utc)
    obs_create = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72.0, "bpm", "{beats}/min")
        .set_effective_date(tz_aware)
        .set_reference_range(low=60, high=100)
        .build()
    )
    # Convert to ORM as the sync path does
    orm = Observation(**obs_create.model_dump(exclude_unset=True))

    # Must not raise FhirSerializationError
    fhir_dict = assert_valid_fhir(orm)
    assert fhir_dict["resourceType"] == "Observation"
    # And the effectiveDateTime must be FHIR-conformant (carry an offset)
    eff = fhir_dict.get("effectiveDateTime")
    assert eff is not None
    assert eff.endswith("Z") or "+" in eff or eff.count("-") > 2, (
        f"effectiveDateTime {eff!r} is missing a timezone offset — will "
        "fail the FHIR R4 regex"
    )


# ---------------------------------------------------------------------------
# Defensive layer: ORM models must serialize naive datetimes safely
# ---------------------------------------------------------------------------


def test_observation_to_fhir_dict_handles_naive_datetime():
    """Even if a naive datetime slips through, to_fhir_dict must emit valid FHIR.

    Guards the existing-data case: rows written before the fix may have
    naive effective_datetime values that would still fail validation on
    export.
    """
    from app.models.fhir import Observation
    from app.services.fhir_helpers import assert_valid_fhir

    naive = datetime(2026, 6, 20, 22, 39, 56, 471381)  # no tz
    orm = Observation(
        tenant_id=TENANT,
        status="final",
        code={"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
        subject={"reference": f"Patient/{PATIENT}"},
        effective_datetime=naive,
        value_quantity={"value": 72.0, "unit": "bpm"},
    )
    fhir_dict = assert_valid_fhir(orm)
    eff = fhir_dict["effectiveDateTime"]
    # Must carry an offset now (the defensive helper assumed UTC)
    assert eff.endswith("Z") or "+" in eff, (
        f"Naive datetime was not defended: {eff!r}"
    )


# ---------------------------------------------------------------------------
# UX: dropped-invalid count surfaces to the caller
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_observations_returns_dropped_count(monkeypatch):
    """map_observations_to_biomarkers must surface how many it dropped.

    The sync endpoint needs this to report partial success instead of
    a silent zero-result "success".

    Note: the naive-datetime case that triggered the original bug is now
    fixed defensively by fhir_isoformat() — so to exercise the dropped-
    count pathway we need a *truly* invalid resource (missing required
    ``code``).
    """
    from app.services import fhir_service as svc

    # Bypass the DB — the function uses AsyncSessionLocal() internally
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

                def first(self):
                    return None

            return _R()

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: _FakeSession())

    # Build ONE valid + ONE genuinely invalid observation. The invalid one
    # has a non-numeric valueQuantity.value — fhir.resources rejects this
    # regardless of any defensive date helper.
    from app.models.fhir import Observation

    valid = Observation(
        tenant_id=TENANT,
        status="final",
        code={"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
        subject={"reference": f"Patient/{PATIENT}"},
        effective_datetime=datetime(2026, 6, 20, 22, 39, 56, tzinfo=timezone.utc),
        value_quantity={"value": 72.0},
    )
    invalid = Observation(
        tenant_id=TENANT,
        status="final",
        code={"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
        subject={"reference": f"Patient/{PATIENT}"},
        effective_datetime=datetime(2026, 6, 20, 22, 39, 56, tzinfo=timezone.utc),
        value_quantity={"value": "not-a-number"},  # genuinely invalid
    )

    result = await svc.map_observations_to_biomarkers(_FakeSession(), [valid, invalid])
    assert isinstance(result, dict), (
        f"map_observations_to_biomarkers returned {type(result)} — needs to "
        "expose the dropped-invalid count as a dict"
    )
    assert "dropped_invalid" in result
    assert result["dropped_invalid"] >= 1


@pytest.mark.asyncio
async def test_map_observations_naive_datetime_no_longer_dropped(monkeypatch):
    """Guards against the D9 regression: a naive effective_datetime must NOT
    be dropped. The defensive ``fhir_isoformat`` helper assumes UTC and
    emits a conformant FHIR string."""
    from app.services import fhir_service as svc

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

                def first(self):
                    return None

            return _R()

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: _FakeSession())

    from app.models.fhir import Observation

    naive_obs = Observation(
        tenant_id=TENANT,
        status="final",
        code={"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
        subject={"reference": f"Patient/{PATIENT}"},
        effective_datetime=datetime(2026, 6, 20, 22, 39, 56),  # naive
        value_quantity={"value": 72.0},
    )

    result = await svc.map_observations_to_biomarkers(
        _FakeSession(), [naive_obs]
    )
    assert result["dropped_invalid"] == 0, (
        "Naive datetime was dropped — fhir_isoformat defensive layer regressed"
    )
    assert result["mapped"] == 1


def test_manual_sync_response_includes_dropped_field():
    """Source-level: the manual sync endpoint must surface dropped count."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "api"
        / "v1"
        / "endpoints"
        / "integrations.py"
    ).read_text()
    # The response dict must include a field that exposes dropped/invalid count
    assert "dropped_invalid" in src or "invalid_dropped" in src, (
        "manual sync endpoint must surface dropped_invalid count to the UI "
        "(audit A4 follow-up: silent drops are the bug)"
    )
