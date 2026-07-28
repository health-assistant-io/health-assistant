"""Tests for audit item A4 (telemetry/FHIR split in sync_active_integrations)
and the long-format hypertable rewrite.

A4: The background Celery task ``sync_active_integrations`` (runs every 60s)
    wrote ALL pulled Observations into ``fhir_observations`` regardless of
    ``BiomarkerDefinition.is_telemetry``. Three other code paths (manual
    sync, webhook, bridge) did the split correctly. Result: telemetry-class
    biomarkers synced via the background loop landed in the FHIR table where
    the AI telemetry tools couldn't see them.

The fix introduces ``integration_sync_service.apply_telemetry_split`` and
wires it into both the background task and the manual sync endpoint.

The long-format rewrite (migration ``t1e2l3o4n5g6``) collapses the
slug→column branching: every telemetry observation becomes one
``TelemetryDataModel(slug=..., value=..., unit=..., patient_id=...)`` row.
These tests pin that contract.
"""
import datetime
import inspect
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest


TENANT_A = UUID("11111111-1111-1111-1111-111111111111")


class _FakeObservation:
    """Stand-in for the ORM Observation class so test routing checks can
    distinguish it from TelemetryDataModel via ``type(x).__name__``."""

    def __init__(self, biomarker_id, value=70.0, code_loinc="8867-4",
                 patient_id=None):
        self.id = uuid4()
        self.biomarker_id = biomarker_id
        self.effective_datetime = datetime.datetime(
            2026, 1, 1, tzinfo=datetime.timezone.utc
        )
        self.raw_value = value
        self.normalized_value = value
        self.value_quantity = {"value": value, "unit": "{beats}/min"}
        self.performer = None
        self.patient_id = patient_id
        self.subject = {
            "reference": f"Patient/{patient_id}" if patient_id else None
        }


# Patch the helper's type-check by giving the fake class the real name
_FakeObservation.__name__ = "Observation"
_FakeObservation.__qualname__ = "Observation"


def _make_obs(biomarker_id, value=70.0, code_loinc="8867-4", patient_id=None):
    """Build a fake Observation ORM object for the split helper."""
    return _FakeObservation(biomarker_id, value, code_loinc, patient_id)


def _make_biomarker(b_id, slug, is_telemetry):
    b = MagicMock()
    b.id = b_id
    b.slug = slug
    b.is_telemetry = is_telemetry
    return b


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Captures db.add_all(...) calls so the test can verify routing."""

    def __init__(self, biomarker_rows):
        self._biomarker_rows = biomarker_rows
        self.added_telemetry = []
        self.added_fhir = []

    async def execute(self, query):
        # The helper's only DB read is the BiomarkerDefinition select.
        return _FakeResult(self._biomarker_rows)

    def add_all(self, records):
        # Distinguish by class name since both are added via add_all
        for r in records:
            cls = type(r).__name__
            if cls == "TelemetryDataModel":
                self.added_telemetry.append(r)
            elif cls == "Observation":
                self.added_fhir.append(r)
            else:
                raise AssertionError(f"Unexpected record type routed: {cls}")


# ---------------------------------------------------------------------------
# A4: split-helper unit tests (long-format contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_routes_telemetry_flagged_to_hypertable():
    """A4: a telemetry-flagged biomarker must land in telemetry_data only."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = _make_biomarker(b_id, "heart-rate", is_telemetry=True)
    session = _FakeSession([b_def])

    obs = _make_obs(b_id, value=72.0)

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="fitbit-1",
        provider_name="fitbit",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 1
    assert len(fhir_records) == 0
    assert session.added_telemetry == telemetry_records
    assert session.added_fhir == []
    # Long-format: slug + value on the row (no dedicated columns).
    row = telemetry_records[0]
    assert row.slug == "heart-rate"
    assert row.value == 72.0
    assert row.unit == "{beats}/min"
    assert not hasattr(row, "heart_rate"), (
        "Long-format TelemetryDataModel must not carry dedicated metric columns"
    )


@pytest.mark.asyncio
async def test_split_routes_non_telemetry_to_fhir():
    """A4: a non-telemetry biomarker must land in fhir_observations only."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = _make_biomarker(b_id, "cholesterol", is_telemetry=False)
    session = _FakeSession([b_def])

    obs = _make_obs(b_id, value=5.2, code_loinc="2093-3")

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="labcorp",
        provider_name="labcorp",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 0
    assert len(fhir_records) == 1
    assert session.added_fhir == fhir_records
    # Performer must be stamped with the integration reference
    assert fhir_records[0].performer[0]["reference"].startswith("Integration/")
    assert fhir_records[0].performer[0]["display"] == "labcorp"


@pytest.mark.asyncio
async def test_split_mixed_batch_routes_correctly():
    """A4: a mixed batch (1 telemetry + 1 FHIR) must split cleanly."""
    from app.services.integration_sync_service import apply_telemetry_split

    telemetry_b = _make_biomarker(uuid4(), "steps", is_telemetry=True)
    fhir_b = _make_biomarker(uuid4(), "ldl", is_telemetry=False)
    session = _FakeSession([telemetry_b, fhir_b])

    obs_t = _make_obs(telemetry_b.id, value=5400, code_loinc="41950-7")
    obs_f = _make_obs(fhir_b.id, value=120.0, code_loinc="2089-1")

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs_t, obs_f],
        tenant_id=TENANT_A,
        instance_name="dummy-1",
        provider_name="dev_dummy",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 1
    assert len(fhir_records) == 1
    # Long-format: steps slug lives on the row, no dedicated column.
    assert telemetry_records[0].slug == "steps"
    assert telemetry_records[0].value == 5400


@pytest.mark.asyncio
async def test_split_unknown_biomarker_defaults_to_fhir():
    """A4: an observation with no biomarker_id defaults to FHIR.

    This mirrors the behavior of every other code path — only flagged
    biomarkers route to telemetry.
    """
    from app.services.integration_sync_service import apply_telemetry_split

    session = _FakeSession([])  # no biomarker definitions loaded

    obs = _make_obs(biomarker_id=None, value=1.0)

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="x",
        provider_name="x",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 0
    assert len(fhir_records) == 1


@pytest.mark.asyncio
async def test_split_empty_input_returns_empty():
    """A4: no observations → no rows added, no DB hit."""
    from app.services.integration_sync_service import apply_telemetry_split

    session = _FakeSession([])
    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [],
        tenant_id=TENANT_A,
        instance_name="x",
        provider_name="x",
        integration_id=uuid4(),
    )
    assert telemetry_records == []
    assert fhir_records == []


@pytest.mark.asyncio
async def test_split_long_tail_slug_uses_uniform_row_shape():
    """Long-format: a telemetry biomarker WITHOUT a historical dedicated
    column (e.g. ``stress-level``) lands in a uniform long-format row —
    same shape as heart-rate. This is the core modularity win: no branching
    on slug, no JSONB catch-all."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = _make_biomarker(b_id, "stress-level", is_telemetry=True)
    session = _FakeSession([b_def])

    obs = _make_obs(b_id, value=6.5, code_loinc="custom-stress")

    telemetry_records, _ = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="whoop-1",
        provider_name="whoop",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 1
    row = telemetry_records[0]
    # Uniform long-format shape — no JSONB ``data`` payload.
    assert row.slug == "stress-level"
    assert row.value == 6.5
    assert not hasattr(row, "data"), (
        "Long-format TelemetryDataModel must not carry a JSONB ``data`` column"
    )


@pytest.mark.asyncio
async def test_split_carries_patient_id_onto_telemetry_row():
    """Long-format improvement: the observation's ``patient_id`` is
    persisted on the telemetry row, killing the device→UserIntegration→
    Patient attribution chain in the migration path."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = _make_biomarker(b_id, "heart-rate", is_telemetry=True)
    session = _FakeSession([b_def])

    patient_id = uuid4()
    obs = _make_obs(b_id, value=72.0, patient_id=patient_id)

    telemetry_records, _ = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="fitbit-1",
        provider_name="fitbit",
        integration_id=uuid4(),
    )
    assert telemetry_records[0].patient_id == patient_id


@pytest.mark.asyncio
async def test_split_skips_telemetry_obs_with_no_numeric_value():
    """Long-format hypertable requires NOT NULL ``value``; a telemetry obs
    we can't extract a numeric value from is skipped (the FHIR path is the
    better home for non-numeric observations anyway)."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = _make_biomarker(b_id, "mood", is_telemetry=True)
    session = _FakeSession([b_def])

    obs = _make_obs(b_id, value=None)
    obs.normalized_value = None
    obs.raw_value = None
    obs.value_quantity = None

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="x",
        provider_name="x",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 0
    assert len(fhir_records) == 0  # telemetry-flagged → not routed to FHIR either


# ---------------------------------------------------------------------------
# A4: the background task now actually invokes the helper
# ---------------------------------------------------------------------------


def test_sync_active_integrations_uses_run_sync():
    """A4 regression: the background task must delegate to the shared run_sync
    pipeline (which calls apply_telemetry_split internally).

    Catches the bug regressing at source level — if a future edit removes the
    delegation, this test fails.
    """
    from app.workers import tasks

    src = inspect.getsource(tasks.sync_active_integrations)
    assert "run_sync" in src, (
        "sync_active_integrations does not invoke run_sync — "
        "the shared pipeline (which calls apply_telemetry_split) is missing"
    )


def test_run_sync_calls_apply_telemetry_split():
    """The shared pipeline must call apply_telemetry_split."""
    from app.services import integration_sync_service

    src = inspect.getsource(integration_sync_service.run_sync)
    assert "apply_telemetry_split" in src, (
        "run_sync does not invoke apply_telemetry_split — telemetry would "
        "again land in fhir_observations"
    )


def test_manual_sync_endpoint_uses_run_sync():
    """A4: the manual sync endpoint should delegate to the shared pipeline (DRY)."""
    from app.api.v1.endpoints import integrations

    src = inspect.getsource(integrations)
    assert "run_sync" in src, (
        "manual sync endpoint should use run_sync for DRY"
    )


# ---------------------------------------------------------------------------
# Webhook-path parity: the webhook handler must route through the shared
# ``apply_telemetry_split`` helper instead of inlining a copy of the routing
# logic. Three source-level guards catch regressions.
# ---------------------------------------------------------------------------


def _webhook_handler_source() -> str:
    from app.api.v1.endpoints import integrations

    return inspect.getsource(integrations.integration_webhook)


def test_webhook_endpoint_uses_apply_telemetry_split():
    """The webhook handler must call ``apply_telemetry_split``."""
    src = _webhook_handler_source()
    assert "apply_telemetry_split" in src, (
        "webhook handler must route through apply_telemetry_split"
    )


def test_webhook_endpoint_no_longer_inlines_telemetry_loop():
    """The webhook handler must NOT carry its own copy of the routing loop."""
    src = _webhook_handler_source()
    assert '"calories" in slug' not in src
    assert "TelemetryDataModel(" not in src, (
        "webhook handler must not construct TelemetryDataModel directly — "
        "apply_telemetry_split owns that"
    )


def test_webhook_endpoint_passes_real_per_channel_counts_to_notifications():
    """post_sync_notifications must receive the real per-channel counts."""
    src = _webhook_handler_source()
    assert "fhir_persisted=len(fhir_records)" in src
    assert "telemetry_persisted=len(telemetry_records)" in src


# ---------------------------------------------------------------------------
# Source-level guard: the split helper itself has no slug→column branching
# (long-format contract). Catches a future revert to the wide+JSONB shape.
# ---------------------------------------------------------------------------


def test_apply_telemetry_split_has_no_slug_to_column_branching():
    """Long-format contract: ``apply_telemetry_split`` must not branch on
    slug→column. A revert to the old ``if 'heart-rate' in slug: hr = value``
    pattern would reintroduce the modularity tax."""
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc.apply_telemetry_split)
    assert '"heart-rate" in slug' not in src, (
        "apply_telemetry_split must not branch on heart-rate slug — "
        "long-format stores every metric uniformly"
    )
    assert '"calories" in slug' not in src
    assert '"steps" in slug' not in src
    assert "data_payload" not in src


# ---------------------------------------------------------------------------
# Workstream B.2 / E.3 / F / G: run_sync opt-in hooks (source-level guards).
# These are unaffected by the telemetry long-format rewrite but live in this
# file — kept verbatim from the prior test version.
# ---------------------------------------------------------------------------


def test_run_sync_wires_clinical_events_opt_in_hook():
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc.run_sync)
    assert "supports_clinical_events" in src
    assert "pull_clinical_events" in src
    assert "create_event" in src
    assert "source_integration_id=integration.id" in src


def test_opt_in_helper_handles_missing_method():
    from app.services.integration_sync_service import _opt_in

    class _Bare:
        pass

    assert _opt_in(_Bare(), "supports_clinical_events") is False

    class _OptIn:
        def supports_clinical_events(self):
            return True

    assert _opt_in(_OptIn(), "supports_clinical_events") is True


def test_opt_in_helper_swallows_exceptions_from_capability_probe():
    from app.services.integration_sync_service import _opt_in

    class _Broken:
        def supports_clinical_events(self):
            raise RuntimeError("boom")

    assert _opt_in(_Broken(), "supports_clinical_events") is False


def test_run_sync_wires_examinations_opt_in_hook():
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc.run_sync)
    assert "supports_examinations" in src
    assert "pull_examinations" in src
    assert "create_examination" in src


def test_run_sync_wires_catalog_proposals_opt_in_hook():
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc.run_sync)
    assert "supports_catalog_proposals" in src
    assert "pull_catalog_proposals" in src
    assert "apply_proposal" in src


def test_run_sync_enforces_proposals_per_sync_cap():
    from app.services import integration_sync_service as svc

    assert svc.INTEGRATION_MAX_PROPOSALS_PER_SYNC > 0
    src = inspect.getsource(svc.run_sync)
    assert "INTEGRATION_MAX_PROPOSALS_PER_SYNC" in src


def test_sync_result_carries_proposal_counts():
    from app.services.integration_sync_service import SyncResult

    result = SyncResult()
    assert hasattr(result, "proposals_pulled")
    assert hasattr(result, "proposals_applied")


def test_run_sync_wires_hitl_proposals_opt_in_hook():
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc.run_sync)
    assert "supports_hitl_proposals" in src
    assert "pull_hitl_proposals" in src
    assert "create_proposal" in src


def test_run_sync_enforces_hitl_proposals_per_sync_cap():
    from app.services import integration_sync_service as svc

    assert svc.INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC > 0
    src = inspect.getsource(svc.run_sync)
    assert "INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC" in src


def test_run_sync_emits_hitl_proposal_notification_on_insert():
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc.run_sync)
    assert "_emit_hitl_proposal_notification" in src


def test_sync_result_carries_hitl_proposal_counts():
    from app.services.integration_sync_service import SyncResult

    result = SyncResult()
    assert hasattr(result, "hitl_proposals_pulled")
    assert hasattr(result, "hitl_proposals_inserted")
