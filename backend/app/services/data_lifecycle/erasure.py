"""Data-subject erasure: the ordered cascade walker and the erasure ledger.

One request, one ordered walk — never scattered per-table deletes. Children are
deleted before parents and the subject row is last, so a failure part-way
leaves an auditable ``failed`` request instead of a half-detached record
(deletes run in one transaction, so a crash rolls the whole walk back).

The walker owns the awkward shapes:

* **hypertable rows** — ``telemetry_data`` has no FK (Timescale limitation), so
  the walker deletes by ``patient_id``/``tenant_id`` explicitly;
* **runtime rows** — LangGraph checkpoint threads of the subject's chat
  sessions are deleted before the sessions;
* **files** — document uploads and export artifacts are unlinked before their
  rows disappear;
* **JSONB-held references** — ``fhir_provenance.target``/``entity`` references,
  ``export_jobs.patient_ids`` arrays, and chat-message references held by
  ``proposals.source_ref`` are matched and removed/scrubbed;
* **secrets** — ``integration_credentials.sealed_blob`` rows and the sealed
  ``user_config`` wrappers (``_oauth``, ``api_secret``, ``webhook_secret``) are
  crypto-shredded in both locations (plan 6 W6 / F12);
* **audit traces** — ``audit_events`` rows survive as proof of the action but
  their JSONB diff (potential PHI) is redacted (crypto-free anonymize).

The ``done`` rows are the erasure ledger: :func:`replay_erasure_ledger` is the
restore-time hook that re-applies their subject ids to a restored database
before it serves traffic (plan 6 §2.7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Text, cast, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.data_lifecycle import LifecycleClass
from app.core.errors import DomainError
from app.models.ai_task_model import AICallLog
from app.models.audit_model import AuditEvent
from app.models.base import Base
from app.models.chat_model import ChatMessage, ChatSession
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventOccurrence,
    EventAnatomyLink,
    EventExaminationLink,
    EventObservationLink,
)
from app.models.data_lifecycle_model import ErasureRequest
from app.models.doctor_model import DoctorModel
from app.models.document_model import DocumentModel
from app.models.enums import ErasureStatus, ErasureSubjectType, Role
from app.models.examination_model import ExaminationModel
from app.models.export_import_job import ExportJobModel, ImportJobModel
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.communication import CommunicationModel
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
from app.models.oauth import OAuthClient
from app.models.patient_layout import PatientLayoutModel
from app.models.proposal import Proposal
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

logger = logging.getLogger(__name__)

REPORT_SCHEMA_VERSION = 1

#: Scrub markers for the erasure ledger subject kinds.
SUBJECT_MODELS: dict[str, Any] = {
    ErasureSubjectType.PATIENT.value: Patient,
    ErasureSubjectType.USER.value: UserModel,
    ErasureSubjectType.TENANT.value: TenantModel,
}

#: Patient-owned tables in children-before-parents order. The subject row
#: (``fhir_patients``) is last by construction; a test walks the FK graph and
#: asserts no referenced table appears before its referencing table.
PATIENT_DELETE_ORDER: tuple[str, ...] = (
    "telemetry_data",
    "telemetry_anomalies",
    "telemetry_devices",
    "notification_deliveries",
    "notification_recipients",
    "notifications",
    "notification_triggers",
    "notification_rules",
    "proposals",
    "chat_messages",
    "chat_sessions",
    "task_logs",
    "event_observation_links",
    "event_examination_links",
    "event_anatomy_links",
    "clinical_event_occurrences",
    "fhir_communications",
    "fhir_medications",
    "fhir_observations",
    "fhir_diagnostic_reports",
    "fhir_allergy_intolerances",
    "fhir_devices",
    "patient_immunizations",
    "clinical_events",
    "documents",
    "examination_doctors",
    "examinations",
    "integration_credentials",
    "integration_sync_state",
    "integration_sync_logs",
    "integration_debug_logs",
    "user_integrations",
    "patient_layouts",
    "fhir_provenance",
    "fhir_patients",
)

#: Tables whose rows are *anonymized* (payload redacted, row kept) rather than
#: deleted. They are not part of the delete order's row-count contract.
ANONYMIZED_TABLES: tuple[str, ...] = ("ai_call_logs", "audit_events")

#: Tables the tenant walker never deletes: the ledger must outlive its subject.
TENANT_WALKER_SKIP: frozenset[str] = frozenset({"erasure_requests"})

#: ``user_config`` keys that hold sealed secret wrappers (plan 8 F12).
SEALED_CONFIG_KEYS: tuple[str, ...] = ("api_secret", "webhook_secret")


class ErasureError(DomainError):
    """Base typed erasure failure (carries a stable machine ``code``)."""

    code = "erasure_error"
    status_code = 500

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class ErasureSubjectNotFound(ErasureError):
    code = "erasure_subject_not_found"
    status_code = 404


class ErasureForbidden(ErasureError):
    code = "erasure_forbidden"
    status_code = 403


class ErasureSubjectUnsupported(ErasureError):
    code = "erasure_subject_unsupported"
    status_code = 400


@dataclass
class ErasureReport:
    """The impact report stored on an erasure request (``report`` JSONB)."""

    subject_type: str
    subject_id: str
    dry_run: bool
    counts: dict[str, int] = field(default_factory=dict)
    anonymized: dict[str, int] = field(default_factory=dict)
    secrets: dict[str, int] = field(default_factory=dict)
    files: dict[str, int] = field(default_factory=dict)
    cascade: list[str] = field(default_factory=list)
    export_job_id: str | None = None
    export_offer: dict[str, Any] = field(
        default_factory=lambda: {
            "available": True,
            "endpoint": "/api/v1/export",
            "scope": "patient",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "subject": {"type": self.subject_type, "id": self.subject_id},
            "dry_run": self.dry_run,
            "counts": dict(sorted(self.counts.items())),
            "anonymized": dict(sorted(self.anonymized.items())),
            "secrets": dict(sorted(self.secrets.items())),
            "files": dict(sorted(self.files.items())),
            "cascade": list(self.cascade),
            "export_job_id": self.export_job_id,
            "export_offer": self.export_offer,
        }

    def merge(self, other: ErasureReport) -> None:
        for bucket in ("counts", "anonymized", "secrets", "files"):
            target = getattr(self, bucket)
            for key, value in getattr(other, bucket).items():
                target[key] = target.get(key, 0) + value
        self.cascade.extend(name for name in other.cascade if name not in self.cascade)


def _table(model: Any):
    return getattr(model, "__table__", model)


async def _count(db: AsyncSession, model: Any, *criteria) -> int:
    table = _table(model)
    result = await db.execute(select(func.count()).select_from(table).where(*criteria))
    return int(result.scalar() or 0)


async def _delete(db: AsyncSession, model: Any, *criteria) -> int:
    table = _table(model)
    result = await db.execute(delete(table).where(*criteria))
    return int(result.rowcount or 0)


async def _anonymize(db: AsyncSession, model: Any, values: dict[str, Any], *criteria) -> int:
    table = _table(model)
    result = await db.execute(update(table).where(*criteria).values(**values))
    return int(result.rowcount or 0)


def _tenant_order() -> list[str]:
    """Every tenant-scoped table, children first (FK-depth order).

    Depth is ``1 + max(parent depth)`` across tenant-scoped FKs; self
    references are ignored. Sorting by descending depth (name as tiebreak)
    keeps children before parents without relying on metadata declaration
    order, which cycles (``users`` ↔ ``tenants.owner_id``) can perturb.
    """
    tables = {
        table.name: table
        for table in Base.metadata.tables.values()
        if "tenant_id" in table.columns and table.name not in TENANT_WALKER_SKIP
    }
    depth = dict.fromkeys(tables, 0)
    for _ in range(len(tables) + 1):
        changed = False
        for name, table in tables.items():
            parent_depth = 0
            for fk in table.foreign_keys:
                parent = fk.column.table.name
                if parent != name and parent in depth:
                    parent_depth = max(parent_depth, depth[parent] + 1)
            if parent_depth != depth[name]:
                depth[name] = parent_depth
                changed = True
        if not changed:
            break
    return sorted(tables, key=lambda name: (-depth[name], name))


def validate_patient_order(order: tuple[str, ...] = PATIENT_DELETE_ORDER) -> list[str]:
    """Return FK-order violations for a declared delete order.

    A table that references another table in the same walk must appear
    **before** its parent. Self-references are exempt (the row is one delete).
    """
    position = {name: index for index, name in enumerate(order)}
    violations: list[str] = []
    for name, index in position.items():
        table = Base.metadata.tables[name]
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if parent == name or parent not in position:
                continue
            if position[parent] < index:
                violations.append(
                    f"{name} (position {index}) references {parent} "
                    f"(position {position[parent]}) — children must come first"
                )
    return violations


class ErasureWalker:
    """The ordered cascade walker for one subject."""

    def __init__(self, db: AsyncSession, *, dry_run: bool):
        self.db = db
        self.dry_run = dry_run
        self.report = ErasureReport(subject_type="", subject_id="", dry_run=dry_run)

    async def run(self, subject_type: ErasureSubjectType | str, subject_id: UUID) -> ErasureReport:
        kind = ErasureSubjectType(subject_type)
        self.report.subject_type = kind.value
        self.report.subject_id = str(subject_id)
        if kind is ErasureSubjectType.PATIENT:
            await self._patient(subject_id)
        elif kind is ErasureSubjectType.USER:
            await self._user(subject_id)
        else:
            await self._tenant(subject_id)
        return self.report

    # -- counters -----------------------------------------------------------

    async def _step(self, name: str, model: Any, *criteria) -> int:
        """Count (dry) or delete (real) one table's subject rows."""
        if self.dry_run:
            n = await _count(self.db, model, *criteria)
        else:
            n = await _delete(self.db, model, *criteria)
        self.report.counts[name] = n
        if name not in self.report.cascade:
            self.report.cascade.append(name)
        return n

    async def _anonymize_step(self, name: str, model: Any, values: dict[str, Any], *criteria):
        if self.dry_run:
            n = await _count(self.db, model, *criteria)
        else:
            n = await _anonymize(self.db, model, values, *criteria)
        self.report.anonymized[name] = n
        if name not in self.report.cascade:
            self.report.cascade.append(name)
        return n

    # -- shared adapters ----------------------------------------------------

    async def _checkpoints(self, session_ids: list[UUID]) -> None:
        if not session_ids:
            return
        exists = (await self.db.execute(text("SELECT to_regclass('public.checkpoints')"))).scalar()
        if exists is None:
            return
        ids = [str(session_id) for session_id in session_ids]
        patterns = [f"{session_id}:%" for session_id in ids]
        total = 0
        for table_name in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
            count_sql = text(
                f"SELECT count(*) FROM {table_name} "
                "WHERE thread_id = ANY(:ids) OR thread_id LIKE ANY(:patterns)"
            )
            found = (await self.db.execute(count_sql, {"ids": ids, "patterns": patterns})).scalar()
            total += int(found or 0)
            if not self.dry_run:
                await self.db.execute(
                    text(
                        f"DELETE FROM {table_name} "
                        "WHERE thread_id = ANY(:ids) OR thread_id LIKE ANY(:patterns)"
                    ),
                    {"ids": ids, "patterns": patterns},
                )
        self.report.counts["checkpoints"] = total
        if "checkpoints" not in self.report.cascade:
            self.report.cascade.append("checkpoints")

    async def _shred_integrations(self, criteria) -> list[UUID]:
        """Destroy sealed key material in BOTH storage locations.

        ``integration_credentials.sealed_blob`` rows are deleted and every
        sealed wrapper in ``user_config`` (``_oauth`` token blob plus the
        platform ``api_secret``/``webhook_secret`` wrappers) is removed before
        the integration row itself is deleted.
        """
        result = await self.db.execute(select(UserIntegration).where(criteria))
        integrations = list(result.scalars().all())
        ids = [integration.id for integration in integrations]
        if not ids:
            self.report.secrets["integration_credentials"] = 0
            self.report.secrets["user_config_secrets"] = 0
            self.report.secrets["residual_token_columns"] = 0
            return []

        credentials = await self.db.execute(
            select(func.count())
            .select_from(IntegrationCredential)
            .where(IntegrationCredential.user_integration_id.in_(ids))
        )
        credential_count = int(credentials.scalar() or 0)

        sealed_fields = 0
        residual_tokens = 0
        for integration in integrations:
            config = dict(integration.user_config or {})
            removed = False
            for key in ("_oauth", *SEALED_CONFIG_KEYS):
                if config.get(key) not in (None, "", {}, []):
                    sealed_fields += 1
                    config.pop(key, None)
                    removed = True
            for key, value in list(config.items()):
                if isinstance(value, dict) and "_encrypted" in value:
                    sealed_fields += 1
                    config.pop(key, None)
                    removed = True
            if integration.access_token:
                residual_tokens += 1
            if integration.refresh_token:
                residual_tokens += 1
            if not self.dry_run:
                if removed:
                    integration.user_config = config
                    flag_modified(integration, "user_config")
                integration.access_token = None
                integration.refresh_token = None

        self.report.secrets["integration_credentials"] = credential_count
        self.report.secrets["user_config_secrets"] = sealed_fields
        self.report.secrets["residual_token_columns"] = residual_tokens
        if "crypto_shred" not in self.report.cascade:
            self.report.cascade.insert(0, "crypto_shred")
        return ids

    async def _document_files(self, criteria) -> None:
        result = await self.db.execute(select(DocumentModel.file_path).where(criteria))
        paths = [row[0] for row in result.all() if row[0]]
        existing = [path for path in paths if Path(path).exists()]
        self.report.files["documents"] = len(existing)
        if not self.dry_run:
            for path in existing:
                try:
                    Path(path).unlink()
                except OSError as exc:  # pragma: no cover - platform dependent
                    logger.warning("could not unlink erased document %s: %s", path, exc)

    async def _scrub_export_jobs(self, criteria) -> None:
        result = await self.db.execute(select(ExportJobModel).where(criteria))
        jobs = list(result.scalars().all())
        files = 0
        for job in jobs:
            for path in (job.file_path, job.manifest_path):
                if path and Path(path).exists():
                    files += 1
                    if not self.dry_run:
                        try:
                            Path(path).unlink()
                        except OSError as exc:  # pragma: no cover - platform dependent
                            logger.warning("could not unlink erased export %s: %s", path, exc)
            if not self.dry_run:
                job.file_path = None
                job.manifest_path = None
                job.patient_ids = []
        self.report.files["export_artifacts"] = files
        if jobs and "export_jobs_scrubbed" not in self.report.cascade:
            self.report.cascade.append("export_jobs_scrubbed")

    # -- patient walk -------------------------------------------------------

    async def _patient(self, patient_id: UUID) -> None:
        patient = (
            await self.db.execute(select(Patient).where(Patient.id == patient_id))
        ).scalar_one_or_none()
        if patient is None:
            return
        pid = patient.id

        session_ids = list(
            (await self.db.execute(select(ChatSession.id).where(ChatSession.patient_id == pid)))
            .scalars()
            .all()
        )
        examination_ids = select(ExaminationModel.id).where(ExaminationModel.patient_id == pid)
        event_ids = select(ClinicalEvent.id).where(ClinicalEvent.patient_id == pid)
        message_ids = select(ChatMessage.id).where(ChatMessage.session_id.in_(session_ids))

        await self._checkpoints(session_ids)

        await self._step("telemetry_data", TelemetryDataModel, TelemetryDataModel.patient_id == pid)
        await self._step(
            "telemetry_anomalies", TelemetryAnomalyModel, TelemetryAnomalyModel.patient_id == pid
        )
        await self._step(
            "telemetry_devices", TelemetryDeviceModel, TelemetryDeviceModel.patient_id == pid
        )
        notification_ids = select(Notification.id).where(Notification.patient_id == pid)
        await self._step(
            "notification_deliveries",
            NotificationDelivery,
            NotificationDelivery.notification_id.in_(notification_ids),
        )
        await self._step(
            "notification_recipients",
            NotificationRecipient,
            NotificationRecipient.notification_id.in_(notification_ids),
        )
        await self._step("notifications", Notification, Notification.patient_id == pid)
        await self._step(
            "notification_triggers", NotificationTrigger, NotificationTrigger.patient_id == pid
        )
        await self._step("notification_rules", NotificationRule, NotificationRule.patient_id == pid)
        await self._step(
            "proposals",
            Proposal,
            (Proposal.patient_id == pid) | (Proposal.source_ref.in_(message_ids)),
        )
        await self._step("chat_messages", ChatMessage, ChatMessage.session_id.in_(session_ids))
        await self._step("chat_sessions", ChatSession, ChatSession.patient_id == pid)
        await self._step("task_logs", TaskLog, TaskLog.resource_id == pid)
        await self._anonymize_step(
            "ai_call_logs", AICallLog, {"patient_id": None}, AICallLog.patient_id == pid
        )
        await self._anonymize_step(
            "audit_events",
            AuditEvent,
            {"old_value": None, "new_value": None},  # gate-allow: *, audit diff
            AuditEvent.resource_id == pid,
        )

        await self._document_files(
            (DocumentModel.patient_id == pid) | (DocumentModel.examination_id.in_(examination_ids))
        )
        await self._scrub_export_jobs(cast(ExportJobModel.patient_ids, Text).contains(str(pid)))

        await self._step(
            "event_observation_links",
            EventObservationLink,
            EventObservationLink.event_id.in_(event_ids),
        )
        await self._step(
            "event_examination_links",
            EventExaminationLink,
            EventExaminationLink.event_id.in_(event_ids),
        )
        await self._step(
            "event_anatomy_links",
            EventAnatomyLink,
            EventAnatomyLink.event_id.in_(event_ids),
        )
        await self._step(
            "clinical_event_occurrences",
            ClinicalEventOccurrence,
            ClinicalEventOccurrence.event_id.in_(event_ids),
        )
        await self._step(
            "fhir_communications",
            CommunicationModel,
            (CommunicationModel.subject_patient_id == pid)
            | (CommunicationModel.encounter_id.in_(examination_ids)),
        )
        await self._step(
            "fhir_medications",
            Medication,
            (Medication.patient_id == pid) | (Medication.examination_id.in_(examination_ids)),
        )
        await self._step(
            "fhir_observations",
            Observation,
            (Observation.patient_id == pid) | (Observation.examination_id.in_(examination_ids)),
        )
        await self._step(
            "fhir_diagnostic_reports",
            DiagnosticReport,
            DiagnosticReport.patient_id == pid,
        )
        await self._step(
            "fhir_allergy_intolerances",
            AllergyIntolerance,
            AllergyIntolerance.patient_id == pid,
        )
        await self._step("fhir_devices", DeviceModel, DeviceModel.patient_id == pid)
        await self._step(
            "patient_immunizations",
            PatientImmunization,
            (PatientImmunization.patient_id == pid)
            | (PatientImmunization.examination_id.in_(examination_ids)),
        )
        await self._step("clinical_events", ClinicalEvent, ClinicalEvent.patient_id == pid)
        await self._step(
            "documents",
            DocumentModel,
            (DocumentModel.patient_id == pid) | (DocumentModel.examination_id.in_(examination_ids)),
        )
        await self._step(
            "examination_doctors",
            Base.metadata.tables["examination_doctors"],
            Base.metadata.tables["examination_doctors"].c.examination_id.in_(examination_ids),
        )
        await self._step("examinations", ExaminationModel, ExaminationModel.patient_id == pid)
        integration_ids = list(
            (
                await self.db.execute(
                    select(UserIntegration.id).where(UserIntegration.patient_id == pid)
                )
            )
            .scalars()
            .all()
        )
        await self._shred_integrations(UserIntegration.patient_id == pid)
        if integration_ids:
            integration_filter = UserIntegration.id.in_(integration_ids)
        else:
            integration_filter = UserIntegration.id.is_(None)
        await self._step(
            "integration_credentials",
            IntegrationCredential,
            IntegrationCredential.user_integration_id.in_(integration_ids)
            if integration_ids
            else IntegrationCredential.user_integration_id.is_(None),
        )
        await self._step(
            "integration_sync_state",
            IntegrationSyncState,
            IntegrationSyncState.user_integration_id.in_(integration_ids)
            if integration_ids
            else IntegrationSyncState.user_integration_id.is_(None),
        )
        await self._step(
            "integration_sync_logs",
            IntegrationSyncLog,
            IntegrationSyncLog.integration_id.in_(integration_ids)
            if integration_ids
            else IntegrationSyncLog.integration_id.is_(None),
        )
        await self._step(
            "integration_debug_logs",
            IntegrationDebugLog,
            IntegrationDebugLog.integration_id.in_(integration_ids)
            if integration_ids
            else IntegrationDebugLog.integration_id.is_(None),
        )
        await self._step(
            "user_integrations",
            UserIntegration,
            integration_filter if integration_ids else UserIntegration.patient_id == pid,
        )

        await self._step(
            "patient_layouts", PatientLayoutModel, PatientLayoutModel.patient_id == pid
        )
        await self._step(
            "fhir_provenance",
            ProvenanceModel,
            cast(ProvenanceModel.target, Text).contains(str(pid))
            | cast(ProvenanceModel.entity, Text).contains(str(pid)),
        )
        await self._step("fhir_patients", Patient, Patient.id == pid)

    # -- user walk ----------------------------------------------------------

    async def _user(self, user_id: UUID) -> None:
        user = (
            await self.db.execute(select(UserModel).where(UserModel.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            return
        uid = user.id

        owned = list(
            (await self.db.execute(select(Patient.id).where(Patient.user_id == uid)))
            .scalars()
            .all()
        )
        for patient_id in owned:
            child = ErasureWalker(self.db, dry_run=self.dry_run)
            await child._patient(patient_id)
            self.report.merge(child.report)

        remaining_sessions = select(ChatSession.id).where(
            ChatSession.user_id == uid,
            ChatSession.patient_id.is_(None) | ChatSession.patient_id.notin_(owned),
        )
        session_ids = list((await self.db.execute(remaining_sessions)).scalars().all())
        message_ids = select(ChatMessage.id).where(ChatMessage.session_id.in_(session_ids))
        remaining_notifications = select(Notification.id).where(
            Notification.patient_id.is_(None) | Notification.patient_id.notin_(owned)
        )
        await self._checkpoints(session_ids)

        owned_exams = select(ExaminationModel.id).where(ExaminationModel.patient_id.in_(owned))
        remaining_docs = (
            (DocumentModel.owner_id == uid)
            & (DocumentModel.patient_id.is_(None) | DocumentModel.patient_id.notin_(owned))
            & (
                DocumentModel.examination_id.is_(None)
                | DocumentModel.examination_id.notin_(owned_exams)
            )
        )
        await self._document_files(remaining_docs)
        user_export_scope = ExportJobModel.user_id == uid
        for owned_patient_id in owned:
            user_export_scope = user_export_scope & ~cast(
                ExportJobModel.patient_ids, Text
            ).contains(str(owned_patient_id))
        await self._scrub_export_jobs(user_export_scope)
        await self._step("documents", DocumentModel, remaining_docs)
        await self._step("export_jobs", ExportJobModel, ExportJobModel.user_id == uid)
        await self._step("import_jobs", ImportJobModel, ImportJobModel.user_id == uid)
        await self._step(
            "auth_sessions",
            Base.metadata.tables["auth_sessions"],
            Base.metadata.tables["auth_sessions"].c.user_id == uid,
        )
        await self._step(
            "notification_deliveries",
            NotificationDelivery,
            (NotificationDelivery.user_id == uid)
            & NotificationDelivery.notification_id.in_(remaining_notifications),
        )
        await self._step(
            "notification_recipients",
            NotificationRecipient,
            (NotificationRecipient.user_id == uid)
            & NotificationRecipient.notification_id.in_(remaining_notifications),
        )
        await self._step(
            "notification_subscriptions",
            NotificationSubscription,
            NotificationSubscription.user_id == uid,
        )
        await self._step("mobile_push_targets", MobilePushTarget, MobilePushTarget.user_id == uid)
        await self._step(
            "proposals",
            Proposal,
            (
                (Proposal.user_id == uid)
                & (Proposal.patient_id.is_(None) | Proposal.patient_id.notin_(owned))
            )
            | (Proposal.source_ref.in_(message_ids)),
        )
        await self._step("chat_messages", ChatMessage, ChatMessage.session_id.in_(session_ids))
        await self._step(
            "chat_sessions",
            ChatSession,
            (ChatSession.user_id == uid)
            & (ChatSession.patient_id.is_(None) | ChatSession.patient_id.notin_(owned)),
        )
        await self._step(
            "patient_layouts",
            PatientLayoutModel,
            (PatientLayoutModel.user_id == uid) & PatientLayoutModel.patient_id.notin_(owned),
        )
        await self._anonymize_step(
            "doctors", DoctorModel, {"user_id": None}, DoctorModel.user_id == uid
        )
        await self._anonymize_step(
            "oauth_clients",
            OAuthClient,
            {"created_by_user_id": None},
            OAuthClient.created_by_user_id == uid,
        )
        await self._anonymize_step(
            "ai_call_logs", AICallLog, {"user_id": None}, AICallLog.user_id == uid
        )
        await self._step("task_logs", TaskLog, TaskLog.resource_id == uid)
        await self._anonymize_step(
            "audit_events",
            AuditEvent,
            {"old_value": None, "new_value": None, "user_id": None},  # gate-allow: *, audit diff
            (AuditEvent.user_id == uid) | (AuditEvent.resource_id == uid),
        )

        remaining_integrations = (UserIntegration.user_id == uid) & (
            UserIntegration.patient_id.notin_(owned)
        )
        integration_ids = list(
            (await self.db.execute(select(UserIntegration.id).where(remaining_integrations)))
            .scalars()
            .all()
        )
        await self._shred_integrations(remaining_integrations)
        if integration_ids:
            await self._step(
                "integration_credentials",
                IntegrationCredential,
                IntegrationCredential.user_integration_id.in_(integration_ids),
            )
            await self._step(
                "integration_sync_state",
                IntegrationSyncState,
                IntegrationSyncState.user_integration_id.in_(integration_ids),
            )
            await self._step(
                "integration_sync_logs",
                IntegrationSyncLog,
                IntegrationSyncLog.integration_id.in_(integration_ids),
            )
            await self._step(
                "integration_debug_logs",
                IntegrationDebugLog,
                IntegrationDebugLog.integration_id.in_(integration_ids),
            )
        await self._step("user_integrations", UserIntegration, remaining_integrations)
        await self._step("users", UserModel, UserModel.id == uid)

    # -- tenant walk --------------------------------------------------------

    async def _tenant(self, tenant_id: UUID) -> None:
        tenant = (
            await self.db.execute(select(TenantModel).where(TenantModel.id == tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            return
        tid = tenant.id

        session_ids = list(
            (await self.db.execute(select(ChatSession.id).where(ChatSession.tenant_id == tid)))
            .scalars()
            .all()
        )
        await self._checkpoints(session_ids)
        await self._document_files(DocumentModel.tenant_id == tid)
        await self._scrub_export_jobs(ExportJobModel.tenant_id == tid)
        await self._shred_integrations(UserIntegration.tenant_id == tid)

        for name in _tenant_order():
            await self._step(
                name, Base.metadata.tables[name], Base.metadata.tables[name].c.tenant_id == tid
            )
        await self._step("tenants", TenantModel, TenantModel.id == tid)


async def replay_erasure_ledger(db: AsyncSession, *, limit: int = 500) -> dict[str, Any]:
    """Re-apply every ``done`` erasure to a restored database.

    Restore procedure: run this before serving traffic (it is also exposed as
    a maintenance script). Rows whose subject is absent are no-ops, so a
    replay is idempotent; a resurrected subject is erased again.
    """
    result = await db.execute(
        select(ErasureRequest)
        .where(ErasureRequest.status == ErasureStatus.DONE)
        .order_by(ErasureRequest.finished_at.asc())
        .limit(limit)
    )
    replayed = 0
    details: list[dict[str, Any]] = []
    for request in result.scalars().all():
        walker = ErasureWalker(db, dry_run=False)
        report = await walker.run(ErasureSubjectType(request.subject_type), request.subject_id)
        if any(report.counts.values()):
            replayed += 1
        details.append(
            {
                "request_id": str(request.id),
                "subject_type": request.subject_type.value,
                "subject_id": str(request.subject_id),
                "counts": report.counts,
            }
        )
    await db.commit()
    return {"replayed": replayed, "requests": details}


async def run_erasure(
    db: AsyncSession,
    *,
    subject_type: ErasureSubjectType | str,
    subject_id: UUID,
    requested_by: UUID | None,
    dry_run: bool,
    export_before_erase: bool = False,
) -> ErasureRequest:
    """Create the request row, walk the cascade, and persist the report.

    The request row commits before the walk (``queued`` → ``running``) so a
    crash leaves an auditable failed partial; the walk itself is one
    transaction — a mid-walk failure rolls every delete back.
    """
    kind = ErasureSubjectType(subject_type)
    request = ErasureRequest(
        tenant_id=None,
        subject_type=kind,
        subject_id=subject_id,
        requested_by=requested_by,
        status=ErasureStatus.QUEUED,
        dry_run=dry_run,
        report={},
        report_schema_version=REPORT_SCHEMA_VERSION,
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)
    request_row_id = request.id

    tenant_id = None
    subject_model = SUBJECT_MODELS[kind.value]
    subject = (
        await db.execute(select(subject_model).where(subject_model.id == subject_id))
    ).scalar_one_or_none()
    if subject is not None:
        tenant_id = getattr(subject, "tenant_id", None) or getattr(subject, "id", None)
    request.tenant_id = tenant_id
    request.status = ErasureStatus.RUNNING
    request.started_at = datetime.now(UTC)
    await db.commit()

    try:
        export_job_id = None
        if export_before_erase:
            export_job_id = await _run_erasure_export(
                db, kind, subject_id, requested_by=requested_by
            )
        walker = ErasureWalker(db, dry_run=dry_run)
        report = await walker.run(kind, subject_id)
        report.export_job_id = export_job_id
        report.export_offer["scope"] = {
            ErasureSubjectType.PATIENT: "patient",
            ErasureSubjectType.USER: "group",
            ErasureSubjectType.TENANT: "system",
        }[kind]
        request.report = report.to_dict()
        request.status = ErasureStatus.DRY_RUN_DONE if dry_run else ErasureStatus.DONE
        request.finished_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(request)
        return request
    except Exception as exc:
        await db.rollback()
        request = (
            await db.execute(select(ErasureRequest).where(ErasureRequest.id == request_row_id))
        ).scalar_one()
        request.status = ErasureStatus.FAILED
        request.finished_at = datetime.now(UTC)
        request.report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "subject": {"type": kind.value, "id": str(subject_id)},
            "dry_run": dry_run,
            "error": {"code": "erasure_failed", "message": str(exc)[:500]},
        }
        await db.commit()
        raise


async def _run_erasure_export(
    db: AsyncSession,
    kind: ErasureSubjectType,
    subject_id: UUID,
    *,
    requested_by: UUID | None,
) -> str | None:
    """W5: produce the subject's export *before* the walk deletes it."""
    from app.models.enums import ExportScope, ExportType
    from app.services.export.export_service import ExportService

    subject = (
        await db.execute(
            select(SUBJECT_MODELS[kind.value]).where(SUBJECT_MODELS[kind.value].id == subject_id)
        )
    ).scalar_one_or_none()
    if subject is None or requested_by is None:
        return None
    tenant_id = getattr(subject, "tenant_id", None) or subject.id
    if kind is ErasureSubjectType.PATIENT:
        scope, patient_ids = ExportScope.PATIENT, [str(subject_id)]
    elif kind is ErasureSubjectType.USER:
        patient_ids = [
            str(pid)
            for pid in (await db.execute(select(Patient.id).where(Patient.user_id == subject_id)))
            .scalars()
            .all()
        ]
        scope = ExportScope.GROUP if len(patient_ids) > 1 else ExportScope.PATIENT
        if not patient_ids:
            return None
    else:
        scope, patient_ids = ExportScope.SYSTEM, None

    service = ExportService(db)
    job = await service.create_job(
        user_id=requested_by,
        tenant_id=tenant_id,
        scope=scope,
        export_type=ExportType.FULL_BACKUP,
        patient_ids=patient_ids,
    )
    await service.run_export(job.id)
    return str(job.id)


def assert_can_erase(current_user: Any, kind: ErasureSubjectType, subject: Any) -> None:
    """RBAC gate: SYSTEM_ADMIN all; ADMIN own-tenant user/patient; USER self."""
    role = getattr(current_user, "role", None)
    user_id = str(getattr(current_user, "user_id", "") or "")
    if role == Role.SYSTEM_ADMIN.value:
        return
    if kind is ErasureSubjectType.TENANT:
        raise ErasureForbidden("Only a SYSTEM_ADMIN can erase a tenant.")
    subject_tenant = str(getattr(subject, "tenant_id", "") or "")
    token_tenant = str(getattr(current_user, "tenant_id", "") or "")
    if subject_tenant != token_tenant:
        raise ErasureSubjectNotFound("Erasure subject not found")
    if role == Role.ADMIN.value:
        return
    if role == Role.USER.value:
        if kind is ErasureSubjectType.USER and str(subject.id) == user_id:
            return
        if kind is ErasureSubjectType.PATIENT and str(getattr(subject, "user_id", "")) == user_id:
            return
    raise ErasureForbidden("Access denied")


async def resolve_subject(
    db: AsyncSession,
    kind: ErasureSubjectType | str,
    subject_id: UUID,
    *,
    tenant_id: UUID | None,
) -> Any:
    """Load and tenant-scope the subject (cross-tenant is a hidden 404)."""
    try:
        wanted = ErasureSubjectType(kind)
    except ValueError as exc:
        raise ErasureSubjectUnsupported(f"Unknown subject type {kind!r}") from exc
    model = SUBJECT_MODELS[wanted.value]
    subject = (await db.execute(select(model).where(model.id == subject_id))).scalar_one_or_none()
    if subject is None:
        raise ErasureSubjectNotFound("Erasure subject not found")
    if wanted is ErasureSubjectType.TENANT:
        if tenant_id is not None and str(subject.id) != str(tenant_id):
            raise ErasureSubjectNotFound("Erasure subject not found")
    elif tenant_id is not None and str(subject.tenant_id) != str(tenant_id):
        raise ErasureSubjectNotFound("Erasure subject not found")
    return subject


def lifecycle_of(table: str) -> LifecycleClass:
    """The lifecycle class a walked table belongs to (report consumer)."""
    from app.core.data_lifecycle import class_for

    return class_for(table)
