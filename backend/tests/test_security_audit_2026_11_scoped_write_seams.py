"""Scoped-credential write seams (2026-11 scoped-credential audit A1-A7).

A PAT/integration principal carries its owner's role *name* but only its own
intersected permission list. These tests pin the seams where a
``patient:read``-only credential could still write, or where a
``role == SYSTEM_ADMIN`` branch widened a scoped credential past tenant scope:

- A1 clinical write seams: medications / allergies / immunizations,
  event occurrences + links + biomarker correlations, examination and
  document deletes, and the bridge mutation routes;
- A2 observations/telemetry ingest;
- A3 role-trust branches (audit list/export, broadcast, elevation ledger,
  import job gate, patient-less documents);
- A4 break-glass deny/revoke;
- A5 bridge ``/ai/*``;
- A7 tenant-restore sidecars (shared unit vocabulary, surfaced failures).
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.errors import AuthorizationError
from app.models.access_elevation import AccessElevation, ElevationScope, ElevationStatus
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import Unit
from app.models.clinical_event import ClinicalEvent, ClinicalEventType
from app.models.concept_model import Concept
from app.models.document_model import DocumentModel
from app.models.enums import CatalogScope, Role
from app.models.examination_model import ExaminationModel
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Observation, Patient
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.schemas.allergy import AllergyIntoleranceCreate, AllergyIntoleranceUpdate
from app.schemas.clinical_event import ClinicalEventOccurrenceCreate
from app.schemas.medication import MedicationRecordCreate, MedicationRecordUpdate
from app.schemas.user import TokenData
from app.schemas.vaccine import PatientImmunizationCreate, PatientImmunizationUpdate
from app.services.clinical import (
    allergy_service,
    clinical_event_service,
    examination_service,
    medication_service,
    vaccine_service,
)
from app.services.documents import document_service
from tests._auth_helpers import create_tenant, create_user

READ_ONLY = ["patient:read"]
WRITER = [
    "patient:read",
    "clinical:write",
    "catalog:create",
    "catalog:moderate",
    "document:write",
]


def scoped(owner: UserModel, permissions: list[str], *, scope: str = "all") -> TokenData:
    """A PAT-shaped principal owned by ``owner`` with exactly ``permissions``."""
    return TokenData(
        user_id=owner.id,
        tenant_id=owner.tenant_id,
        role=owner.role,
        sub=str(owner.id),
        pat_id=str(uuid.uuid4()),
        pat_permissions=list(permissions),
        pat_patient_scope=scope,
        pat_patient_ids=[],
    )


@pytest_asyncio.fixture
async def ctx():
    """Tenant + ADMIN owner + one patient + a bridge integration bound to it."""
    tenant_id = await create_tenant("Scoped seams")
    owner = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Seam", "given": ["Record"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=owner.id,
                patient_id=patient_id,
                provider="health_assistant_bridge",
                instance_name="seams",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()
    return SimpleNamespace(
        tenant_id=tenant_id,
        owner=owner,
        patient_id=patient_id,
        integration_id=integration_id,
        read_only=scoped(owner, READ_ONLY),
        writer=scoped(owner, WRITER),
    )


async def _medication(db, ctx, *, note="seam") -> Medication:
    return await medication_service.add_patient_medication(
        db,
        ctx.writer,
        MedicationRecordCreate(patient_id=ctx.patient_id, code={"text": "Seam Med"}, note=note),
    )


# ---------------------------------------------------------------------------
# A1 — medications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_medication_writes_refuse_read_only_principal(ctx):
    data = MedicationRecordCreate(patient_id=ctx.patient_id, code={"text": "Seam Med"})
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await medication_service.add_patient_medication(db, ctx.read_only, data)

    async with AsyncSessionLocal() as db:
        record = await _medication(db, ctx)

    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await medication_service.update_patient_medication(
                db, record.id, ctx.read_only, MedicationRecordUpdate(note="nope")
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await medication_service.delete_patient_medication(db, record.id, ctx.read_only)

    # The row survived both refused attempts.
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(Medication.id).where(Medication.id == record.id))
        ).scalar_one_or_none() == record.id


@pytest.mark.asyncio
async def test_medication_write_capable_principal_succeeds(ctx):
    async with AsyncSessionLocal() as db:
        record = await _medication(db, ctx)
    async with AsyncSessionLocal() as db:
        updated = await medication_service.update_patient_medication(
            db, record.id, ctx.writer, MedicationRecordUpdate(note="updated")
        )
        assert updated is not None and updated.note == "updated"
    async with AsyncSessionLocal() as db:
        assert await medication_service.delete_patient_medication(db, record.id, ctx.writer)


# ---------------------------------------------------------------------------
# A1 — allergies / immunizations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allergy_writes_refuse_read_only_principal(ctx):
    data = AllergyIntoleranceCreate(patient_id=ctx.patient_id, code={"text": "Dust"})
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await allergy_service.add_patient_allergy(db, ctx.read_only, data)

    async with AsyncSessionLocal() as db:
        record = await allergy_service.add_patient_allergy(db, ctx.writer, data)
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await allergy_service.update_patient_allergy(
                db, record.id, ctx.read_only, AllergyIntoleranceUpdate(note="nope")
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await allergy_service.delete_patient_allergy(db, record.id, ctx.read_only)
    async with AsyncSessionLocal() as db:
        assert await allergy_service.delete_patient_allergy(db, record.id, ctx.writer)


@pytest.mark.asyncio
async def test_immunization_writes_refuse_read_only_principal(ctx):
    data = PatientImmunizationCreate(
        patient_id=ctx.patient_id,
        vaccine_code={"text": "Influenza"},
        administered_at=datetime.now(UTC),
    )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await vaccine_service.add_patient_immunization(db, ctx.read_only, data)

    async with AsyncSessionLocal() as db:
        record = await vaccine_service.add_patient_immunization(db, ctx.writer, data)
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await vaccine_service.update_patient_immunization(
                db, record.id, ctx.read_only, PatientImmunizationUpdate(note="nope")
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await vaccine_service.delete_patient_immunization(db, record.id, ctx.read_only)
    async with AsyncSessionLocal() as db:
        assert await vaccine_service.delete_patient_immunization(db, record.id, ctx.writer)


# ---------------------------------------------------------------------------
# A1 — clinical-event subresources
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def event_ctx(ctx):
    """A clinical event + event type + examination + observation + anatomy."""
    category = Concept(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        scope=CatalogScope.TENANT,
        slug=f"seam-cat-{uuid.uuid4().hex[:8]}",
        name="Seam category",
    )
    event_type = ClinicalEventType(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        name="Seam type",
        slug=f"seam-type-{uuid.uuid4().hex[:8]}",
        category_concept_id=category.id,
    )
    event = ClinicalEvent(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        patient_id=ctx.patient_id,
        title="Seam event",
    )
    examination = ExaminationModel(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        patient_id=ctx.patient_id,
        notes="Seam exam",
    )
    observation = Observation(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        patient_id=ctx.patient_id,
        status="final",
        code={"text": "Seam obs"},
        subject={"reference": f"Patient/{ctx.patient_id}"},
    )
    anatomy = AnatomyStructure(
        id=uuid.uuid4(),
        slug=f"seam-anatomy-{uuid.uuid4().hex[:8]}",
        name="Seam anatomy",
    )
    async with AsyncSessionLocal() as db:
        db.add(category)
        await db.flush()
        db.add_all([event_type, event, examination, observation, anatomy])
        await db.commit()
    return SimpleNamespace(
        **vars(ctx),
        event_id=event.id,
        event_type_id=event_type.id,
        examination_id=examination.id,
        observation_id=observation.id,
        anatomy_id=anatomy.id,
    )


@pytest.mark.asyncio
async def test_event_subresources_refuse_read_only_principal(event_ctx):
    ctx = event_ctx
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.add_occurrence(
                db,
                ctx.event_id,
                ctx.read_only,
                ClinicalEventOccurrenceCreate(occurred_at=datetime.now(UTC), title="x"),
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.delete_occurrence(
                db, ctx.event_id, uuid.uuid4(), ctx.read_only
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.link_examination(
                db, ctx.event_id, ctx.read_only, ctx.examination_id
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.link_observation(
                db, ctx.event_id, ctx.read_only, ctx.observation_id
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.link_anatomy(
                db, ctx.event_id, ctx.read_only, ctx.anatomy_id
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.unlink_anatomy(
                db, ctx.event_id, ctx.anatomy_id, ctx.read_only
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.add_correlated_biomarker(
                db, ctx.event_type_id, uuid.uuid4(), actor=ctx.read_only
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.remove_correlated_biomarker(
                db, ctx.event_type_id, uuid.uuid4(), actor=ctx.read_only
            )


@pytest.mark.asyncio
async def test_event_occurrence_write_capable_principal_succeeds(event_ctx):
    ctx = event_ctx
    async with AsyncSessionLocal() as db:
        result = await clinical_event_service.add_occurrence(
            db,
            ctx.event_id,
            ctx.writer,
            ClinicalEventOccurrenceCreate(occurred_at=datetime.now(UTC), title="relapse"),
        )
    assert result["occurrences"]


# ---------------------------------------------------------------------------
# A1 — examination / document deletes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_examination_and_document_delete_refuse_read_only_principal(ctx):
    examination_id = uuid.uuid4()
    document_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            ExaminationModel(
                id=examination_id,
                tenant_id=ctx.tenant_id,
                patient_id=ctx.patient_id,
                notes="Delete me",
            )
        )
        await db.flush()
        db.add(
            DocumentModel(
                id=document_id,
                filename="seam.txt",
                file_path=f"/tmp/opencode/{document_id}.txt",
                owner_id=ctx.owner.id,
                tenant_id=ctx.tenant_id,
                patient_id=ctx.patient_id,
                status="uploaded",
                progress=0,
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        examination = (
            await db.execute(select(ExaminationModel).where(ExaminationModel.id == examination_id))
        ).scalar_one()
        with pytest.raises(AuthorizationError):
            await examination_service.delete_examination(db, examination, ctx.read_only)

    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await document_service.delete_document(str(document_id), db, ctx.read_only)

    # Both rows are still there.
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(
                select(ExaminationModel.id).where(ExaminationModel.id == examination_id)
            )
        ).scalar_one_or_none() == examination_id
        assert (
            await db.execute(select(DocumentModel.id).where(DocumentModel.id == document_id))
        ).scalar_one_or_none() == document_id


# ---------------------------------------------------------------------------
# A1 — bridge mutation routes
# ---------------------------------------------------------------------------


def _bridge_request(payload: dict):
    request = SimpleNamespace(headers={}, query_params={})
    request.json = AsyncMock(return_value=payload)
    return request


@pytest.mark.asyncio
async def test_bridge_mutation_routes_refuse_read_only_principal(
    ctx, monkeypatch: pytest.MonkeyPatch
):
    from app.extensions.builtin.health_assistant_bridge.provider import (
        HealthAssistantBridgeProvider,
    )
    from app.services.integration import integration_actor

    provider = HealthAssistantBridgeProvider()
    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()

    async def _read_only_actor(_db, _integration):
        return ctx.read_only

    monkeypatch.setattr(integration_actor, "resolve_integration_actor", _read_only_actor)

    with pytest.raises(AuthorizationError):
        await provider._create_medication(
            integration,
            _bridge_request({"code": {"text": "Bridge Med"}, "client_request_id": "ro-1"}),
        )
    with pytest.raises(AuthorizationError):
        await provider._create_allergy(
            integration,
            _bridge_request({"code": {"text": "Bridge Allergy"}, "client_request_id": "ro-2"}),
        )
    with pytest.raises(AuthorizationError):
        await provider._create_vaccine(
            integration,
            _bridge_request(
                {"vaccine_code": {"text": "Bridge Vaccine"}, "client_request_id": "ro-3"}
            ),
        )

    # Bridge deletes go through the same actor gate (the proxy never runs the
    # route gate, so a read-only principal must be refused at the seam).
    examination_id = uuid.uuid4()
    document_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            ExaminationModel(
                id=examination_id,
                tenant_id=ctx.tenant_id,
                patient_id=ctx.patient_id,
                notes="Bridge delete probe",
            )
        )
        await db.flush()
        db.add(
            DocumentModel(
                id=document_id,
                filename="bridge-delete.txt",
                file_path=f"/tmp/opencode/{document_id}.txt",
                owner_id=ctx.owner.id,
                tenant_id=ctx.tenant_id,
                patient_id=ctx.patient_id,
                status="uploaded",
                progress=0,
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()

    with pytest.raises(AuthorizationError):
        await provider._delete_examination(integration, str(examination_id), _bridge_request({}))
    with pytest.raises(AuthorizationError):
        await provider._delete_document(integration, str(document_id), _bridge_request({}))
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(
                select(ExaminationModel.id).where(ExaminationModel.id == examination_id)
            )
        ).scalar_one_or_none() == examination_id
        assert (
            await db.execute(select(DocumentModel.id).where(DocumentModel.id == document_id))
        ).scalar_one_or_none() == document_id


@pytest.mark.asyncio
async def test_bridge_mutation_routes_write_capable_principal_succeeds(
    ctx, monkeypatch: pytest.MonkeyPatch
):
    from app.extensions.builtin.health_assistant_bridge.provider import (
        HealthAssistantBridgeProvider,
    )
    from app.services.integration import integration_actor

    provider = HealthAssistantBridgeProvider()
    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()

    async def _writer_actor(_db, _integration):
        return ctx.writer

    monkeypatch.setattr(integration_actor, "resolve_integration_actor", _writer_actor)

    created = await provider._create_medication(
        integration,
        _bridge_request({"code": {"text": "Bridge Med"}, "client_request_id": "rw-1"}),
    )
    updated = await provider._update_medication(
        integration, created["id"], _bridge_request({"note": "bridge update"})
    )
    assert updated["note"] == "bridge update"
    deleted = await provider._delete_medication(integration, created["id"], _bridge_request({}))
    assert deleted["deleted"] is True

    # The row is gone (the hard delete runs inside the provider's session).
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(Medication.id).where(Medication.id == uuid.UUID(created["id"])))
        ).scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# A2 — observations / telemetry ingest
# ---------------------------------------------------------------------------


def _observation(tenant_id, patient_id, *, slug="seam") -> Observation:
    return Observation(
        tenant_id=tenant_id,
        patient_id=patient_id,
        status="final",
        code={"coding": [{"system": "urn:local", "code": slug}]},
        subject={"reference": f"Patient/{patient_id}"},
        effective_datetime=datetime.now(UTC),
        value_quantity={"value": 1.0, "unit": "mg/dL"},
    )


@pytest.mark.asyncio
async def test_ingest_refuses_read_only_principal(ctx):
    from app.services.integration import integration_ingest as funnel

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()
        with pytest.raises(AuthorizationError):
            await funnel.ingest_observations(
                db,
                integration,
                [_observation(ctx.tenant_id, ctx.patient_id)],
                actor=ctx.read_only,
            )
        with pytest.raises(AuthorizationError):
            await funnel.ingest_telemetry(
                db,
                integration,
                [funnel.TelemetryReading(slug="steps", value=1.0, timestamp=datetime.now(UTC))],
                actor=ctx.read_only,
            )


@pytest.mark.asyncio
async def test_ingest_accepts_write_capable_principal(ctx):
    from app.services.integration import integration_ingest as funnel

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()
        report = await funnel.ingest_observations(
            db,
            integration,
            [_observation(ctx.tenant_id, ctx.patient_id)],
            actor=ctx.writer,
        )
    assert report.received == 1
    assert report.written == 1


# ---------------------------------------------------------------------------
# A3 — role-trust branches stay tenant-scoped for scoped credentials
# ---------------------------------------------------------------------------


def _scoped_system_admin(tenant_id) -> TokenData:
    owner_id = uuid.uuid4()
    return TokenData(
        user_id=owner_id,
        tenant_id=tenant_id,
        role=Role.SYSTEM_ADMIN.value,
        sub=str(owner_id),
        pat_id=str(uuid.uuid4()),
        pat_permissions=["audit:read", "tenant:read", "patient:read"],
        pat_patient_scope="all",
        pat_patient_ids=[],
    )


@pytest.mark.asyncio
async def test_scoped_system_admin_audit_view_stays_in_its_tenant(ctx):
    from app.api.v1.endpoints.admin import _resolve_audit_tenant

    scoped_admin = _scoped_system_admin(ctx.tenant_id)
    own = await _resolve_audit_tenant(scoped_admin, None, False, action="admin.audit_view")
    assert own == ctx.tenant_id, "a scoped SYSTEM_ADMIN credential reads its own tenant only"

    with pytest.raises(HTTPException) as denied:
        await _resolve_audit_tenant(scoped_admin, None, True, action="admin.audit_view")
    assert denied.value.status_code == 403

    foreign = uuid.uuid4()
    with pytest.raises(HTTPException) as hidden:
        await _resolve_audit_tenant(scoped_admin, foreign, False, action="admin.audit_view")
    assert hidden.value.status_code == 404


@pytest.mark.asyncio
async def test_scoped_system_admin_cannot_broadcast_system_wide(ctx):
    from app.api.v1.endpoints.admin import broadcast_notification

    with pytest.raises(HTTPException) as denied:
        await broadcast_notification(
            title="seam",
            body=None,
            severity="info",
            scope="system",
            tenant_id=None,
            current_user=_scoped_system_admin(ctx.tenant_id),
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_scoped_system_admin_elevation_ledger_stays_scoped(ctx):
    from app.api.v1.endpoints.admin_elevations import list_access_elevations

    scoped_admin = _scoped_system_admin(ctx.tenant_id)
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as system_scope:
            await list_access_elevations(
                tenant_id=None,
                scope=ElevationScope.SYSTEM,
                status_filter=None,
                current_user=scoped_admin,
                db=db,
            )
        assert system_scope.value.status_code == 403

        with pytest.raises(HTTPException) as cross_tenant:
            await list_access_elevations(
                tenant_id=uuid.uuid4(),
                scope=None,
                status_filter=None,
                current_user=scoped_admin,
                db=db,
            )
        assert cross_tenant.value.status_code == 404


@pytest.mark.asyncio
async def test_scoped_system_admin_import_job_gate_is_owner_scoped(ctx):
    from app.api.v1.endpoints.import_data import _authorize_job_access

    foreign_job = SimpleNamespace(user_id=uuid.uuid4())
    with pytest.raises(HTTPException) as hidden:
        _authorize_job_access(foreign_job, _scoped_system_admin(ctx.tenant_id))
    assert hidden.value.status_code == 404

    # The unscoped operator keeps the tenant-wide gate.
    operator = TokenData(
        user_id=ctx.owner.id,
        tenant_id=ctx.tenant_id,
        role=Role.SYSTEM_ADMIN.value,
        sub=str(ctx.owner.id),
    )
    _authorize_job_access(foreign_job, operator)


@pytest.mark.asyncio
async def test_scoped_system_admin_patientless_document_is_uploader_only(ctx):
    from app.api.v1.endpoints.documents import _require_document_patient_access

    document = SimpleNamespace(patient_id=None, owner_id=uuid.uuid4())
    scoped_admin = _scoped_system_admin(ctx.tenant_id)
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as denied:
            await _require_document_patient_access(document, scoped_admin, db)
        assert denied.value.status_code == 403

        operator = TokenData(
            user_id=ctx.owner.id,
            tenant_id=ctx.tenant_id,
            role=Role.SYSTEM_ADMIN.value,
            sub=str(ctx.owner.id),
        )
        await _require_document_patient_access(document, operator, db)


@pytest.mark.asyncio
async def test_scoped_system_admin_document_fetch_is_tenant_scoped(ctx):
    """The ``tenant_id=None`` operator fetch never applies to a scoped credential."""
    from app.api.v1.endpoints.documents import get_document_endpoint

    document_id = uuid.uuid4()
    other_tenant = await create_tenant("Other tenant")
    foreign_owner = await create_user(role=Role.USER, tenant_id=other_tenant)
    async with AsyncSessionLocal() as db:
        db.add(
            DocumentModel(
                id=document_id,
                filename="foreign.txt",
                file_path=f"/tmp/opencode/{document_id}.txt",
                owner_id=foreign_owner.id,
                tenant_id=other_tenant,
                patient_id=None,
                status="uploaded",
                progress=0,
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as hidden:
            await get_document_endpoint(
                document_id=str(document_id),
                current_user=_scoped_system_admin(ctx.tenant_id),
                db=db,
            )
        assert hidden.value.status_code == 404


@pytest.mark.asyncio
async def test_connector_erasure_removes_its_scoped_principal(ctx):
    """Deleting an integration with a live principal must not trip the FK/check.

    ``integration_principals.integration_id`` is ``ON DELETE SET NULL`` while
    ``ck_integration_principals_integration_kind_requires_binding`` rejects a
    NULL binding for an ``integration``-kind row, so connector erasure has to
    remove the bound principal rows (revocation is the audited record).
    """
    from app.services.integration import integration_principals
    from app.services.integration.integration_ingest import _require_ingest_actor

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()
        await _require_ingest_actor(db, integration, None)
        await db.commit()
        removed = await integration_principals.delete_for_integration(
            db, tenant_id=ctx.tenant_id, integration_id=integration.id
        )
        assert removed >= 1
        await db.delete(integration)
        await db.commit()

    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(
                select(UserIntegration.id).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# A4 — break-glass deny / revoke refuse scoped credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_credential_cannot_deny_or_revoke_break_glass(ctx):
    from app.services.auth.elevations import deny_elevation, revoke_elevation

    elevation_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            AccessElevation(
                id=elevation_id,
                tenant_id=ctx.tenant_id,
                requested_by=ctx.owner.id,
                scope=ElevationScope.TENANT,
                status=ElevationStatus.PENDING,
                permissions=["audit:read"],
                reason="seam",
                duration_minutes=30,
                requested_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        await db.commit()

    scoped_approver = scoped(ctx.owner, ["break_glass:approve", "audit:read"])
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await deny_elevation(db, elevation_id, scoped_approver)
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await revoke_elevation(db, elevation_id, scoped_approver)

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(AccessElevation).where(AccessElevation.id == elevation_id))
        ).scalar_one()
        assert row.status == ElevationStatus.PENDING


# ---------------------------------------------------------------------------
# A5 — bridge AI routes require ai:use
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_ai_routes_refuse_principal_without_ai_use(
    ctx, monkeypatch: pytest.MonkeyPatch
):
    from app.extensions.builtin.health_assistant_bridge.provider import (
        HealthAssistantBridgeProvider,
    )
    from app.services.integration import integration_actor

    provider = HealthAssistantBridgeProvider()
    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()

    async def _read_only_actor(_db, _integration):
        return ctx.read_only

    monkeypatch.setattr(integration_actor, "resolve_integration_actor", _read_only_actor)

    with pytest.raises(AuthorizationError):
        await provider._ai_chat(integration, _bridge_request({"message": "hello"}))
    with pytest.raises(AuthorizationError):
        await provider._ai_transcribe(
            integration,
            _bridge_request({"data": base64.b64encode(b"audio").decode()}),
        )


# ---------------------------------------------------------------------------
# A7 — tenant restore: shared unit vocabulary + surfaced row failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_restore_never_writes_the_shared_unit_vocabulary(ctx):
    from app.schemas.biomarker import CatalogImportPayload, UnitCreate
    from app.services.catalog.catalog_import_service import CatalogImportService

    async with AsyncSessionLocal() as db:
        seeded = (await db.execute(select(Unit).limit(1))).scalar_one_or_none()
        if seeded is None:
            seeded = Unit(
                id=uuid.uuid4(),
                symbol=f"seam-unit-seed-{uuid.uuid4().hex[:6]}",
                name="Seam seeded unit",
                quantity_type="OTHER",
            )
            db.add(seeded)
            await db.commit()
        seeded_symbol, seeded_name = seeded.symbol, seeded.name

    unknown_symbol = f"seam-unit-{uuid.uuid4().hex[:6]}"
    payload = CatalogImportPayload(
        metadata=None,
        units=[
            UnitCreate(symbol=seeded_symbol, name="renamed by a tenant", quantity_type="OTHER"),
            UnitCreate(symbol=unknown_symbol, name="new", quantity_type="OTHER"),
        ],
        biomarkers=[],
    )
    async with AsyncSessionLocal() as db:
        result = await CatalogImportService(db, tenant_id=ctx.tenant_id).import_catalog(payload)
        await db.rollback()

    assert [outcome.reference for outcome in result.failures] == [unknown_symbol]
    assert "shared vocabulary" in (result.failures[0].error or "")
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(Unit.name).where(Unit.symbol == seeded_symbol))
        ).scalar_one() == seeded_name
        assert (
            await db.execute(select(Unit.id).where(Unit.symbol == unknown_symbol))
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_catalog_sidecar_restore_surfaces_row_failures(ctx, monkeypatch: pytest.MonkeyPatch):
    from app.services.imports.service import ImportService

    async def _boom(self, *args, **kwargs):
        raise RuntimeError("identity lookup exploded")

    monkeypatch.setattr(ImportService, "_find_catalog_identity", _boom)

    async with AsyncSessionLocal() as db:
        service = ImportService(db)
        for sidecar, payload, label in (
            ("medication_catalog.json", {"medications": [{"name": "Seam med"}]}, "medication"),
            ("allergy_catalog.json", {"allergies": [{"name": "Seam allergy"}]}, "allergy"),
        ):
            created, errors, _warnings = await service.restore_sidecar(
                sidecar, payload, ctx.tenant_id, {}
            )
            assert created
            assert any(label in error for error in errors), errors


@pytest.mark.asyncio
async def test_tenant_restore_skips_exported_system_catalog_rows(ctx):
    from app.services.imports.service import ImportService

    name = f"System Seam {uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        service = ImportService(db)
        count, errors = await service._restore_medication_catalog(
            {"medications": [{"name": name, "tenant_id": None}]}, ctx.tenant_id
        )
        await db.rollback()
    assert count == 0 and errors == []
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(Medication.id).where(Medication.patient_id == ctx.patient_id))
        ).first() is None
