import logging
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.processors.nlp import get_nlp_extractor_from_db
from app.ai.schemas.nlp import UnknownMedicationExtract
from app.core.authz.permissions import Permission
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.patient_access_grant import GrantPermission
from app.schemas.medication import (
    MedicationRecordCreate,
    MedicationRecordUpdate,
)
from app.schemas.user import TokenData
from app.services.auth.access import check_patient_access, require_principal_permission
from app.services.fhir.fhir_helpers import assert_valid_fhir
from app.services.notifications.notification_manager import NotificationManager

logger = logging.getLogger(__name__)


async def get_medication_catalog(
    db: AsyncSession, tenant_id: UUID, search: str | None = None
) -> list[MedicationCatalog]:
    # Delegate to the unified catalog search service: trigram similarity on
    # name + indications/description substring fallback, tenant-scoped, with
    # similarity ranking. (Previously: name.ilike only, unranked.)
    from app.services.catalog.catalog_search_service import search_medications

    return await search_medications(db, tenant_id, search)


async def get_catalog_medication(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> MedicationCatalog | None:
    query = select(MedicationCatalog).where(
        MedicationCatalog.id == catalog_id,
        or_(
            MedicationCatalog.tenant_id.is_(None),
            MedicationCatalog.tenant_id == tenant_id,
        ),
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_patient_medications(
    db: AsyncSession, patient_id: UUID, tenant_id: UUID
) -> list[Medication]:
    query = (
        select(Medication)
        .where(
            Medication.patient_id == patient_id,
            Medication.tenant_id == tenant_id,
        )
        .order_by(Medication.start_date.desc(), Medication.created_at.desc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def add_patient_medication(
    db: AsyncSession,
    current_user: TokenData,
    data: MedicationRecordCreate,
    *,
    source_integration_id: UUID | None = None,
    external_id: str | None = None,
) -> Medication:
    """Create a patient medication (prescription).

    Refactored to the canonical write-chokepoint shape shared with
    ``clinical_event_service.create_event`` and
    ``examination_service.create_examination``: takes the request actor
    (``TokenData``) rather than a raw ``tenant_id`` so provenance +
    ``created_by`` audit + patient-access checks live here once. The
    ``POST /medications/patient/{id}`` endpoint and the integration-sync
    engine (Phase 4 of the fhir-server multi-resource sync plan, when a
    provider declares the ``medication`` data class) both call this.

    Integration-sourced dedup: when **both** ``source_integration_id`` and
    ``external_id`` are supplied, the service looks up an existing record
    with that key for the same patient + tenant and returns it as-is
    rather than creating a duplicate (mirrors the pattern on events /
    exams / docs / allergies / immunizations). UI callers leave both
    ``None`` and get the original create-always behavior.
    """
    patient_id = data.patient_id
    if patient_id is None:
        raise ValueError("MedicationRecordCreate.patient_id is required.")
    require_principal_permission(current_user, Permission.CLINICAL_WRITE)
    await check_patient_access(patient_id, current_user, db, required=GrantPermission.WRITE)

    effective_external_id = external_id or data.external_id

    if source_integration_id is not None and effective_external_id is not None:
        existing = await _find_integration_medication(
            db,
            tenant_id=current_user.tenant_id,
            patient_id=patient_id,
            source_integration_id=source_integration_id,
            external_id=effective_external_id,
        )
        if existing is not None:
            logger.info(
                "add_patient_medication: returning existing %s (dedup hit on "
                "source_integration_id=%s external_id=%r)",
                existing.id,
                source_integration_id,
                effective_external_id,
            )
            return existing

    # Handle both schema field 'frequency' and internal 'timing'
    timing_data = getattr(data, "timing", None)
    if not timing_data and hasattr(data, "frequency"):
        # Map our internal frequency schema to FHIR timing
        freq = data.frequency
        if freq:
            timing_data = {
                "repeat": {
                    "frequency": freq.frequency,
                    "period": freq.period,
                    "periodUnit": freq.period_unit[0] if freq.period_unit else "d",
                    "dayOfWeek": freq.days_of_week,
                    "timeOfDay": freq.time_of_day,
                }
            }

    # ``intent`` is Optional on the schema so the ORM column default
    # (STATEMENT) applies when a caller doesn't specify it; pull it out
    # of the spread to avoid passing ``intent=None`` (which would override
    # the NOT NULL column default).
    dump = data.model_dump(exclude={"frequency", "timing", "external_id", "patient_id", "intent"})
    intent_value = data.intent
    new_record = Medication(
        **dump,
        patient_id=patient_id,
        tenant_id=current_user.tenant_id,
        subject={"reference": f"Patient/{patient_id}"},
        created_by=current_user.user_id,
        source_integration_id=source_integration_id,
        external_id=effective_external_id,
    )
    if intent_value is not None:
        new_record.intent = intent_value
    if timing_data:
        new_record.frequency = timing_data

    # FHIR validation gate (audit: write-time gate coverage). The Medication
    # ORM row projects to either MedicationStatement or MedicationRequest per
    # the `intent` discriminator (see Medication.to_fhir_dict); the gate
    # catches invalid shapes before persisting.
    assert_valid_fhir(new_record)
    db.add(new_record)
    try:
        await db.flush()
    except IntegrityError:
        # Race window between the dedup SELECT and INSERT — the partial
        # unique index ``uq_fhir_medications_integration_dedup`` caught a
        # concurrent insert. Roll back and return the winner.
        await db.rollback()
        existing = await _find_integration_medication(
            db,
            tenant_id=current_user.tenant_id,
            patient_id=patient_id,
            source_integration_id=source_integration_id,
            external_id=effective_external_id,
        )
        if existing is not None:
            return existing
        raise
    await db.commit()
    await db.refresh(new_record)

    # Automatically create triggers
    if timing_data:
        await NotificationManager.sync_medication_triggers(
            patient_id=patient_id,
            medication_id=new_record.id,
            medication_name=new_record.code.get("text", "medication"),
            timing_data=timing_data,
            tenant_id=current_user.tenant_id,
        )

    return new_record


async def _find_integration_medication(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    patient_id: UUID,
    source_integration_id: UUID,
    external_id: str,
) -> Medication | None:
    """Look up an existing integration-sourced medication by dedup key.

    The partial unique index ``uq_fhir_medications_integration_dedup``
    (migration f1m2u3l4t5i6) backs this lookup.
    """
    stmt = select(Medication).where(
        Medication.tenant_id == tenant_id,
        Medication.patient_id == patient_id,
        Medication.source_integration_id == source_integration_id,
        Medication.external_id == external_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_patient_medication(
    db: AsyncSession,
    medication_id: UUID,
    current_user: TokenData,
    data: MedicationRecordUpdate,
) -> Medication | None:
    """Update one patient medication.

    Takes the request actor (not a raw tenant id) so the scoped-credential
    guard runs at the same seam as create: a ``patient:read``-only PAT or
    integration principal is refused here even when it reaches the service
    through a path that never ran the route gate.
    """
    require_principal_permission(current_user, Permission.CLINICAL_WRITE)
    tenant_id = current_user.tenant_id
    query = select(Medication).where(
        Medication.id == medication_id,
        Medication.tenant_id == tenant_id,
    )
    result = await db.execute(query)
    record = result.scalar_one_or_none()

    if not record:
        return None

    update_data = data.model_dump(exclude_unset=True)

    # Handle the 'code' field specifically if it's updated
    if update_data.get("code"):
        # Ensure we don't accidentally wipe existing catalog_id if only text changed,
        # or vice versa (though current UI sends both)
        current_code = record.code or {}
        record.code = {**current_code, **update_data["code"]}
        del update_data["code"]

    # Handle frequency update
    timing_data = None
    if update_data.get("frequency"):
        freq = data.frequency
        timing_data = {
            "repeat": {
                "frequency": freq.frequency,
                "period": freq.period,
                "periodUnit": freq.period_unit[0] if freq.period_unit else "d",
                "dayOfWeek": freq.days_of_week,
                "timeOfDay": freq.time_of_day,
            }
        }
        record.frequency = timing_data
        del update_data["frequency"]

    for key, value in update_data.items():
        setattr(record, key, value)

    # FHIR validation gate (audit: write-time gate coverage). Verifies the
    # mutated Medication still projects to a valid MedicationStatement /
    # MedicationRequest before commit.
    assert_valid_fhir(record)
    await db.commit()
    await db.refresh(record)

    # Sync triggers if timing or name changed
    if timing_data or "code" in data.model_dump(exclude_unset=True):
        await NotificationManager.sync_medication_triggers(
            patient_id=record.patient_id,
            medication_id=record.id,
            medication_name=record.code.get("text", "medication"),
            timing_data=record.frequency,
            tenant_id=tenant_id,
        )

    return record


async def delete_patient_medication(
    db: AsyncSession, medication_id: UUID, current_user: TokenData
) -> bool:
    """Hard-delete one medication row (actor-gated, tenant-scoped)."""
    require_principal_permission(current_user, Permission.CLINICAL_WRITE)
    # Cleanup triggers first
    await NotificationManager.delete_triggers_by_reference(medication_id)

    query = delete(Medication).where(
        Medication.id == medication_id,
        Medication.tenant_id == current_user.tenant_id,
    )
    result = await db.execute(query)
    await db.commit()
    return result.rowcount > 0


async def get_medication_usage(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> list[dict[str, Any]]:
    """Get all patients using a specific medication from the catalog"""
    from app.models.fhir.patient import Patient

    query = (
        select(Medication, Patient)
        .join(Patient, Medication.patient_id == Patient.id)
        .where(
            Medication.code["catalog_id"].astext == str(catalog_id),
            Medication.tenant_id == tenant_id,
        )
    )
    result = await db.execute(query)
    rows = result.all()

    usage = []
    for med, patient in rows:
        usage.append(
            {
                "medication": med.to_dict(),
                "patient": {
                    "id": str(patient.id),
                    "name": patient.name,
                    "mrn": patient.mrn,
                },
            }
        )
    return usage


async def reprocess_medication(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> MedicationCatalog | None:
    """Use AI to re-analyze and enrich medication catalog entry"""
    query = select(MedicationCatalog).where(
        MedicationCatalog.id == catalog_id,
        or_(
            MedicationCatalog.tenant_id.is_(None),
            MedicationCatalog.tenant_id == tenant_id,
        ),
    )
    result = await db.execute(query)
    med = result.scalar_one_or_none()

    if not med:
        return None

    # Get NLP extractor
    nlp = await get_nlp_extractor_from_db(db, task_type="nlp", tenant_id=tenant_id)

    # Wrap in unknown extract to trigger pass 2
    wrapped = [UnknownMedicationExtract(raw_name=med.name)]

    # Run Pass 2 (Enrichment)
    new_defs = await nlp.parse_document_pass_2_medications(wrapped)

    if not new_defs.definitions:
        return med

    enriched = new_defs.definitions[0]

    # Update fields
    med.description = enriched.description
    med.indications = enriched.indications
    med.side_effects = enriched.side_effects
    med.contraindications = enriched.contraindications
    med.dosage_info = enriched.dosage_info

    # Only update name if it was somehow significantly improved or cleaned
    if enriched.name and len(enriched.name) > 2:
        med.name = enriched.name

    await db.commit()
    await db.refresh(med)
    return med
