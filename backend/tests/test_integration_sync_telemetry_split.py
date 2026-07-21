"""Tests for audit item A4 (telemetry/FHIR split in sync_active_integrations).

A4: The background Celery task ``sync_active_integrations`` (runs every 60s)
    wrote ALL pulled Observations into ``fhir_observations`` regardless of
    ``BiomarkerDefinition.is_telemetry``. Three other code paths (manual
    sync, webhook, bridge) did the split correctly. Result: telemetry-class
    biomarkers synced via the background loop landed in the FHIR table where
    the AI telemetry tools couldn't see them.

The fix introduces ``integration_sync_service.apply_telemetry_split`` and
wires it into both the background task and the manual sync endpoint.
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

    def __init__(self, biomarker_id, value=70.0, code_loinc="8867-4"):
        self.id = uuid4()
        self.biomarker_id = biomarker_id
        self.effective_datetime = datetime.datetime(
            2026, 1, 1, tzinfo=datetime.timezone.utc
        )
        self.raw_value = value
        self.normalized_value = value
        self.value_quantity = {"value": value, "unit": "{beats}/min"}
        self.performer = None
        self.subject = {
            "reference": "Patient/00000000-0000-0000-0000-000000000000"
        }


# Patch the helper's type-check by giving the fake class the real name
_FakeObservation.__name__ = "Observation"
_FakeObservation.__qualname__ = "Observation"


def _make_obs(biomarker_id, value=70.0, code_loinc="8867-4"):
    """Build a fake Observation ORM object for the split helper."""
    return _FakeObservation(biomarker_id, value, code_loinc)


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
# A4: split-helper unit tests
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
    # Heart rate slug → dedicated heart_rate column
    assert telemetry_records[0].heart_rate == 72.0
    assert telemetry_records[0].steps is None
    assert telemetry_records[0].calories is None


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
    # Steps slug → dedicated steps column
    assert telemetry_records[0].steps == 5400
    assert telemetry_records[0].heart_rate is None
    assert telemetry_records[0].calories is None


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
async def test_split_telemetry_long_tail_goes_into_data_jsonb():
    """A4: telemetry biomarkers without a dedicated column land in ``data``."""
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
    # No dedicated column matched → all in JSONB ``data``
    assert row.heart_rate is None
    assert row.steps is None
    assert row.calories is None
    assert row.data is not None
    assert "stress-level" in row.data
    assert row.data["stress-level"] == 6.5


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
# Webhook-path parity (this cleanup pass): the webhook handler must route
# through the shared ``apply_telemetry_split`` helper instead of inlining a
# copy of the routing logic. Three source-level guards catch regressions.
# ---------------------------------------------------------------------------


def _webhook_handler_source() -> str:
    from app.api.v1.endpoints import integrations

    # The webhook route function is ``integration_webhook``; pull just its
    # body so the assertions below don't trip on unrelated code elsewhere in
    # the module.
    return inspect.getsource(integrations.integration_webhook)


def test_webhook_endpoint_uses_apply_telemetry_split():
    """The webhook handler must call ``apply_telemetry_split`` (the same
    helper the manual + background paths use).

    Before this cleanup pass the webhook handler inlined its own copy of the
    telemetry/FHIR routing loop, which had already diverged (subtle slug-match
    differences, missing performer.reference, and post_sync_notifications was
    always called with telemetry_persisted=0).
    """
    src = _webhook_handler_source()
    assert "apply_telemetry_split" in src, (
        "webhook handler must route through apply_telemetry_split — the "
        "inlined copy was deduped in this pass; re-inlining would "
        "reintroduce the divergence"
    )


def test_webhook_endpoint_no_longer_inlines_telemetry_loop():
    """The webhook handler must NOT carry its own copy of the slug-match
    routing loop (the dedup target). The canonical slug matching lives in
    ``apply_telemetry_split``.

    Guards against a future revert: if someone re-inlines the loop, the
    signature ``if \"calories\" in slug`` literal reappears in the handler.
    """
    src = _webhook_handler_source()
    assert '"calories" in slug' not in src, (
        "webhook handler must not inline the calories-slug branch — that "
        "routing decision belongs to apply_telemetry_split"
    )
    assert "TelemetryDataModel(" not in src, (
        "webhook handler must not construct TelemetryDataModel directly — "
        "apply_telemetry_split owns that"
    )


def test_webhook_endpoint_passes_real_per_channel_counts_to_notifications():
    """post_sync_notifications must receive the real per-channel counts.

    Before the fix the webhook path always passed ``telemetry_persisted=0``
    (and ``fhir_persisted=count``) because it didn't track the split. Now
    that it routes through ``apply_telemetry_split``, both counts reflect
    reality. Source-level guard against a revert.
    """
    src = _webhook_handler_source()
    assert "fhir_persisted=len(fhir_records)" in src, (
        "webhook notification dispatch must pass the real fhir count, not "
        "the combined total"
    )
    assert "telemetry_persisted=len(telemetry_records)" in src, (
        "webhook notification dispatch must pass the real telemetry count, "
        "not the prior hard-coded 0"
    )


# ---------------------------------------------------------------------------
# Workstream B.2 (this stack): run_sync calls the clinical-events opt-in
# hook and routes writes through clinical_event_service.create_event with
# source_integration_id. Source-level guards against a revert.
# ---------------------------------------------------------------------------


def test_run_sync_wires_clinical_events_opt_in_hook():
    """``run_sync`` must call ``provider.pull_clinical_events`` gated on
    ``provider.supports_clinical_events()``, then route each returned
    event through ``clinical_event_service.create_event`` with
    ``source_integration_id=integration.id``. Source-level guard."""
    from app.services import integration_sync_service as svc
    import inspect

    src = inspect.getsource(svc.run_sync)
    assert "supports_clinical_events" in src, (
        "run_sync must probe supports_clinical_events — the opt-in gate "
        "for the clinical-events pull hook"
    )
    assert "pull_clinical_events" in src, (
        "run_sync must call pull_clinical_events on providers that opt in"
    )
    assert "create_event" in src, (
        "run_sync must route pulled events through clinical_event_service."
        "create_event (the dedup-aware write path)"
    )
    assert "source_integration_id=integration.id" in src, (
        "run_sync must stamp source_integration_id with the integration's "
        "own id — providers can't fake it"
    )


def test_opt_in_helper_handles_missing_method():
    """The ``_opt_in`` helper must return False (not raise) when the method
    is absent — that's the default for any provider that hasn't opted in."""
    from app.services.integration_sync_service import _opt_in

    class _Bare:
        pass

    assert _opt_in(_Bare(), "supports_clinical_events") is False

    class _OptIn:
        def supports_clinical_events(self):
            return True

    assert _opt_in(_OptIn(), "supports_clinical_events") is True


def test_opt_in_helper_swallows_exceptions_from_capability_probe():
    """A buggy provider that raises out of ``supports_*`` must not break the
    whole sync turn — the helper logs and treats it as not-supported."""
    from app.services.integration_sync_service import _opt_in

    class _Broken:
        def supports_clinical_events(self):
            raise RuntimeError("boom")

    assert _opt_in(_Broken(), "supports_clinical_events") is False


# ---------------------------------------------------------------------------
# Workstream E.3 (this stack): run_sync calls the examinations opt-in hook
# and routes writes through examination_service.create_examination with
# source_integration_id. Source-level guards against a revert.
# ---------------------------------------------------------------------------


def test_run_sync_wires_examinations_opt_in_hook():
    """``run_sync`` must call ``provider.pull_examinations`` gated on
    ``provider.supports_examinations()``, then route each returned exam
    through ``examination_service.create_examination`` with
    ``source_integration_id=integration.id``. Mirrors the clinical-events
    guard above. Source-level guard."""
    from app.services import integration_sync_service as svc
    import inspect

    src = inspect.getsource(svc.run_sync)
    assert "supports_examinations" in src, (
        "run_sync must probe supports_examinations — the opt-in gate "
        "for the examinations pull hook"
    )
    assert "pull_examinations" in src, (
        "run_sync must call pull_examinations on providers that opt in"
    )
    assert "create_examination" in src, (
        "run_sync must route pulled exams through examination_service."
        "create_examination (the dedup-aware write path)"
    )


# ---------------------------------------------------------------------------
# Workstream F.3 (this stack): run_sync calls the catalog-proposals opt-in
# hook and routes writes through catalog_proposal_service.apply_proposal.
# Source-level guards against a revert.
# ---------------------------------------------------------------------------


def test_run_sync_wires_catalog_proposals_opt_in_hook():
    """``run_sync`` must call ``provider.pull_catalog_proposals`` gated on
    ``provider.supports_catalog_proposals()``, then route each returned
    proposal through ``catalog_proposal_service.apply_proposal``. Mirrors
    the clinical-events + examinations guards above. Source-level guard."""
    from app.services import integration_sync_service as svc
    import inspect

    src = inspect.getsource(svc.run_sync)
    assert "supports_catalog_proposals" in src, (
        "run_sync must probe supports_catalog_proposals — the opt-in gate "
        "for the catalog-proposals pull hook"
    )
    assert "pull_catalog_proposals" in src, (
        "run_sync must call pull_catalog_proposals on providers that opt in"
    )
    assert "apply_proposal" in src, (
        "run_sync must route pulled proposals through "
        "catalog_proposal_service.apply_proposal (the kind-aware write path)"
    )


def test_run_sync_enforces_proposals_per_sync_cap():
    """``run_sync`` must honor ``INTEGRATION_MAX_PROPOSALS_PER_SYNC`` so a
    runaway provider can't spam the catalog. Source-level guard."""
    from app.services import integration_sync_service as svc

    assert svc.INTEGRATION_MAX_PROPOSALS_PER_SYNC > 0, (
        "Cap must be a positive integer — a zero / negative value would "
        "drop every proposal (or break the loop entirely)"
    )
    import inspect

    src = inspect.getsource(svc.run_sync)
    assert "INTEGRATION_MAX_PROPOSALS_PER_SYNC" in src, (
        "run_sync must reference the cap constant — providers can return "
        "unbounded lists and the engine needs to gate them"
    )


def test_sync_result_carries_proposal_counts():
    """``SyncResult`` must surface ``proposals_pulled`` /
    ``proposals_applied`` so the worker / endpoint can report them."""
    from app.services.integration_sync_service import SyncResult

    result = SyncResult()
    assert hasattr(result, "proposals_pulled"), (
        "SyncResult must expose proposals_pulled for sync-log reporting"
    )
    assert hasattr(result, "proposals_applied"), (
        "SyncResult must expose proposals_applied for sync-log reporting"
    )


# ---------------------------------------------------------------------------
# Workstream G.4 (this stack): run_sync calls the HITL-proposals opt-in
# hook and persists each spec via ``integration_proposal_service.create_proposal``.
# Source-level guards against a revert.
# ---------------------------------------------------------------------------


def test_run_sync_wires_hitl_proposals_opt_in_hook():
    """``run_sync`` must call ``provider.pull_hitl_proposals`` gated on
    ``provider.supports_hitl_proposals()``, then route each returned spec
    through ``integration_proposal_service.create_proposal`` (the dedup-
    aware insert). Mirrors the catalog-proposals guard above."""
    from app.services import integration_sync_service as svc
    import inspect

    src = inspect.getsource(svc.run_sync)
    assert "supports_hitl_proposals" in src, (
        "run_sync must probe supports_hitl_proposals — the opt-in gate "
        "for the HITL-proposals pull hook"
    )
    assert "pull_hitl_proposals" in src, (
        "run_sync must call pull_hitl_proposals on providers that opt in"
    )
    assert "create_proposal" in src, (
        "run_sync must route pulled specs through "
        "integration_proposal_service.create_proposal (the dedup-aware "
        "insert path)"
    )


def test_run_sync_enforces_hitl_proposals_per_sync_cap():
    """``run_sync`` must honor ``INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC``
    so a runaway provider can't spam the inbox."""
    from app.services import integration_sync_service as svc

    assert svc.INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC > 0, (
        "HITL cap must be a positive integer — zero / negative would "
        "drop every proposal (or break the loop entirely)"
    )
    import inspect

    src = inspect.getsource(svc.run_sync)
    assert "INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC" in src, (
        "run_sync must reference the cap constant — providers can return "
        "unbounded lists and the engine needs to gate them"
    )


def test_run_sync_emits_hitl_proposal_notification_on_insert():
    """``run_sync`` must call ``_emit_hitl_proposal_notification`` after
    inserting a new HITL proposal so the user gets a notification. The
    helper is best-effort and won't break the sync on failure, but the
    wiring must be present so the helper actually gets called."""
    from app.services import integration_sync_service as svc
    import inspect

    src = inspect.getsource(svc.run_sync)
    assert "_emit_hitl_proposal_notification" in src, (
        "run_sync must call _emit_hitl_proposal_notification for each "
        "newly-inserted HITL proposal so the user gets an inbox row"
    )


def test_sync_result_carries_hitl_proposal_counts():
    """``SyncResult`` must surface ``hitl_proposals_pulled`` /
    ``hitl_proposals_inserted`` for sync-log reporting."""
    from app.services.integration_sync_service import SyncResult

    result = SyncResult()
    assert hasattr(result, "hitl_proposals_pulled")
    assert hasattr(result, "hitl_proposals_inserted")
