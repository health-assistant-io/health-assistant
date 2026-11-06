"""The one inbound funnel for connector data (plan 8 W2).

Every connector — bundled or later first-wave — writes through this module.
It owns the four typed ingest paths and their invariants:

**observations** — payloads are validated, biomarker-mapped, deduped and then
routed through the canonical FHIR/telemetry split. A value that cannot be
mapped to a catalog biomarker is **kept as a raw observation** with the gap
recorded in the report (never dropped silently).

**telemetry** — raw readings go through the S6 seam: slug registry
(``resolve_telemetry_definition``), device registration
(``resolve_telemetry_device``) and canonical-unit conversion
(``build_telemetry_row``). An unknown/non-telemetry slug is a typed rejection.

**documents** — bytes go through ``document_service.ingest_document_bytes``,
the same path the UI upload uses, so OCR rides the AI gateway (the S1 rule:
no direct model construction anywhere).

**appointments** — the clinical-event/calendar surface (examinations and
longitudinal clinical events), written through their canonical services.

Dedup-key standard (the 2026-07 ``integration_dedup`` convention): every write
carries a stable source identity. Documents, examinations and clinical events
use ``source_integration_id`` + ``external_id`` against their partial unique
indexes; observations and telemetry rows — which carry no source-id columns —
are deduped on their natural key (patient, timestamp, metric, value /
device) before insert. A re-sync with the same cursor therefore writes zero
duplicates, and the report says so (``deduped``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import IntegrationSyncStream
from app.models.fhir import Observation
from app.models.telemetry_model import TelemetryDataModel
from app.services.telemetry.telemetry_ingest import (
    UnknownTelemetrySlugError,
    build_telemetry_row,
    resolve_telemetry_definition,
    resolve_telemetry_device,
)

logger = logging.getLogger(__name__)

INTEGRATION_MAX_DOCS_PER_SYNC = 20
INTEGRATION_MAX_DOC_BYTES_PER_SYNC = 50 * 1024 * 1024  # 50 MiB


@dataclass(frozen=True)
class MappingGap:
    """One observation that stayed raw because no catalog mapping applied."""

    code: str | None
    display: str | None
    patient_id: str | None


@dataclass
class TelemetryReading:
    """One raw telemetry sample from a connector.

    ``device_external_ref`` is the source's opaque device id (registered in
    ``telemetry_devices``); ``unit`` is whatever the source sent — the seam
    stores both canonical and raw pairs.
    """

    slug: str
    value: float
    timestamp: datetime
    unit: str | None = None
    patient_id: UUID | None = None
    device_external_ref: str | None = None


@dataclass
class IngestReport:
    """Outcome of one funnel call: what was written, deduped, rejected, gap'd."""

    received: int = 0
    written: int = 0
    deduped: int = 0
    rejected: int = 0
    mapping_gaps: list[MappingGap] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    written_by_stream: dict[str, int] = field(default_factory=dict)
    written_by_kind: dict[str, int] = field(default_factory=dict)

    def record_kind(self, kind: str, count: int = 1) -> None:
        if count:
            self.written_by_kind[kind] = self.written_by_kind.get(kind, 0) + count

    def record_stream(self, stream: IntegrationSyncStream, count: int) -> None:
        if count:
            key = stream.value
            self.written_by_stream[key] = self.written_by_stream.get(key, 0) + count

    def merge(self, other: IngestReport) -> None:
        self.received += other.received
        self.written += other.written
        self.deduped += other.deduped
        self.rejected += other.rejected
        self.mapping_gaps.extend(other.mapping_gaps)
        self.rejections.extend(other.rejections)
        for key, value in other.written_by_stream.items():
            self.written_by_stream[key] = self.written_by_stream.get(key, 0) + value
        for key, value in other.written_by_kind.items():
            self.written_by_kind[key] = self.written_by_kind.get(key, 0) + value

    @property
    def has_mapping_gaps(self) -> bool:
        return bool(self.mapping_gaps)

    @property
    def status(self) -> str:
        """``partial`` when anything was deduped/rejected/gap'd, else success."""
        if self.rejected or self.has_mapping_gaps:
            return "partial"
        return "success"

    def summary(self) -> str:
        parts = [f"received={self.received}", f"written={self.written}"]
        if self.deduped:
            parts.append(f"deduped={self.deduped}")
        if self.rejected:
            parts.append(f"rejected={self.rejected}")
        if self.mapping_gaps:
            parts.append(f"mapping_gaps={len(self.mapping_gaps)}")
        return " ".join(parts)


def observation_from_payload(payload: Any) -> Observation:
    """Convert an SDK ``ObservationCreate``/dict into an ORM observation."""
    if isinstance(payload, Observation):
        return payload
    obs_dict = (
        payload.model_dump(exclude_unset=True)
        if hasattr(payload, "model_dump")
        else payload.dict(exclude_unset=True)
        if hasattr(payload, "dict")
        else dict(payload)
    )
    return Observation(**obs_dict)


def _natural_key(
    *,
    patient_id: Any,
    effective_datetime: Any,
    code: Any,
    normalized_value: Any,
    raw_value: Any,
    value_string: Any,
    value_quantity: Any,
) -> tuple:
    effective_key = (
        effective_datetime.isoformat()
        if isinstance(effective_datetime, datetime)
        else str(effective_datetime)
    )
    value = normalized_value
    if value is None:
        value = raw_value
    if value is None:
        value = value_string
    if value is None and isinstance(value_quantity, dict):
        value = value_quantity.get("value")
    return (
        str(patient_id),
        effective_key,
        json.dumps(code, sort_keys=True, default=str) if code else None,
        str(value) if value is not None else None,
    )


def observation_natural_key(obs: Observation) -> tuple:
    """The dedup key for an observation (no source-id columns exist)."""
    return _natural_key(
        patient_id=getattr(obs, "patient_id", None),
        effective_datetime=getattr(obs, "effective_datetime", None),
        code=getattr(obs, "code", None),
        normalized_value=getattr(obs, "normalized_value", None),
        raw_value=getattr(obs, "raw_value", None),
        value_string=getattr(obs, "value_string", None),
        value_quantity=getattr(obs, "value_quantity", None),
    )


def _observation_code_label(obs: Observation) -> tuple[str | None, str | None]:
    code = getattr(obs, "code", None) or {}
    if not isinstance(code, dict):
        return None, None
    codings = code.get("coding") or []
    remote_code = codings[0].get("code") if codings and isinstance(codings[0], dict) else None
    return remote_code, code.get("text")


async def _existing_observation_keys(
    db: AsyncSession, integration: Any, observations: list[Observation]
) -> set[tuple]:
    """Load natural keys of already-stored observations in the batch window."""
    patient_ids = {obs.patient_id for obs in observations if obs.patient_id}
    times = [obs.effective_datetime for obs in observations if obs.effective_datetime]
    if not patient_ids or not times:
        return set()
    result = await db.execute(
        select(
            Observation.patient_id,
            Observation.effective_datetime,
            Observation.code,
            Observation.normalized_value,
            Observation.raw_value,
            Observation.value_string,
            Observation.value_quantity,
        ).where(
            Observation.tenant_id == integration.tenant_id,
            Observation.patient_id.in_(patient_ids),
            Observation.effective_datetime >= min(times),
            Observation.effective_datetime <= max(times),
        )
    )
    keys: set[tuple] = set()
    for (
        patient_id,
        effective_datetime,
        code,
        normalized_value,
        raw_value,
        value_string,
        value_quantity,
    ) in result.all():
        keys.add(
            _natural_key(
                patient_id=patient_id,
                effective_datetime=effective_datetime,
                code=code,
                normalized_value=normalized_value,
                raw_value=raw_value,
                value_string=value_string,
                value_quantity=value_quantity,
            )
        )
    return keys


async def _existing_telemetry_keys(
    db: AsyncSession, integration: Any, rows: list[dict[str, Any]]
) -> set[tuple]:
    """Load natural keys of already-stored telemetry rows in the batch window."""
    timestamps = [row["timestamp"] for row in rows if row.get("timestamp")]
    slugs = {row["slug"] for row in rows if row.get("slug")}
    if not timestamps or not slugs:
        return set()
    result = await db.execute(
        select(
            TelemetryDataModel.patient_id,
            TelemetryDataModel.device_id,
            TelemetryDataModel.slug,
            TelemetryDataModel.timestamp,
        ).where(
            TelemetryDataModel.tenant_id == integration.tenant_id,
            TelemetryDataModel.slug.in_(slugs),
            TelemetryDataModel.timestamp >= min(timestamps),
            TelemetryDataModel.timestamp <= max(timestamps),
        )
    )
    return {
        (str(patient_id), str(device_id), slug, timestamp.isoformat() if timestamp else None)
        for patient_id, device_id, slug, timestamp in result.all()
    }


def _telemetry_key(row: dict[str, Any]) -> tuple:
    timestamp = row.get("timestamp")
    return (
        str(row.get("patient_id")),
        str(row.get("device_id")),
        row.get("slug"),
        timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
    )


async def ingest_observations(
    db: AsyncSession,
    integration: Any,
    items: list[Any],
    *,
    provider_name: str | None = None,
    instance_name: str | None = None,
) -> IngestReport:
    """Ingest observation payloads (FHIR Observations) with mapping + dedup.

    Missing catalog mappings are recorded as :class:`MappingGap` entries and
    the row is stored **raw** (``biomarker_id`` NULL) — the contract is
    "never dropped silently". The FHIR/telemetry split is the S3/S6 helper,
    so telemetry-class biomarkers land in the hypertable with canonical units
    and a registered device.
    """
    if not items:
        return IngestReport()
    from app.services.fhir.fhir_helpers import (
        coerce_observation_patient_ids,
        validate_and_filter_observations,
    )
    from app.services.fhir.fhir_service import map_observations_to_biomarkers
    from app.services.integration_sync_service import apply_telemetry_split

    report = IngestReport(received=len(items))
    observations = [observation_from_payload(item) for item in items]
    coerce_observation_patient_ids(observations)

    # Drop rows that cannot be projected to valid FHIR *before* anything is
    # queued (the mapping helper reports a count but not which rows).
    observations, dropped_invalid = validate_and_filter_observations(observations, logger)
    if dropped_invalid:
        report.rejected += dropped_invalid

    await map_observations_to_biomarkers(db, observations)

    for obs in observations:
        if obs.biomarker_id is None:
            code, display = _observation_code_label(obs)
            report.mapping_gaps.append(
                MappingGap(
                    code=code,
                    display=display,
                    patient_id=str(obs.patient_id) if obs.patient_id else None,
                )
            )

    existing = await _existing_observation_keys(db, integration, observations)
    fresh: list[Observation] = []
    for obs in observations:
        key = observation_natural_key(obs)
        if key in existing:
            report.deduped += 1
            continue
        existing.add(key)
        fresh.append(obs)

    telemetry_records, fhir_records = await apply_telemetry_split(
        db,
        fresh,
        tenant_id=integration.tenant_id,
        instance_name=instance_name or getattr(integration, "instance_name", None),
        provider_name=provider_name or integration.provider,
        integration_id=integration.id,
    )

    # Telemetry dedup (the split is a pure router): an observation that
    # became a telemetry row is NOT an Observation row, so the
    # observation-key pass above cannot see it — dedup the split's output
    # on the telemetry natural key (patient, device, slug, timestamp) or a
    # re-pulled group would insert a duplicate hypertable row on every
    # sync. In-batch duplicates collapse through the same growing set.
    if telemetry_records:
        key_rows = [
            {
                "patient_id": rec.patient_id,
                "device_id": rec.device_id,
                "slug": rec.slug,
                "timestamp": rec.timestamp,
            }
            for rec in telemetry_records
        ]
        # no_autoflush: the split's add_all left this batch's rows pending —
        # a plain SELECT here would autoflush them and match THEMSELVES.
        with db.no_autoflush:
            existing_telemetry = await _existing_telemetry_keys(db, integration, key_rows)
        kept: list[TelemetryDataModel] = []
        for rec, key_row in zip(telemetry_records, key_rows, strict=True):
            key = _telemetry_key(key_row)
            if key in existing_telemetry:
                db.expunge(rec)
                report.deduped += 1
                continue
            existing_telemetry.add(key)
            kept.append(rec)
        telemetry_records = kept

    report.record_stream(IntegrationSyncStream.TELEMETRY, len(telemetry_records))
    report.record_stream(IntegrationSyncStream.OBSERVATIONS, len(fhir_records))
    report.written = len(telemetry_records) + len(fhir_records)
    return report


async def ingest_telemetry(
    db: AsyncSession,
    integration: Any,
    readings: list[TelemetryReading],
    *,
    provider_name: str | None = None,
) -> IngestReport:
    """Ingest raw telemetry readings through the registry/device/units seam."""
    if not readings:
        return IngestReport()

    report = IngestReport(received=len(readings))
    source = provider_name or integration.provider
    default_patient = getattr(integration, "patient_id", None)
    rows: list[dict[str, Any]] = []
    definitions: dict[str, Any] = {}
    devices: dict[tuple, UUID] = {}

    for reading in readings:
        patient_id = reading.patient_id or default_patient
        if patient_id is None:
            report.rejected += 1
            report.rejections.append({"slug": reading.slug, "reason": "no patient attribution"})
            continue
        try:
            definition = definitions.get(reading.slug)
            if definition is None:
                definition = await resolve_telemetry_definition(
                    db, integration.tenant_id, reading.slug
                )
                definitions[reading.slug] = definition
        except UnknownTelemetrySlugError as exc:
            report.rejected += 1
            report.rejections.append({"slug": reading.slug, "reason": str(exc)})
            continue

        external_ref = reading.device_external_ref or getattr(integration, "instance_name", None)
        external_ref = external_ref or source
        device_key = (str(patient_id), external_ref)
        device_id = devices.get(device_key)
        if device_id is None:
            device_id = await resolve_telemetry_device(
                db,
                tenant_id=integration.tenant_id,
                patient_id=patient_id,
                source=source,
                external_ref=str(external_ref),
            )
            devices[device_key] = device_id

        rows.append(
            build_telemetry_row(
                tenant_id=integration.tenant_id,
                patient_id=patient_id,
                device_id=device_id,
                timestamp=reading.timestamp,
                definition=definition,
                value=float(reading.value),
                unit=reading.unit,
            )
        )

    existing = await _existing_telemetry_keys(db, integration, rows)
    fresh: list[TelemetryDataModel] = []
    for row in rows:
        key = _telemetry_key(row)
        if key in existing:
            report.deduped += 1
            continue
        existing.add(key)
        fresh.append(TelemetryDataModel(**row))

    if fresh:
        db.add_all(fresh)
    report.record_stream(IntegrationSyncStream.TELEMETRY, len(fresh))
    report.written = len(fresh)
    return report


async def _document_exists(db: AsyncSession, integration: Any, external_id: str) -> bool:
    from app.models.document_model import DocumentModel

    result = await db.execute(
        select(DocumentModel.id).where(
            DocumentModel.tenant_id == integration.tenant_id,
            DocumentModel.patient_id == integration.patient_id,
            DocumentModel.source_integration_id == integration.id,
            DocumentModel.external_id == external_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def ingest_documents(
    db: AsyncSession,
    integration: Any,
    documents: list[Any],
    *,
    actor: Any,
    examination_by_external_id: dict[str, UUID] | None = None,
) -> IngestReport:
    """Ingest provider-fetched document bytes (OCR handled by the pipeline)."""
    if not documents:
        return IngestReport()
    from app.services.catalog.concept_service import resolve_concept_by_slug
    from app.services.documents.document_service import ingest_document_bytes

    report = IngestReport(received=len(documents))
    examination_by_external_id = examination_by_external_id or {}
    bytes_this_sync = 0

    for index, doc_spec in enumerate(documents):
        if index >= INTEGRATION_MAX_DOCS_PER_SYNC:
            report.rejected += 1
            report.rejections.append({"reason": "count cap", "index": index})
            continue
        content = getattr(doc_spec, "content", b"") or b""
        if bytes_this_sync + len(content) > INTEGRATION_MAX_DOC_BYTES_PER_SYNC:
            report.rejected += 1
            report.rejections.append({"reason": "byte cap", "index": index})
            logger.warning(
                "documents ingest: doc #%d (%d bytes) exceeds the %d-byte per-sync cap",
                index,
                len(content),
                INTEGRATION_MAX_DOC_BYTES_PER_SYNC,
            )
            continue

        external_id = getattr(doc_spec, "external_id", None)
        if external_id is not None and await _document_exists(db, integration, str(external_id)):
            report.deduped += 1
            continue

        try:
            exam_ext_id = getattr(doc_spec, "examination_external_id", None)
            resolved_exam_id = (
                examination_by_external_id.get(str(exam_ext_id)) if exam_ext_id else None
            )
            category_slug = getattr(doc_spec, "category_concept_slug", None)
            resolved_category_id = (
                await resolve_concept_by_slug(db, str(category_slug), tenant_id=actor.tenant_id)
                if category_slug
                else None
            )
            await ingest_document_bytes(
                filename=getattr(doc_spec, "filename", None) or "unknown",
                content=content,
                content_type=getattr(doc_spec, "content_type", None),
                tenant_id=actor.tenant_id,
                patient_id=integration.patient_id,
                owner_id=actor.user_id,
                db=db,
                examination_id=resolved_exam_id,
                include_in_extraction=bool(getattr(doc_spec, "include_in_extraction", True)),
                category_concept_id=resolved_category_id,
                source_integration_id=integration.id,
                external_id=external_id,
            )
        except Exception as exc:
            report.rejected += 1
            report.rejections.append(
                {
                    "reason": type(exc).__name__,
                    "index": index,
                    "filename": getattr(doc_spec, "filename", None),
                }
            )
            logger.warning(
                "document ingest failed for integration %s doc #%d (%r): %s",
                integration.id,
                index,
                getattr(doc_spec, "filename", "?"),
                exc,
            )
            continue

        bytes_this_sync += len(content)
        report.written += 1
        report.record_stream(IntegrationSyncStream.DOCUMENTS, 1)
    return report


async def _examination_exists(db: AsyncSession, integration: Any, external_id: str) -> bool:
    from app.models.examination_model import ExaminationModel

    result = await db.execute(
        select(ExaminationModel.id).where(
            ExaminationModel.tenant_id == integration.tenant_id,
            ExaminationModel.patient_id == integration.patient_id,
            ExaminationModel.source_integration_id == integration.id,
            ExaminationModel.external_id == external_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _clinical_event_exists(db: AsyncSession, integration: Any, external_id: str) -> bool:
    from app.models.clinical_event import ClinicalEvent

    result = await db.execute(
        select(ClinicalEvent.id).where(
            ClinicalEvent.tenant_id == integration.tenant_id,
            ClinicalEvent.patient_id == integration.patient_id,
            ClinicalEvent.source_integration_id == integration.id,
            ClinicalEvent.external_id == external_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def ingest_appointments(
    db: AsyncSession,
    integration: Any,
    *,
    examinations: list[Any] | None = None,
    clinical_events: list[Any] | None = None,
    actor: Any,
) -> tuple[IngestReport, dict[str, UUID]]:
    """Ingest the clinical-event/calendar surface; returns exam external→id map."""
    from app.services.clinical.clinical_event_service import create_event
    from app.services.clinical.examination_service import create_examination

    report = IngestReport()
    exam_by_external_id: dict[str, UUID] = {}

    for exam_payload in examinations or []:
        report.received += 1
        external_id = getattr(exam_payload, "external_id", None)
        if external_id is not None and await _examination_exists(db, integration, str(external_id)):
            report.deduped += 1
            continue
        try:
            created_exam = await create_examination(
                db,
                actor,
                exam_payload,
                source_integration_id=integration.id,
                external_id=external_id,
            )
        except Exception as exc:
            report.rejected += 1
            report.rejections.append(
                {"kind": "examination", "external_id": external_id, "reason": type(exc).__name__}
            )
            logger.warning(
                "create_examination failed for integration %s exam %r: %s",
                integration.id,
                external_id,
                exc,
            )
            continue
        report.written += 1
        report.record_stream(IntegrationSyncStream.APPOINTMENTS, 1)
        report.record_kind("examinations")
        if external_id and created_exam is not None:
            exam_by_external_id[str(external_id)] = created_exam.id

    for event_payload in clinical_events or []:
        report.received += 1
        external_id = getattr(event_payload, "external_id", None)
        if external_id is not None and await _clinical_event_exists(
            db, integration, str(external_id)
        ):
            report.deduped += 1
            continue
        try:
            await create_event(
                db,
                actor,
                event_payload,
                source_integration_id=integration.id,
                external_id=external_id,
            )
        except Exception as exc:
            report.rejected += 1
            report.rejections.append(
                {"kind": "clinical_event", "external_id": external_id, "reason": type(exc).__name__}
            )
            logger.warning(
                "create_event failed for integration %s event %r: %s",
                integration.id,
                external_id,
                exc,
            )
            continue
        report.written += 1
        report.record_stream(IntegrationSyncStream.APPOINTMENTS, 1)
        report.record_kind("clinical_events")

    return report, exam_by_external_id


async def ingest_labs(
    db: AsyncSession,
    integration: Any,
    reports: list[dict[str, Any]],
    *,
    actor: Any,
) -> IngestReport:
    """Ingest lab reports (FHIR DiagnosticReport) as the ``labs`` stream.

    Dedup follows the funnel's natural-key convention for rows without
    source-id columns (the DiagnosticReport table has none): (tenant,
    patient, code, effective) — a re-pull of the same report writes zero
    duplicates while a genuinely new report of the same panel still lands.
    """
    if not reports:
        return IngestReport()
    from app.models.fhir import DiagnosticReport
    from app.services.fhir.fhir_service import create_diagnostic_report

    report = IngestReport(received=len(reports))
    for payload in reports:
        code = payload.get("code") if isinstance(payload, dict) else None
        effective = payload.get("effective_datetime")
        existing = await db.execute(
            select(DiagnosticReport.id).where(
                DiagnosticReport.tenant_id == integration.tenant_id,
                DiagnosticReport.patient_id == integration.patient_id,
                DiagnosticReport.code == (code or {}),
                DiagnosticReport.effective_datetime == effective,
            )
        )
        if existing.scalar_one_or_none() is not None:
            report.deduped += 1
            continue
        try:
            await create_diagnostic_report(payload, tenant_id=integration.tenant_id)
        except Exception as exc:
            report.rejected += 1
            report.rejections.append({"kind": "diagnostic_report", "reason": type(exc).__name__})
            logger.warning(
                "create_diagnostic_report failed for integration %s: %s",
                integration.id,
                exc,
            )
            continue
        report.written += 1
        report.record_stream(IntegrationSyncStream.LABS, 1)
    return report
