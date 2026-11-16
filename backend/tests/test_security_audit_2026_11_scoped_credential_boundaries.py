"""Scoped-credential boundaries that outlive a single route gate (2026-11 audit).

The write-seam pass (``test_security_audit_2026_11_scoped_write_seams.py``)
closed the per-service permission seams. This file pins the remaining
boundaries — the places where a *read* is still a bulk egress, or where a
credential's owner role name leaks an operator path:

- B1 empty ingest batches short-circuit before actor resolution (nothing is
  written, so no gate runs) while every non-empty batch is gated;
- B2 clinical-event create/update/delete refuse a ``patient:read``-only
  principal;
- B3 examination create refuses the same principal;
- B4 the documents / appointments / labs ingest paths refuse it;
- B5 the bridge allergy/vaccine update+delete routes refuse it;
- F2 export applies the credential's patient reach (no ``system`` archive,
  a ``group`` list is bounded) instead of the owner's role name;
- F3 tenant reads keep a scoped ``SYSTEM_ADMIN`` inside its own tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.errors import AuthorizationError
from app.models.clinical_event import ClinicalEvent
from app.models.enums import Role
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.patient import Patient
from app.models.fhir.vaccine import PatientImmunization
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.schemas.backup import BackupRequest
from app.schemas.clinical_event import ClinicalEventCreate, ClinicalEventUpdate
from app.schemas.examination import ExaminationCreate
from app.schemas.user import TokenData
from app.services.clinical import clinical_event_service, examination_service
from tests._auth_helpers import create_tenant, create_user

READ_ONLY = ["patient:read"]
WRITER = ["patient:read", "clinical:write", "document:write"]


def scoped(
    owner: UserModel,
    permissions: list[str],
    *,
    scope: str = "all",
    patient_ids: list[uuid.UUID] | None = None,
) -> TokenData:
    """A PAT-shaped principal owned by ``owner`` with exactly ``permissions``."""
    return TokenData(
        user_id=owner.id,
        tenant_id=owner.tenant_id,
        role=owner.role,
        sub=str(owner.id),
        pat_id=str(uuid.uuid4()),
        pat_permissions=list(permissions),
        pat_patient_scope=scope,
        pat_patient_ids=[str(pid) for pid in patient_ids or []],
    )


@pytest_asyncio.fixture
async def ctx():
    """Tenant + ADMIN owner + one patient + a bridge integration bound to it."""
    tenant_id = await create_tenant("Scoped boundaries")
    owner = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                user_id=owner.id,
                name={"family": "Boundary", "given": ["Record"]},
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
                instance_name="boundaries",
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


def _bridge_request(payload: dict):
    request = SimpleNamespace(headers={}, query_params={})
    request.json = AsyncMock(return_value=payload)
    return request


# ---------------------------------------------------------------------------
# B1 — empty ingest batches write nothing and resolve no actor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_ingest_batches_short_circuit_before_the_actor_gate(
    ctx, monkeypatch: pytest.MonkeyPatch
):
    """An empty batch is not a write: no rows, no permission, no actor lookup.

    The gate still refuses every non-empty batch (A2 tests), so the short
    circuit cannot be used to reach a write path.
    """
    from app.services.integration import integration_actor
    from app.services.integration import integration_ingest as funnel

    resolved: list[uuid.UUID] = []

    async def _spy(_db, integration):
        resolved.append(integration.id)
        raise AssertionError("an empty batch must not resolve an ingest actor")

    monkeypatch.setattr(integration_actor, "resolve_integration_actor", _spy)

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()
        for report in (
            await funnel.ingest_observations(db, integration, []),
            await funnel.ingest_telemetry(db, integration, []),
            await funnel.ingest_documents(db, integration, [], actor=ctx.read_only),
            await funnel.ingest_labs(db, integration, [], actor=ctx.read_only),
        ):
            assert report.received == 0
            assert report.written == 0
    assert resolved == []


# ---------------------------------------------------------------------------
# B2 — clinical-event create / update / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_writes_refuse_read_only_principal(ctx):
    event_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            ClinicalEvent(
                id=event_id,
                tenant_id=ctx.tenant_id,
                patient_id=ctx.patient_id,
                title="Boundary event",
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.create_event(
                db,
                ctx.read_only,
                ClinicalEventCreate(patient_id=ctx.patient_id, title="read-only create"),
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.update_event(
                db, event_id, ctx.read_only, ClinicalEventUpdate(title="read-only update")
            )
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await clinical_event_service.soft_delete_event(db, event_id, ctx.read_only)

    # Fifteen minutes of nothing: the row is untouched and not tombstoned.
    async with AsyncSessionLocal() as db:
        event = (
            await db.execute(select(ClinicalEvent).where(ClinicalEvent.id == event_id))
        ).scalar_one()
        assert event.title == "Boundary event"
        assert event.deleted_at is None

    # The same seams accept a write-capable principal (the gate is not vacuous).
    async with AsyncSessionLocal() as db:
        updated = await clinical_event_service.update_event(
            db, event_id, ctx.writer, ClinicalEventUpdate(title="written")
        )
        assert updated["title"] == "written"
    async with AsyncSessionLocal() as db:
        await clinical_event_service.soft_delete_event(db, event_id, ctx.writer)


# ---------------------------------------------------------------------------
# B3 — examination create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_examination_create_refuses_read_only_principal(ctx):
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await examination_service.create_examination(
                db,
                ctx.read_only,
                ExaminationCreate(patient_id=ctx.patient_id, examination_date=date.today()),
            )
    async with AsyncSessionLocal() as db:
        created = await examination_service.create_examination(
            db,
            ctx.writer,
            ExaminationCreate(patient_id=ctx.patient_id, examination_date=date.today()),
        )
    assert created.patient_id == ctx.patient_id


# ---------------------------------------------------------------------------
# B4 — documents / appointments / labs ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_funnels_refuse_read_only_principal(ctx):
    from app.services.integration import integration_ingest as funnel

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()
        with pytest.raises(AuthorizationError):
            await funnel.ingest_documents(
                db,
                integration,
                [SimpleNamespace(content=b"x", filename="x.pdf", external_id="b4-doc")],
                actor=ctx.read_only,
            )
        with pytest.raises(AuthorizationError):
            await funnel.ingest_appointments(
                db,
                integration,
                examinations=[
                    ExaminationCreate(
                        patient_id=ctx.patient_id,
                        examination_date=date.today(),
                        external_id="b4-exam",
                    )
                ],
                actor=ctx.read_only,
            )
        with pytest.raises(AuthorizationError):
            await funnel.ingest_labs(
                db,
                integration,
                [{"code": {"text": "Boundary panel"}, "effective_datetime": datetime.now(UTC)}],
                actor=ctx.read_only,
            )

    # Appointments are gated even for an empty batch: the seam always runs.
    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == ctx.integration_id)
            )
        ).scalar_one()
        with pytest.raises(AuthorizationError):
            await funnel.ingest_appointments(db, integration, actor=ctx.read_only)


# ---------------------------------------------------------------------------
# B5 — bridge allergy / vaccine update + delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_allergy_and_vaccine_mutations_refuse_read_only_principal(
    ctx, monkeypatch: pytest.MonkeyPatch
):
    from app.extensions.builtin.health_assistant_bridge.provider import (
        HealthAssistantBridgeProvider,
    )
    from app.services.integration import integration_actor

    provider = HealthAssistantBridgeProvider()
    allergy_id = uuid.uuid4()
    vaccine_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            AllergyIntolerance(
                id=allergy_id,
                tenant_id=ctx.tenant_id,
                patient_id=ctx.patient_id,
                code={"text": "Boundary allergy"},
            )
        )
        db.add(
            PatientImmunization(
                id=vaccine_id,
                tenant_id=ctx.tenant_id,
                patient_id=ctx.patient_id,
                vaccine_code={"text": "Boundary vaccine"},
                administered_at=datetime.now(UTC),
            )
        )
        await db.commit()

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
        await provider._update_allergy(
            integration, str(allergy_id), _bridge_request({"note": "nope"})
        )
    with pytest.raises(AuthorizationError):
        await provider._delete_allergy(integration, str(allergy_id), _bridge_request({}))
    with pytest.raises(AuthorizationError):
        await provider._update_vaccine(
            integration, str(vaccine_id), _bridge_request({"note": "nope"})
        )
    with pytest.raises(AuthorizationError):
        await provider._delete_vaccine(integration, str(vaccine_id), _bridge_request({}))

    async with AsyncSessionLocal() as db:
        allergy = (
            await db.execute(select(AllergyIntolerance).where(AllergyIntolerance.id == allergy_id))
        ).scalar_one()
        vaccine = (
            await db.execute(
                select(PatientImmunization).where(PatientImmunization.id == vaccine_id)
            )
        ).scalar_one()
        assert allergy.note is None and allergy.deleted_at is None
        assert vaccine.note is None and vaccine.deleted_at is None

    # A write-capable actor takes the same routes.
    async def _writer_actor(_db, _integration):
        return ctx.writer

    monkeypatch.setattr(integration_actor, "resolve_integration_actor", _writer_actor)
    updated = await provider._update_allergy(
        integration, str(allergy_id), _bridge_request({"note": "written"})
    )
    assert updated["note"] == "written"
    assert (await provider._delete_allergy(integration, str(allergy_id), _bridge_request({})))[
        "deleted"
    ] is True
    updated = await provider._update_vaccine(
        integration, str(vaccine_id), _bridge_request({"note": "written"})
    )
    assert updated["note"] == "written"
    assert (await provider._delete_vaccine(integration, str(vaccine_id), _bridge_request({})))[
        "deleted"
    ] is True


# ---------------------------------------------------------------------------
# F2 — export applies the credential's patient reach
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _allow_step_up(monkeypatch: pytest.MonkeyPatch):
    from app.services.auth import step_up

    async def _allow(_principal, _action):
        return "password"

    monkeypatch.setattr(step_up, "enforce", _allow)


@pytest.fixture
def _no_task_export(monkeypatch: pytest.MonkeyPatch):
    from app.workers import tasks

    monkeypatch.setattr(tasks, "export_backup", SimpleNamespace(delay=lambda *a, **k: None))


@pytest.mark.asyncio
async def test_scoped_credential_cannot_export_the_tenant(ctx, _allow_step_up, _no_task_export):
    from app.api.v1.endpoints.export import create_export

    other_patient = uuid.uuid4()
    granted = scoped(
        ctx.owner,
        ["export:patient", "patient:read"],
        scope="granted",
        patient_ids=[ctx.patient_id],
    )
    none = scoped(ctx.owner, ["export:patient", "patient:read"], scope="none")

    async with AsyncSessionLocal() as db:
        for credential in (granted, none):
            with pytest.raises(HTTPException) as denied:
                await create_export(
                    BackupRequest(scope="system", export_type="full_backup"),
                    db=db,
                    current_user=credential,
                )
            assert denied.value.status_code == 403

        # A group list outside the reach is refused, not silently narrowed.
        with pytest.raises(HTTPException) as outside:
            await create_export(
                BackupRequest(
                    scope="group",
                    export_type="full_backup",
                    patient_ids=[str(ctx.patient_id), str(other_patient)],
                ),
                db=db,
                current_user=granted,
            )
        assert outside.value.status_code == 403

        # ``none`` reaches no patient at all — not even a one-patient group.
        with pytest.raises(HTTPException) as none_group:
            await create_export(
                BackupRequest(
                    scope="group",
                    export_type="full_backup",
                    patient_ids=[str(ctx.patient_id)],
                ),
                db=db,
                current_user=none,
            )
        assert none_group.value.status_code == 403
        # A patient-scoped call reaches the canonical access helper, whose
        # domain refusal is mapped to 403 by the global handler.
        with pytest.raises(AuthorizationError) as none_patient:
            await create_export(
                BackupRequest(
                    scope="patient",
                    export_type="fhir_only",
                    patient_ids=[str(ctx.patient_id)],
                ),
                db=db,
                current_user=none,
            )
        assert none_patient.value.status_code == 403

        # The allowed patient is exportable (the gate is not a blanket refusal).
        response = await create_export(
            BackupRequest(
                scope="patient",
                export_type="fhir_only",
                patient_ids=[str(ctx.patient_id)],
            ),
            db=db,
            current_user=granted,
        )
        assert response.patient_ids == [str(ctx.patient_id)]

    # No system/group job rows were left behind by the refused calls.
    from app.models.export_import_job import ExportJobModel

    async with AsyncSessionLocal() as db:
        scopes = {
            row.scope
            for row in (
                await db.execute(
                    select(ExportJobModel).where(ExportJobModel.tenant_id == ctx.tenant_id)
                )
            )
            .scalars()
            .all()
        }
    assert "system" not in scopes


@pytest.mark.asyncio
async def test_scoped_credential_job_access_is_bounded_by_its_patient_reach(ctx):
    from app.api.v1.endpoints.export import _authorize_job_access, list_export_jobs
    from app.models.enums import ExportScope, ExportType, JobStatus
    from app.models.export_import_job import ExportJobModel

    other_patient = uuid.uuid4()
    system_job = ExportJobModel(
        tenant_id=ctx.tenant_id,
        user_id=ctx.owner.id,
        scope=ExportScope.SYSTEM,
        export_type=ExportType.FULL_BACKUP,
        status=JobStatus.COMPLETED,
        progress=100,
    )
    outside_job = ExportJobModel(
        tenant_id=ctx.tenant_id,
        user_id=ctx.owner.id,
        scope=ExportScope.GROUP,
        export_type=ExportType.FULL_BACKUP,
        status=JobStatus.COMPLETED,
        progress=100,
        patient_ids=[str(other_patient)],
    )
    inside_job = ExportJobModel(
        tenant_id=ctx.tenant_id,
        user_id=ctx.owner.id,
        scope=ExportScope.PATIENT,
        export_type=ExportType.FHIR_ONLY,
        status=JobStatus.COMPLETED,
        progress=100,
        patient_ids=[str(ctx.patient_id)],
    )
    async with AsyncSessionLocal() as db:
        db.add_all([system_job, outside_job, inside_job])
        await db.commit()

    credential = scoped(
        ctx.owner,
        ["export:patient"],
        scope="granted",
        patient_ids=[ctx.patient_id],
    )
    async with AsyncSessionLocal() as db:
        for job in (system_job, outside_job):
            with pytest.raises(HTTPException) as hidden:
                await _authorize_job_access(job, credential, db)
            assert hidden.value.status_code == 404
        # The reach-covered job is readable/downloadable.
        await _authorize_job_access(inside_job, credential, db)
        listing = await list_export_jobs(limit=50, current_user=credential, db=db)
    assert [item.id for item in listing.items] == [str(inside_job.id)]

    # The unscoped session principal keeps its role gate on both surfaces.
    session = TokenData(
        user_id=ctx.owner.id,
        tenant_id=ctx.tenant_id,
        role=Role.ADMIN.value,
        sub=str(ctx.owner.id),
    )
    async with AsyncSessionLocal() as db:
        await _authorize_job_access(system_job, session, db)
        listing = await list_export_jobs(limit=50, current_user=session, db=db)
    assert {item.id for item in listing.items} == {
        str(system_job.id),
        str(outside_job.id),
        str(inside_job.id),
    }


# ---------------------------------------------------------------------------
# F3 — a scoped SYSTEM_ADMIN reads only its own tenant
# ---------------------------------------------------------------------------


def _scoped_system_admin(tenant_id) -> TokenData:
    owner_id = uuid.uuid4()
    return TokenData(
        user_id=owner_id,
        tenant_id=tenant_id,
        role=Role.SYSTEM_ADMIN.value,
        sub=str(owner_id),
        pat_id=str(uuid.uuid4()),
        pat_permissions=["tenant:read", "settings:read"],
        pat_patient_scope="all",
        pat_patient_ids=[],
    )


@pytest.mark.asyncio
async def test_scoped_system_admin_tenant_read_stays_in_its_tenant(ctx):
    from app.api.v1.endpoints.tenants import (
        get_tenant_endpoint,
        get_tenant_security_policy,
    )

    credential = _scoped_system_admin(ctx.tenant_id)
    foreign_tenant = await create_tenant("Foreign tenant")

    # Own tenant: readable (the credential is not denied wholesale).
    own = await get_tenant_endpoint(tenant_id=str(ctx.tenant_id), current_user=credential)
    assert own.id == ctx.tenant_id

    # Cross-tenant: the documented GET contract refuses (403, no body leak)...
    with pytest.raises(HTTPException) as denied:
        await get_tenant_endpoint(tenant_id=str(foreign_tenant), current_user=credential)
    assert denied.value.status_code == 403

    # ...and the policy surface hides the tenant's existence (404).
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as hidden:
            await get_tenant_security_policy(
                tenant_id=str(foreign_tenant), current_user=credential, db=db
            )
        assert hidden.value.status_code == 404

        own_policy = await get_tenant_security_policy(
            tenant_id=str(ctx.tenant_id), current_user=credential, db=db
        )
        assert own_policy.tenant_id == ctx.tenant_id

    # The unscoped operator keeps the cross-tenant read on both surfaces.
    operator = TokenData(
        user_id=ctx.owner.id,
        tenant_id=ctx.tenant_id,
        role=Role.SYSTEM_ADMIN.value,
        sub=str(ctx.owner.id),
    )
    foreign = await get_tenant_endpoint(tenant_id=str(foreign_tenant), current_user=operator)
    assert foreign.id == foreign_tenant
    async with AsyncSessionLocal() as db:
        policy = await get_tenant_security_policy(
            tenant_id=str(foreign_tenant), current_user=operator, db=db
        )
        assert policy.tenant_id == foreign_tenant
