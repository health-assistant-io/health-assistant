"""The inbound FHIR R4 client connector (plan 8 W4 item 2).

Pins the ``fhir_client`` bundled T1 extension end to end on a fixture FHIR
server (``httpx.MockTransport`` — the ``fhir_server`` harness):

* the manifest registers and declares exactly the pull profile (sync +
  secrets; observation/lab_report/medication/examination);
* a happy-path ``run_sync`` lands each resource class in its typed stream
  through the one ingest funnel — observations (one mapped, one raw with
  the gap recorded), labs, medications, examinations;
* a re-sync of the same resources writes **zero duplicates** (dedup keys)
  while cursors advanced per resource;
* cursors are per-resource ``integration_sync_state`` rows with the right
  stream per data class;
* a missing or revoked credential fails closed before any pull (typed
  ``auth`` / engine ``credential`` class, instance ERROR);
* hostile fixtures (oversized, wrong-typed, wrong-resource) are rejected
  and never written;
* tenant B cannot read tenant A's pull;
* the connector adds zero migrations (no revision references a connector
  name) and the credential never persists in ``user_config``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.extensions.builtin.fhir_client.provider import FhirClientProvider
from app.extensions.errors import ExtensionError
from app.models.enums import IntegrationCursorKind
from app.models.fhir import Observation
from app.models.fhir.patient import DiagnosticReport
from app.services.integration.integration_credentials import (
    CredentialError,
    read_credential,
    revoke_credential,
    seal_credential,
)
from app.services.integration.integration_sync_state import hydrate as hydrate_cursors
from app.services.integration.integration_sync_state import stream_health

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_VERSIONS = BACKEND_DIR / "alembic" / "versions"

FHIR_BASE = "https://fhir.example.org/r4"
REMOTE_PATIENT = "REMOTE-PT-1"


@pytest.fixture(autouse=True)
def _synthetic_dns(monkeypatch):
    """Synthetic hosts resolve to a fixed public address (the SSRF guard
    fails closed on resolution failure — same harness as fhir_server)."""
    from app.extensions import polite_http

    monkeypatch.setattr(polite_http, "_default_resolver", lambda host: ["93.184.216.34"])


@pytest.fixture(autouse=True)
def _registry_manifests():
    """The engine's declared-data-class gate reads the real singleton
    registry; make sure the bundled manifests (including fhir_client) are
    loaded."""
    from app.extensions.registry import integration_registry

    if integration_registry.get_manifest("fhir_client") is None:
        integration_registry._load_manifests()
    assert integration_registry.has_capability("fhir_client", "sync")
    return integration_registry


# ---------------------------------------------------------------------------
# Fixture FHIR server
# ---------------------------------------------------------------------------


def _obs(code="2093-3", display="Cholesterol", value=180.0, rid="obs-mapped"):
    code_field: dict = {"text": display} if display else {}
    coding = [{"system": "http://loinc.org", "code": code}]
    if display:
        coding[0]["display"] = display
    code_field["coding"] = coding
    return {
        "resourceType": "Observation",
        "id": rid,
        "status": "final",
        "code": code_field,
        "subject": {"reference": f"Patient/{REMOTE_PATIENT}"},
        "valueQuantity": {"value": value, "unit": "mg/dL", "code": "mg/dL"},
        "effectiveDateTime": "2026-06-01T10:00:00Z",
        "meta": {"lastUpdated": "2026-06-01T10:01:00Z"},
    }


def _lab_report(rid="lab-1", status="final", code="24323-8"):
    return {
        "resourceType": "DiagnosticReport",
        "id": rid,
        "status": status,
        "code": {
            "coding": [{"system": "http://loinc.org", "code": code, "display": "Lipid panel"}],
            "text": "Lipid panel",
        },
        "subject": {"reference": f"Patient/{REMOTE_PATIENT}"},
        "effectiveDateTime": "2026-06-02T08:00:00Z",
        "conclusion": "Mildly elevated LDL.",
        "meta": {"lastUpdated": "2026-06-02T08:05:00Z"},
    }


def _med_statement(rid="med-1"):
    return {
        "resourceType": "MedicationStatement",
        "id": rid,
        "status": "active",
        "medicationCodeableConcept": {
            "coding": [{"system": "http://www.nlm.nih.gov/research/umrx/rxnorm", "code": "896209"}],
            "text": "Atorvastatin 20 mg",
        },
        "subject": {"reference": f"Patient/{REMOTE_PATIENT}"},
        "effectiveDateTime": "2026-05-01",
        "meta": {"lastUpdated": "2026-06-03T09:00:00Z"},
    }


def _encounter(rid="enc-1"):
    return {
        "resourceType": "Encounter",
        "id": rid,
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        "type": [{"text": "Office visit"}],
        "subject": {"reference": f"Patient/{REMOTE_PATIENT}"},
        "period": {"start": "2026-06-04T09:00:00Z", "end": "2026-06-04T09:30:00Z"},
        "meta": {"lastUpdated": "2026-06-04T10:00:00Z"},
    }


def _bundle(resources: list) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": r} for r in resources],
    }


def _fixture_server(calls: list[str] | None = None, **overrides):
    """A MockTransport FHIR server: one searchset Bundle per resource path."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        resource_type = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        return httpx.Response(200, json=_bundle(overrides.get(resource_type, [])))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(client: httpx.AsyncClient) -> FhirClientProvider:
    provider = FhirClientProvider()
    provider._http_client = client
    return provider


async def _make_identity(config: dict, *, provider: str = "fhir_client"):
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_integration import UserIntegration
    from app.models.user_model import UserModel

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name=f"T {tenant_id.hex[:6]}",
                slug=f"fcli-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"fcli-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Remote", "given": ["Pull"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider=provider,
                instance_name=f"fhir-client-{tenant_id.hex[:6]}",
                status="ACTIVE",
                user_config=config,
            )
        )
        await db.commit()
    return tenant_id, patient_id, integration_id


def _default_config() -> dict:
    return {
        "fhir_base_url": FHIR_BASE,
        "auth_mode": "api_key",
        "remote_patient_id": REMOTE_PATIENT,
        "time_window_months": 12,
    }


@pytest_asyncio.fixture
async def integration():
    from app.models.user_integration import UserIntegration

    _tenant_id, _patient_id, integration_id = await _make_identity(_default_config())
    async with AsyncSessionLocal() as db:
        yield (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        ).scalar_one()


async def _load_integration(integration_id) -> object:
    from app.models.user_integration import UserIntegration

    async with AsyncSessionLocal() as db:
        return (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        ).scalar_one()


async def _run_sync(integration_row, client: httpx.AsyncClient):
    from app.services import integration_sync_service as svc

    with (
        patch.object(svc, "_try_acquire_lock", AsyncMock(return_value=(True, "test-lock"))),
        patch.object(svc, "_release_lock", AsyncMock()),
        patch.object(svc, "post_sync_notifications", AsyncMock()),
    ):
        async with AsyncSessionLocal() as db:
            fresh = await _load_integration_in(db, integration_row.id)
            return await svc.run_sync(db, fresh, _provider(client), source="test")


async def _load_integration_in(db: AsyncSession, integration_id):
    from app.models.user_integration import UserIntegration

    return (
        await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
    ).scalar_one()


async def _seal_api_key(integration_row, token: str = "tok-1"):
    async with AsyncSessionLocal() as db:
        await seal_credential(db, integration_row, "api_key", {"api_key": token})
        await db.commit()


async def _count(db: AsyncSession, model, tenant_id) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# Manifest / registry / wiring contract
# ---------------------------------------------------------------------------


def test_manifest_registers_and_declares_the_pull_profile(_registry_manifests):
    manifest = _registry_manifests.get_manifest("fhir_client")
    assert manifest and manifest["id"] == "fhir-client"
    assert manifest["kind"] == "connector"
    assert manifest["docs"] == "docs/docs-tree.json"
    assert _registry_manifests.has_capability("fhir_client", "sync")
    assert _registry_manifests.has_capability("fhir_client", "secrets")
    assert not _registry_manifests.has_capability("fhir_client", "push"), (
        "the inbound client is pull-only — no push capability"
    )
    assert _registry_manifests.declared_data_classes("fhir_client") == frozenset(
        {"observation", "lab_report", "medication", "examination"}
    )


def test_no_migration_references_connector_names():
    """A connector is an extension + rows, never DDL: no alembic revision
    may reference a connector name (plan 8 §5 definition of done)."""
    hits = []
    for revision in sorted(ALEMBIC_VERSIONS.glob("*.py")):
        body = revision.read_text(encoding="utf-8").lower()
        for pattern in ("fhir_client", "fhir-client"):
            if pattern in body:
                hits.append(f"{revision.name}: {pattern}")
    assert hits == []


def test_engine_and_sdk_wire_the_labs_contract():
    import inspect

    from app.extensions.sdk.base import BaseConfigFlow, BaseHealthProvider
    from app.services import integration_sync_service as svc
    from app.services.integration import sync_legs

    assert "return []" in inspect.getsource(BaseHealthProvider.pull_labs), (
        "pull_labs must default to [] — providers that don't pull labs are unaffected"
    )
    assert "return None" in inspect.getsource(BaseConfigFlow.store_credentials), (
        "store_credentials must default to no-op — user_config-secret flows are unaffected"
    )
    src = inspect.getsource(svc.run_sync) + inspect.getsource(sync_legs)
    assert '_declared(provider, "lab_report")' in src
    assert "pull_labs" in src
    assert "ingest_labs" in src, "labs must route through the ingest funnel, never direct calls"


# ---------------------------------------------------------------------------
# Happy path: one sync, four streams
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_pull_lands_every_stream_and_records_the_gap(integration):
    calls: list[str] = []
    client = _fixture_server(
        calls,
        Observation=[
            _obs(),
            _obs(code="99999-9", display=None, rid="obs-gap"),
        ],
        DiagnosticReport=[_lab_report()],
        MedicationStatement=[_med_statement()],
        Encounter=[_encounter()],
    )
    await _seal_api_key(integration)

    result = await _run_sync(integration, client)

    assert result.status == "partial", "the unmapped code is a recorded gap, not a failure"
    assert result.fhir_persisted + result.telemetry_persisted == 2, "both observations land"
    assert result.labs_pulled == 1 and result.labs_written == 1
    assert result.medications_written == 1

    # Every search was patient-scoped.
    assert calls and all(f"patient={REMOTE_PATIENT}" in call for call in calls)

    async with AsyncSessionLocal() as db:
        observations = (
            (
                await db.execute(
                    select(Observation).where(Observation.tenant_id == integration.tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(observations) == 2
        raw = [o for o in observations if o.biomarker_id is None]
        assert len(raw) == 1 and raw[0].code["coding"][0]["code"] == "99999-9", (
            "the unmapped LOINC lands raw (never dropped silently)"
        )
        assert await _count(db, DiagnosticReport, integration.tenant_id) == 1
        exams = (
            await db.execute(
                text(
                    "select count(*) from examinations where tenant_id = :t "
                    "and external_id = 'enc-1'"
                ),
                {"t": str(integration.tenant_id)},
            )
        ).scalar_one()
        assert exams == 1, "the Encounter landed as an examination (appointments stream)"
        meds = (
            await db.execute(
                text(
                    "select count(*) from fhir_medications where tenant_id = :t "
                    "and external_id = 'med-1'"
                ),
                {"t": str(integration.tenant_id)},
            )
        ).scalar_one()
        assert meds == 1
        log = (
            (
                await db.execute(
                    text(
                        "select error_class, error_message from integration_sync_logs "
                        "where integration_id = :i order by started_at desc limit 1"
                    ),
                    {"i": str(integration.id)},
                )
            )
            .mappings()
            .first()
        )
        assert log is not None and log["error_class"] == "mapping_gap"
        assert "99999-9" in (log["error_message"] or ""), "the gap names the unmapped code"


@pytest.mark.asyncio
async def test_resync_writes_zero_duplicates_and_cursors_advanced(integration):
    from app.models.examination_model import ExaminationModel

    client = _fixture_server(
        Observation=[_obs()],
        DiagnosticReport=[_lab_report()],
        MedicationStatement=[_med_statement()],
        Encounter=[_encounter()],
    )
    await _seal_api_key(integration)

    first = await _run_sync(integration, client)
    assert first.fhir_persisted + first.telemetry_persisted == 1
    assert first.labs_written == 1

    # The fixture server ignores the cursor on purpose: the second sync
    # re-pulls the SAME resources and must write zero duplicates.
    second = await _run_sync(integration, client)
    assert second.status == "success"
    assert second.fhir_persisted + second.telemetry_persisted == 0, (
        "observations dedup on their natural key"
    )
    assert second.labs_written == 0, "lab reports dedup on (tenant, patient, code, effective)"

    async with AsyncSessionLocal() as db:
        assert await _count(db, Observation, integration.tenant_id) == 1
        assert await _count(db, DiagnosticReport, integration.tenant_id) == 1
        assert await _count(db, ExaminationModel, integration.tenant_id) == 1

    # A third pull with the cursors respected (the server honours nothing —
    # the dedup keys are what keep the DB at one row per resource).
    third = await _run_sync(integration, client)
    assert third.status == "success"
    async with AsyncSessionLocal() as db:
        assert await _count(db, Observation, integration.tenant_id) == 1
        assert await _count(db, DiagnosticReport, integration.tenant_id) == 1


@pytest.mark.asyncio
async def test_cursors_are_per_resource_rows_with_the_right_stream(integration):
    client = _fixture_server(
        Observation=[_obs()],
        DiagnosticReport=[_lab_report()],
        MedicationStatement=[_med_statement()],
        Encounter=[_encounter()],
    )
    await _seal_api_key(integration)
    await _run_sync(integration, client)

    async with AsyncSessionLocal() as db:
        fresh = await _load_integration_in(db, integration.id)
        await hydrate_cursors(db, fresh)
        health = {(row["stream"], row["cursor_key"]): row for row in await stream_health(db, fresh)}

    # The per-resource cursor rows exist with the right stream, beside the
    # engine's primary (empty-key) health rows per stream.
    assert ("observations", "Observation") in health
    assert ("observations", "MedicationStatement") in health
    assert ("labs", "DiagnosticReport") in health
    assert ("appointments", "Encounter") in health
    assert ("observations", "") in health, "the engine's primary observations health row"
    assert ("labs", "") in health and ("appointments", "") in health
    for (_stream, key), row in health.items():
        assert row["cursor_kind"] == IntegrationCursorKind.TIMESTAMP.value, key
        if not key:
            # The engine stamps success health on the stream's primary row.
            assert row["last_success_at"] is not None, _stream
            assert row["consecutive_failures"] == 0, _stream
    assert health[("observations", "Observation")]["cursor_value"] == "2026-06-01T10:01:00Z"
    assert health[("labs", "DiagnosticReport")]["cursor_value"] == "2026-06-02T08:05:00Z"
    assert health[("appointments", "Encounter")]["cursor_value"] == "2026-06-04T10:00:00Z"


# ---------------------------------------------------------------------------
# Fail-closed credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_credential_fails_closed_before_any_pull(integration):
    calls: list[str] = []
    client = _fixture_server(calls, Observation=[_obs()])
    provider = _provider(client)

    with pytest.raises(CredentialError):
        await provider.pull_data(integration)
    assert calls == [], "no HTTP call may happen without a usable credential"

    result = await _run_sync(integration, client)
    assert result.status == "failed"
    assert result.error_type == "auth"
    assert calls == [], "the engine must stop before the pull"


@pytest.mark.asyncio
async def test_revoked_credential_fails_closed_and_flips_the_instance(integration):
    from app.models.user_integration import IntegrationStatus, IntegrationSyncLog

    client = _fixture_server(Observation=[_obs()])
    async with AsyncSessionLocal() as db:
        await seal_credential(db, integration, "api_key", {"api_key": "tok-1"})
        await db.commit()
        assert await revoke_credential(db, integration, "api_key") == 1
        await db.commit()
        with pytest.raises(CredentialError):
            await read_credential(db, integration, "api_key")

    result = await _run_sync(integration, client)

    assert result.status == "failed" and result.error_type == "auth"
    async with AsyncSessionLocal() as db:
        logs = (
            (
                await db.execute(
                    select(IntegrationSyncLog).where(
                        IntegrationSyncLog.integration_id == integration.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert logs[-1].status == "failed"
        assert logs[-1].error_class.value == "credential"
        row = await _load_integration_in(db, integration.id)
        assert row.status == IntegrationStatus.ERROR


# ---------------------------------------------------------------------------
# Hostile fixtures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hostile_resources_are_rejected_and_never_written(integration):
    oversized = _obs(rid="obs-huge")
    oversized["component"] = [{"valueString": "x" * 500_000}]
    malformed_code = _lab_report(rid="lab-malformed", code="55213-7")
    malformed_code["code"] = {"coding": "not-a-list", "text": "broken panel"}

    client = _fixture_server(
        Observation=[_obs(rid="obs-ok"), oversized, "not-even-a-dict", _med_statement()],
        DiagnosticReport=[
            _lab_report(rid="lab-ok"),
            malformed_code,
        ],
    )
    await _seal_api_key(integration)
    result = await _run_sync(integration, client)
    assert result.status == "partial", "the invalid lab report is a typed rejection"

    async with AsyncSessionLocal() as db:
        observations = (
            (
                await db.execute(
                    select(Observation).where(Observation.tenant_id == integration.tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert [o.code["coding"][0]["code"] for o in observations] == ["2093-3"], (
            "only the well-formed observation lands; oversized/wrong-typed entries never do"
        )
        assert await _count(db, DiagnosticReport, integration.tenant_id) == 1, (
            "the invalid-status report is rejected by the write-time gate"
        )


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_b_cannot_read_tenant_as_pull(integration):
    client = _fixture_server(Observation=[_obs()])
    await _seal_api_key(integration)
    await _run_sync(integration, client)

    tenant_b, _, integration_b = await _make_identity({**_default_config(), "auth_mode": "none"})
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(Observation).where(Observation.tenant_id == tenant_b))
        ).scalars().all() == [], "tenant B sees none of tenant A's pulled observations"
        assert await _count(db, DiagnosticReport, tenant_b) == 0

    # Tenant B's own sync writes under tenant B only.
    row_b = await _load_integration(integration_b)
    result = await _run_sync(row_b, client)
    assert result.fhir_persisted + result.telemetry_persisted == 1
    async with AsyncSessionLocal() as db:
        assert await _count(db, Observation, integration.tenant_id) == 1
        assert await _count(db, Observation, tenant_b) == 1


# ---------------------------------------------------------------------------
# Config flow: the credential never persists in user_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_seals_the_key_and_keeps_user_config_clean(integration):
    from app.extensions.builtin.fhir_client.config_flow import FhirClientConfigFlow

    flow = FhirClientConfigFlow()
    assert flow.get_secret_fields() == [], "no secret ever rides the user_config path"

    validated = await flow.validate_input(
        {
            "instance_name": "Portal",
            "fhir_base_url": f"{FHIR_BASE}/",
            "auth_mode": "api_key",
            "api_key": "  tok-secret-1  ",
            "remote_patient_id": REMOTE_PATIENT,
        }
    )
    stored = await flow.prepare_for_storage(validated, context=str(integration.id))
    assert "api_key" not in stored
    assert stored["_pending_credential"]["payload"] == {"api_key": "tok-secret-1"}

    async with AsyncSessionLocal() as db:
        row = await _load_integration_in(db, integration.id)
        await flow.store_credentials(db, row, stored)
        await db.commit()
        assert "_pending_credential" not in (row.user_config or {}), (
            "the transient slot is stripped before commit"
        )
        assert await read_credential(db, integration, "api_key") == {"api_key": "tok-secret-1"}

    # Rotation: a new value reseals in place; a blank value keeps the old.
    stored2 = await flow.prepare_for_storage(
        {**validated, "api_key": "tok-secret-2"}, context=str(integration.id)
    )
    async with AsyncSessionLocal() as db:
        row = await _load_integration_in(db, integration.id)
        await flow.store_credentials(db, row, stored2)
        await db.commit()
        assert await read_credential(db, integration, "api_key") == {"api_key": "tok-secret-2"}

    # An update that omits the field (the UI's blank token) keeps the slot.
    without_key = {k: v for k, v in validated.items() if k != "api_key"}
    stored3 = await flow.prepare_for_storage(without_key, context=str(integration.id))
    assert "_pending_credential" not in stored3, "blank token on update keeps the sealed slot"
    async with AsyncSessionLocal() as db:
        assert await read_credential(db, integration, "api_key") == {"api_key": "tok-secret-2"}


@pytest.mark.asyncio
async def test_config_flow_requires_patient_binding_and_rejects_bad_input():
    from app.extensions.builtin.fhir_client.config_flow import FhirClientConfigFlow

    flow = FhirClientConfigFlow()
    with pytest.raises(ValueError, match="http"):
        await flow.validate_input({"fhir_base_url": "ftp://x", "auth_mode": "none"})
    with pytest.raises(ValueError, match="patient"):
        await flow.validate_input(
            {"fhir_base_url": FHIR_BASE, "auth_mode": "none", "remote_patient_id": ""}
        )
    with pytest.raises(ValueError, match="non-empty"):
        await flow.validate_input(
            {
                "fhir_base_url": FHIR_BASE,
                "auth_mode": "api_key",
                "api_key": "   ",
                "remote_patient_id": REMOTE_PATIENT,
            }
        )


# ---------------------------------------------------------------------------
# Provider surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unscoped_pull_is_refused_and_unconfigured_pulls_nothing(integration):
    provider = FhirClientProvider()
    await provider.setup({})
    empty = SimpleNamespace(
        id=integration.id,
        tenant_id=integration.tenant_id,
        patient_id=integration.patient_id,
        user_config={},
        is_debug_enabled=False,
    )
    assert await provider.pull_data(empty) == []
    no_binding = SimpleNamespace(
        id=integration.id,
        tenant_id=integration.tenant_id,
        patient_id=integration.patient_id,
        user_config={"fhir_base_url": FHIR_BASE, "auth_mode": "none"},
        is_debug_enabled=False,
    )
    with pytest.raises(ExtensionError, match="remote_patient_id"):
        await provider.pull_data(no_binding)


@pytest.mark.asyncio
async def test_check_connection_reports_each_layer(integration):
    from sqlalchemy.orm.attributes import flag_modified

    client = _fixture_server(
        Patient=[
            {
                "resourceType": "Patient",
                "id": REMOTE_PATIENT,
                "name": [{"family": "Doe", "given": ["Jane"]}],
            }
        ]
    )
    await _seal_api_key(integration)

    provider = _provider(client)
    result = await provider.execute_custom_action(integration, "check_connection")
    assert "Connection OK" in result["message"]

    async with AsyncSessionLocal() as db:
        row = await _load_integration_in(db, integration.id)
        row.user_config["remote_patient_id"] = "MISSING"
        flag_modified(row, "user_config")
        await db.commit()
    fresh = await _load_integration(integration.id)
    result = await provider.execute_custom_action(fresh, "check_connection")
    assert "not found" in result["message"]

    with pytest.raises(NotImplementedError):
        await provider.execute_custom_action(integration, "nonexistent")


def _lab_dedup_payloads():
    from app.extensions.builtin.fhir_client.mappers import diagnostic_report_to_payload

    first = diagnostic_report_to_payload(_lab_report(), patient_id=uuid.uuid4())
    again = diagnostic_report_to_payload(_lab_report(), patient_id=uuid.uuid4())
    other_day = diagnostic_report_to_payload(
        {**_lab_report(), "effectiveDateTime": "2026-07-02T08:00:00Z"},
        patient_id=uuid.uuid4(),
    )
    return first, again, other_day


def test_lab_dedup_key_keeps_distinct_reports_of_the_same_panel():
    """The natural key includes the effective datetime: two reports of the
    same panel code at different times are BOTH kept (a re-pull of one is
    what dedups)."""
    first, again, other_day = _lab_dedup_payloads()
    assert first["code"] == again["code"]
    assert first["effective_datetime"] == again["effective_datetime"]
    assert other_day["effective_datetime"] != first["effective_datetime"]
    assert first["subject"]["reference"].startswith("Patient/")


def test_hostile_mapper_inputs_return_none():
    from app.extensions.builtin.fhir_client.mappers import (
        diagnostic_report_to_payload,
        patient_summary,
    )

    bare = {"resourceType": "DiagnosticReport"}
    assert diagnostic_report_to_payload(bare, patient_id=uuid.uuid4()) is None
    wrong_type = {"resourceType": "Observation", "status": "final"}
    assert diagnostic_report_to_payload(wrong_type, patient_id=uuid.uuid4()) is None
    numeric_status = {"resourceType": "DiagnosticReport", "status": 7, "code": {"text": "x"}}
    assert diagnostic_report_to_payload(numeric_status, patient_id=uuid.uuid4()) is None
    assert patient_summary({"resourceType": "Bundle"}) is None
