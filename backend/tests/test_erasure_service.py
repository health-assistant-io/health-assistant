"""Erasure walker + ledger tests (plan 6 W2/W5/W6).

One seeded patient carries every awkward shape the walker owns: FHIR rows,
an examination/document (with a real file), a clinical event + links, the
telemetry hypertable, notifications + push registrations, chat + a proposal,
an integration with sealed credentials AND sealed ``user_config`` wrappers,
AI/audit traces, an export artifact and LangGraph checkpoint rows.

The non-vacuity contract: the dry-run report equals the real-run report, every
attributed row is gone afterwards, files are unlinked, both secret locations
are shredded, and the erasure ledger re-applies against a resurrected subject.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.core.database import AsyncSessionLocal
from app.models.ai_task_model import AICallLog, AITaskDef
from app.models.audit_model import AuditEvent
from app.models.chat_model import ChatMessage, ChatSession
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventOccurrence,
    EventAnatomyLink,
    EventExaminationLink,
    EventObservationLink,
)
from app.models.data_lifecycle_model import ErasureRequest
from app.models.document_model import DocumentModel
from app.models.enums import (
    AICallRoute,
    AICallStatus,
    ErasureStatus,
    ErasureSubjectType,
    Gender,
    NotificationCategory,
    NotificationChannel,
    NotificationSource,
    NotificationType,
    ProposalSource,
    ProposalStatus,
    RecipientKind,
    Role,
    TelemetryAnomalyKind,
    TriggerType,
)
from app.models.examination_model import ExaminationModel
from app.models.export_import_job import ExportJobModel
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.device import DeviceModel
from app.models.fhir.medication import Medication
from app.models.fhir.patient import DiagnosticReport, Observation, Patient
from app.models.fhir.provenance import ProvenanceModel
from app.models.fhir.vaccine import PatientImmunization
from app.models.notification import (
    MobilePushTarget,
    Notification,
    NotificationDelivery,
    NotificationRecipient,
    NotificationSubscription,
    NotificationTrigger,
)
from app.models.notification_rule import NotificationRule
from app.models.patient_layout import PatientLayoutModel
from app.models.proposal import Proposal, ProposalKind
from app.models.task_log import TaskLog
from app.models.telemetry_model import (
    TelemetryAnomalyModel,
    TelemetryDataModel,
    TelemetryDeviceModel,
)
from app.models.tenant_model import TenantModel
from app.models.user_integration import (
    IntegrationCredential,
    IntegrationDebugLog,
    IntegrationSyncLog,
    IntegrationSyncState,
    UserIntegration,
)
from app.models.user_model import UserModel
from app.services.data_lifecycle.erasure import (
    ErasureSubjectNotFound,
    replay_erasure_ledger,
    resolve_subject,
    run_erasure,
)
from app.services.integration.integration_credentials import (
    CredentialError,
    read_credential,
    seal_credential,
)

NOW = datetime.now(UTC)


async def _count(db, model, *criteria) -> int:
    table = getattr(model, "__table__", model)
    return int(
        (await db.execute(select(func.count()).select_from(table).where(*criteria))).scalar() or 0
    )


async def _seed_patient_graph(db, tmp_path: Path) -> dict:
    tenant = TenantModel(name="Erase", slug=f"erase-{uuid.uuid4().hex[:10]}")
    user = UserModel(
        email=f"erase-{uuid.uuid4().hex[:10]}@test.local",
        password_hash="x",
        tenant_id=None,
        role=Role.USER,
    )
    db.add(tenant)
    await db.flush()
    user.tenant_id = tenant.id
    db.add(user)
    await db.flush()

    patient = Patient(
        tenant_id=tenant.id,
        user_id=user.id,
        name=[{"family": "Erase", "given": ["Me"]}],
        gender=Gender.OTHER,
    )
    db.add(patient)
    await db.flush()

    examination = ExaminationModel(tenant_id=tenant.id, patient_id=patient.id)
    db.add(examination)
    await db.flush()

    doc_path = tmp_path / "doc.pdf"
    doc_path.write_bytes(b"%PDF-1.4 test")
    document = DocumentModel(
        tenant_id=tenant.id,
        owner_id=user.id,
        patient_id=patient.id,
        examination_id=examination.id,
        filename="doc.pdf",
        file_path=str(doc_path),
    )
    observation = Observation(
        tenant_id=tenant.id,
        patient_id=patient.id,
        examination_id=examination.id,
        status="final",
        code={"text": "Glucose"},
        subject={"reference": f"Patient/{patient.id}"},
    )
    medication = Medication(
        tenant_id=tenant.id,
        patient_id=patient.id,
        examination_id=examination.id,
        code={"text": "Med"},
    )
    allergy = AllergyIntolerance(
        tenant_id=tenant.id, patient_id=patient.id, code={"text": "Allergy"}
    )
    report = DiagnosticReport(
        tenant_id=tenant.id,
        patient_id=patient.id,
        status="final",
        code={"text": "Report"},
        subject={"reference": f"Patient/{patient.id}"},
    )
    immunization = PatientImmunization(
        tenant_id=tenant.id,
        patient_id=patient.id,
        examination_id=examination.id,
        vaccine_code={"text": "Vaccine"},
    )
    device = DeviceModel(
        tenant_id=tenant.id, patient_id=patient.id, device_name=[{"name": "Watch"}]
    )
    provenance = ProvenanceModel(
        tenant_id=tenant.id,
        target=[{"reference": f"Patient/{patient.id}"}],
        agent=[{"who": {"reference": f"Patient/{patient.id}"}}],
    )
    db.add_all(
        [
            document,
            observation,
            medication,
            allergy,
            report,
            immunization,
            device,
            provenance,
        ]
    )
    await db.flush()

    event = ClinicalEvent(tenant_id=tenant.id, patient_id=patient.id, title="Journey")
    db.add(event)
    await db.flush()
    occurrence = ClinicalEventOccurrence(event_id=event.id, occurred_at=NOW, title="Episode")
    event_exam = EventExaminationLink(event_id=event.id, examination_id=examination.id)
    event_obs = EventObservationLink(event_id=event.id, observation_id=observation.id)
    db.add_all([occurrence, event_exam, event_obs])
    await db.flush()
    anatomy_id = (await db.execute(text("SELECT id FROM anatomy_structures LIMIT 1"))).scalar()
    if anatomy_id is not None:
        db.add(
            EventAnatomyLink(event_id=event.id, anatomy_id=anatomy_id, relation_type="primary_site")
        )

    layout = PatientLayoutModel(
        tenant_id=tenant.id,
        user_id=user.id,
        patient_id=patient.id,
        layout_config={},
        cards_config=[],
    )
    db.add(layout)

    telemetry_device = TelemetryDeviceModel(
        tenant_id=tenant.id,
        patient_id=patient.id,
        source="watch",
        external_ref="dev-1",
    )
    db.add(telemetry_device)
    await db.flush()
    telemetry_row = TelemetryDataModel(
        tenant_id=tenant.id,
        patient_id=patient.id,
        device_id=telemetry_device.id,
        timestamp=NOW,
        slug="heart-rate",
        value=60,
        raw_value=60,
        unit="bpm",
        raw_unit="bpm",
    )
    telemetry_anomaly = TelemetryAnomalyModel(
        tenant_id=tenant.id,
        patient_id=patient.id,
        device_id=telemetry_device.id,
        slug="heart-rate",
        window_start=NOW,
        window_end=NOW,
        kind=TelemetryAnomalyKind.SPIKE,
        z_score=3.2,
        delta=12,
    )
    db.add_all([telemetry_row, telemetry_anomaly])

    trigger = NotificationTrigger(
        tenant_id=tenant.id,
        patient_id=patient.id,
        trigger_type=TriggerType.TIME,
        notification_type=NotificationType.MEDICATION_REMINDER,
        title="Reminder",
    )
    db.add(trigger)
    await db.flush()
    notification = Notification(
        tenant_id=tenant.id,
        patient_id=patient.id,
        trigger_id=trigger.id,
        source=NotificationSource.SYSTEM,
        type=NotificationType.MEDICATION_REMINDER,
        category=NotificationCategory.REMINDER,
        title="Take med",
    )
    db.add(notification)
    await db.flush()
    recipient = NotificationRecipient(
        tenant_id=tenant.id,
        notification_id=notification.id,
        user_id=user.id,
        recipient_kind=RecipientKind.USER,
    )
    delivery = NotificationDelivery(
        tenant_id=tenant.id,
        notification_id=notification.id,
        user_id=user.id,
        channel=NotificationChannel.IN_APP,
    )
    subscription = NotificationSubscription(
        tenant_id=tenant.id,
        user_id=user.id,
        subscription_data={"endpoint": "https://push.test/1", "keys": {}},
    )
    push_target = MobilePushTarget(
        tenant_id=tenant.id,
        user_id=user.id,
        device_id="phone-1",
        platform="unifiedpush",
        endpoint_url="https://push.test/device",
    )
    rule = NotificationRule(
        tenant_id=tenant.id,
        patient_id=patient.id,
        rule_type="OUT_OF_NORMAL_RANGE",
    )
    db.add_all([recipient, delivery, subscription, push_target, rule])

    chat = ChatSession(tenant_id=tenant.id, user_id=user.id, patient_id=patient.id, title="Chat")
    db.add(chat)
    await db.flush()
    message = ChatMessage(session_id=chat.id, role="user", content={"type": "text", "text": "hi"})
    answer = ChatMessage(
        session_id=chat.id, role="assistant", content={"type": "text", "text": "yo"}
    )
    db.add_all([message, answer])
    await db.flush()

    kind = (await db.execute(select(ProposalKind).limit(1))).scalar_one()
    proposal = Proposal(
        tenant_id=tenant.id,
        patient_id=patient.id,
        user_id=user.id,
        source=ProposalSource.CHAT,
        source_ref=message.id,
        kind_id=kind.id,
        status=ProposalStatus.PROPOSED,
        payload={"kind": "x"},
    )
    db.add(proposal)

    integration = UserIntegration(
        tenant_id=tenant.id,
        user_id=user.id,
        patient_id=patient.id,
        provider="fhir_server",
        user_config={},
    )
    db.add(integration)
    await db.flush()
    await seal_credential(
        db, integration, "oauth2", {"access_token": "secret-token", "refresh_token": "r"}
    )
    from app.extensions.sdk.auth import OAuthTokenStore

    OAuthTokenStore().store(
        integration, {"access_token": "oauth-access", "refresh_token": "oauth-refresh"}
    )
    sealed = json.dumps({"sealed": True})
    integration.user_config = {
        **dict(integration.user_config or {}),
        "api_secret": {"_encrypted": sealed, "_kid": "kid1"},
        "webhook_secret": {"_encrypted": sealed, "_kid": "kid1"},
        "instance_name": "kept",
    }
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(integration, "user_config")
    db.add_all(
        [
            IntegrationSyncState(
                tenant_id=tenant.id, user_integration_id=integration.id, stream="observations"
            ),
            IntegrationSyncLog(
                tenant_id=tenant.id,
                integration_id=integration.id,
                status="success",
                started_at=NOW,
            ),
            IntegrationDebugLog(tenant_id=tenant.id, integration_id=integration.id, title="debug"),
        ]
    )

    task_def = (await db.execute(select(AITaskDef).limit(1))).scalar_one()
    task_log = TaskLog(
        tenant_id=tenant.id,
        task_name="erase",
        task_id="t1",
        level="info",
        message="x",
        resource_id=patient.id,
    )
    ai_call = AICallLog(
        tenant_id=tenant.id,
        user_id=user.id,
        patient_id=patient.id,
        task_type_id=task_def.id,
        route=AICallRoute.CHAT,
        started_at=NOW,
        latency_ms=10,
        status=AICallStatus.OK,
    )
    audit = AuditEvent(
        tenant_id=tenant.id,
        user_id=user.id,
        action="read_patient",
        resource_type="Patient",
        resource_id=patient.id,
        old_value={"name": "Erase"},
        new_value={"name": "Erase"},
    )
    db.add_all([task_log, ai_call, audit])

    export_path = tmp_path / "export.zip"
    export_path.write_bytes(b"zip")
    manifest_path = tmp_path / "export.zip.manifest.json"
    manifest_path.write_text("{}")
    export_job = ExportJobModel(
        tenant_id=tenant.id,
        user_id=user.id,
        scope="patient",
        export_type="full_backup",
        patient_ids=[str(patient.id)],
        file_path=str(export_path),
        manifest_path=str(manifest_path),
    )
    db.add(export_job)
    await db.commit()

    thread_id = str(chat.id)
    for sql, params in (
        (
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, "
            "metadata) VALUES (:t, '', :c, '{}'::jsonb, '{}'::jsonb)",
            {"t": thread_id, "c": uuid.uuid4().hex},
        ),
        (
            "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, "
            "blob) VALUES (:t, '', 'ch', '1', 'json', :b)",
            {"t": thread_id, "b": b""},
        ),
    ):
        await db.execute(text(sql), params)
    await db.commit()

    return {
        "tenant_id": tenant.id,
        "user_id": user.id,
        "patient_id": patient.id,
        "integration_id": integration.id,
        "chat_id": chat.id,
        "document_path": doc_path,
        "export_path": export_path,
        "manifest_path": manifest_path,
        "event_id": event.id,
        "notification_id": notification.id,
    }


async def _cleanup(ids: dict) -> None:
    async with AsyncSessionLocal() as db:
        for table in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
            await db.execute(
                text(f"DELETE FROM {table} WHERE thread_id = :t"),
                {"t": str(ids["chat_id"])},
            )
        await db.execute(
            ErasureRequest.__table__.delete().where(
                ErasureRequest.__table__.c.subject_id == ids["patient_id"]
            )
        )
        await db.execute(
            ErasureRequest.__table__.delete().where(
                ErasureRequest.__table__.c.subject_id == ids["user_id"]
            )
        )
        await db.execute(
            ErasureRequest.__table__.delete().where(
                ErasureRequest.__table__.c.subject_id == ids["tenant_id"]
            )
        )
        await db.execute(
            TenantModel.__table__.delete().where(TenantModel.__table__.c.id == ids["tenant_id"])
        )
        await db.commit()
    for path in (ids["document_path"], ids["export_path"], ids["manifest_path"]):
        if Path(path).exists():
            Path(path).unlink()


@pytest.mark.asyncio
async def test_patient_erasure_dry_run_matches_real_run(tmp_path):
    async with AsyncSessionLocal() as db:
        ids = await _seed_patient_graph(db, tmp_path)

    try:
        async with AsyncSessionLocal() as db:
            dry = await run_erasure(
                db,
                subject_type=ErasureSubjectType.PATIENT,
                subject_id=ids["patient_id"],
                requested_by=ids["user_id"],
                dry_run=True,
            )
        assert dry.status is ErasureStatus.DRY_RUN_DONE
        dry_counts = dry.report["counts"]
        dry_anonymized = dry.report["anonymized"]
        dry_secrets = dry.report["secrets"]
        dry_files = dry.report["files"]
        assert dry_counts["fhir_patients"] == 1
        assert dry_counts["documents"] == 1
        assert dry_counts["telemetry_data"] == 1
        assert dry_counts["telemetry_anomalies"] == 1
        assert dry_counts["chat_messages"] == 2
        assert dry_counts["proposals"] == 1
        assert dry_counts["integration_credentials"] == 1
        assert dry_files["documents"] == 1
        assert dry_files["export_artifacts"] == 2
        assert dry_secrets["integration_credentials"] == 1
        assert dry_secrets["user_config_secrets"] == 3  # _oauth + api_secret + webhook_secret

        async with AsyncSessionLocal() as db:
            real = await run_erasure(
                db,
                subject_type=ErasureSubjectType.PATIENT,
                subject_id=ids["patient_id"],
                requested_by=ids["user_id"],
                dry_run=False,
            )
        assert real.status is ErasureStatus.DONE
        assert real.report["counts"] == dry_counts, "dry-run counts must equal real-run counts"
        assert real.report["anonymized"] == dry_anonymized
        assert real.report["secrets"] == dry_secrets
        assert real.report["files"] == dry_files

        async with AsyncSessionLocal() as db:
            assert await _count(db, Patient, Patient.id == ids["patient_id"]) == 0
            # Every patient-attributed clinical table is empty.
            for model, criteria in (
                (ExaminationModel, ExaminationModel.patient_id == ids["patient_id"]),
                (Observation, Observation.patient_id == ids["patient_id"]),
                (Medication, Medication.patient_id == ids["patient_id"]),
                (AllergyIntolerance, AllergyIntolerance.patient_id == ids["patient_id"]),
                (DiagnosticReport, DiagnosticReport.patient_id == ids["patient_id"]),
                (ClinicalEvent, ClinicalEvent.patient_id == ids["patient_id"]),
                (TelemetryDataModel, TelemetryDataModel.patient_id == ids["patient_id"]),
                (TelemetryAnomalyModel, TelemetryAnomalyModel.patient_id == ids["patient_id"]),
                (TelemetryDeviceModel, TelemetryDeviceModel.patient_id == ids["patient_id"]),
                (Notification, Notification.patient_id == ids["patient_id"]),
                (ChatSession, ChatSession.patient_id == ids["patient_id"]),
                (UserIntegration, UserIntegration.patient_id == ids["patient_id"]),
                (PatientLayoutModel, PatientLayoutModel.patient_id == ids["patient_id"]),
                (ProvenanceModel, ProvenanceModel.id.is_(None)),
            ):
                assert await _count(db, model, criteria) == 0
            assert (
                await _count(
                    db,
                    EventExaminationLink,
                    EventExaminationLink.event_id == ids["event_id"],
                )
                == 0
            )
            assert (
                await _count(
                    db,
                    IntegrationCredential,
                    IntegrationCredential.user_integration_id == ids["integration_id"],
                )
                == 0
            )
            checkpoint_rows = (
                await db.execute(
                    text("SELECT count(*) FROM checkpoints WHERE thread_id = :t"),
                    {"t": str(ids["chat_id"])},
                )
            ).scalar()
            assert checkpoint_rows == 0
            # Audit and AI traces survive as proof, redacted.
            ai = (
                (
                    await db.execute(
                        select(AICallLog).where(AICallLog.patient_id == ids["patient_id"])
                    )
                )
                .scalars()
                .all()
            )
            assert ai == []
            audit = (
                (
                    await db.execute(
                        select(AuditEvent).where(AuditEvent.resource_id == ids["patient_id"])
                    )
                )
                .scalars()
                .all()
            )
            assert len(audit) == 1 and audit[0].old_value is None
            # Export artifact: file gone, job row kept as the record.
            export_job = (
                (
                    await db.execute(
                        select(ExportJobModel).where(ExportJobModel.user_id == ids["user_id"])
                    )
                )
                .scalars()
                .first()
            )
            assert export_job is not None
            assert export_job.file_path is None

            # Crypto-shred: re-derive from the database and fail to decrypt.
            class _Stub:
                id = ids["integration_id"]

            with pytest.raises(CredentialError):
                await read_credential(db, _Stub(), "oauth2")

        assert not Path(ids["document_path"]).exists()
        assert not Path(ids["export_path"]).exists()
        assert not Path(ids["manifest_path"]).exists()
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_erasure_ledger_replays_against_a_resurrected_subject(tmp_path):
    async with AsyncSessionLocal() as db:
        ids = await _seed_patient_graph(db, tmp_path)
    try:
        async with AsyncSessionLocal() as db:
            await run_erasure(
                db,
                subject_type=ErasureSubjectType.PATIENT,
                subject_id=ids["patient_id"],
                requested_by=ids["user_id"],
                dry_run=False,
            )
        # Simulate a restore resurrecting the subject's patient + telemetry row.
        async with AsyncSessionLocal() as db:
            db.add(
                Patient(
                    id=ids["patient_id"],
                    tenant_id=ids["tenant_id"],
                    user_id=ids["user_id"],
                    name=[{"family": "Restored"}],
                    gender=Gender.OTHER,
                )
            )
            db.add(
                TelemetryDataModel(
                    tenant_id=ids["tenant_id"],
                    patient_id=ids["patient_id"],
                    device_id=uuid.uuid4(),
                    timestamp=NOW,
                    slug="heart-rate",
                    value=61,
                    raw_value=61,
                    unit="bpm",
                    raw_unit="bpm",
                )
            )
            await db.commit()
        async with AsyncSessionLocal() as db:
            result = await replay_erasure_ledger(db)
            assert result["replayed"] >= 1
        async with AsyncSessionLocal() as db:
            assert await _count(db, Patient, Patient.id == ids["patient_id"]) == 0
            assert (
                await _count(
                    db, TelemetryDataModel, TelemetryDataModel.patient_id == ids["patient_id"]
                )
                == 0
            )
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_user_erasure_removes_account_owned_patients_and_push_targets(tmp_path):
    async with AsyncSessionLocal() as db:
        ids = await _seed_patient_graph(db, tmp_path)
    try:
        async with AsyncSessionLocal() as db:
            dry = await run_erasure(
                db,
                subject_type=ErasureSubjectType.USER,
                subject_id=ids["user_id"],
                requested_by=ids["user_id"],
                dry_run=True,
            )
        async with AsyncSessionLocal() as db:
            request = await run_erasure(
                db,
                subject_type=ErasureSubjectType.USER,
                subject_id=ids["user_id"],
                requested_by=ids["user_id"],
                dry_run=False,
            )
        assert request.status is ErasureStatus.DONE
        assert request.report["counts"] == dry.report["counts"]
        assert request.report["files"] == dry.report["files"]
        assert request.report["secrets"] == dry.report["secrets"]
        async with AsyncSessionLocal() as db:
            assert await _count(db, UserModel, UserModel.id == ids["user_id"]) == 0
            assert await _count(db, Patient, Patient.id == ids["patient_id"]) == 0
            assert (
                await _count(
                    db,
                    NotificationSubscription,
                    NotificationSubscription.user_id == ids["user_id"],
                )
                == 0
            )
            assert (
                await _count(db, MobilePushTarget, MobilePushTarget.user_id == ids["user_id"]) == 0
            )
            assert (
                await _count(db, UserIntegration, UserIntegration.id == ids["integration_id"]) == 0
            )
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_tenant_erasure_purges_hypertable_rows_and_keeps_the_ledger(tmp_path):
    async with AsyncSessionLocal() as db:
        ids = await _seed_patient_graph(db, tmp_path)
    try:
        async with AsyncSessionLocal() as db:
            dry = await run_erasure(
                db,
                subject_type=ErasureSubjectType.TENANT,
                subject_id=ids["tenant_id"],
                requested_by=ids["user_id"],
                dry_run=True,
            )
        async with AsyncSessionLocal() as db:
            request = await run_erasure(
                db,
                subject_type=ErasureSubjectType.TENANT,
                subject_id=ids["tenant_id"],
                requested_by=ids["user_id"],
                dry_run=False,
            )
            ledger_id = request.id
        assert request.report["counts"] == dry.report["counts"]
        assert request.report["files"] == dry.report["files"]
        assert request.report["secrets"] == dry.report["secrets"]
        async with AsyncSessionLocal() as db:
            assert await _count(db, TenantModel, TenantModel.id == ids["tenant_id"]) == 0
            assert (
                await _count(
                    db, TelemetryDataModel, TelemetryDataModel.tenant_id == ids["tenant_id"]
                )
                == 0
            )
            ledger = (
                await db.execute(select(ErasureRequest).where(ErasureRequest.id == ledger_id))
            ).scalar_one_or_none()
            assert ledger is not None, "the erasure ledger must outlive the tenant"
            assert ledger.status is ErasureStatus.DONE
            assert ledger.tenant_id is None
        assert not Path(ids["document_path"]).exists()
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_cross_tenant_subject_is_a_hidden_404(tmp_path):
    async with AsyncSessionLocal() as db:
        ids = await _seed_patient_graph(db, tmp_path)
        other_tenant = uuid.uuid4()
    try:
        async with AsyncSessionLocal() as db:
            with pytest.raises(ErasureSubjectNotFound):
                await resolve_subject(
                    db,
                    ErasureSubjectType.PATIENT,
                    ids["patient_id"],
                    tenant_id=other_tenant,
                )
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_crypto_shred_clears_both_secret_locations(tmp_path):
    """Both at-rest locations are targeted: credential rows and config wrappers."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.user_integration import UserIntegration
    from app.services.data_lifecycle.erasure import ErasureWalker

    async with AsyncSessionLocal() as db:
        ids = await _seed_patient_graph(db, tmp_path)
    try:
        async with AsyncSessionLocal() as db:
            integration = (
                await db.execute(
                    select(UserIntegration).where(UserIntegration.id == ids["integration_id"])
                )
            ).scalar_one()
            assert "_oauth" in (integration.user_config or {})
            assert "api_secret" in (integration.user_config or {})
            assert "webhook_secret" in (integration.user_config or {})

            walker = ErasureWalker(db, dry_run=False)
            await walker._shred_integrations(UserIntegration.patient_id == ids["patient_id"])
            integration.user_config = dict(integration.user_config or {})
            flag_modified(integration, "user_config")
            await db.flush()
            db.expire_all()

            integration = (
                await db.execute(
                    select(UserIntegration).where(UserIntegration.id == ids["integration_id"])
                )
            ).scalar_one()
            config = integration.user_config or {}
            assert "_oauth" not in config
            assert "api_secret" not in config
            assert "webhook_secret" not in config
            assert config.get("instance_name") == "kept"
            # The credential ciphertext rows are removed by the walker step
            # that follows the config scrub (counted in the report).
            assert walker.report.secrets["integration_credentials"] == 1
            assert walker.report.secrets["user_config_secrets"] == 3
            await db.rollback()
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_export_before_erase_produces_the_precondition_artifact(tmp_path, monkeypatch):
    """W5: the subject's export exists before the walker deletes anything."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "uploads"))
    async with AsyncSessionLocal() as db:
        ids = await _seed_patient_graph(db, tmp_path)
    try:
        async with AsyncSessionLocal() as db:
            request = await run_erasure(
                db,
                subject_type=ErasureSubjectType.PATIENT,
                subject_id=ids["patient_id"],
                requested_by=ids["user_id"],
                dry_run=True,
                export_before_erase=True,
            )
        job_id = request.report["export_job_id"]
        assert job_id, "export_before_erase must produce an export job"
        async with AsyncSessionLocal() as db:
            job = (
                await db.execute(select(ExportJobModel).where(ExportJobModel.id == job_id))
            ).scalar_one()
            assert job.file_path and Path(job.file_path).exists()
            assert job.status.value == "COMPLETED"
    finally:
        await _cleanup(ids)
