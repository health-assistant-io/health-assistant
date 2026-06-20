from typing import List, Optional, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, delete
from app.models.fhir.medication import Medication, MedicationCatalog, MedicationStatus
from app.services.notification_manager import NotificationManager
from app.models.notification import NotificationType, TriggerType
from app.schemas.medication import (
    MedicationCatalogCreate,
    MedicationCatalogUpdate,
    MedicationRecordCreate,
    MedicationRecordUpdate,
)


from app.processors.nlp import get_nlp_extractor_from_db
from app.schemas.ai_nlp import UnknownMedicationExtract


async def get_medication_catalog(
    db: AsyncSession, tenant_id: UUID, search: Optional[str] = None
) -> List[MedicationCatalog]:
    # Delegate to the unified catalog search service: trigram similarity on
    # name + indications/description substring fallback, tenant-scoped, with
    # similarity ranking. (Previously: name.ilike only, unranked.)
    from app.services.catalog_search_service import search_medications

    return await search_medications(db, tenant_id, search)


async def get_catalog_medication(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> Optional[MedicationCatalog]:
    query = select(MedicationCatalog).where(
        MedicationCatalog.id == catalog_id,
        or_(
            MedicationCatalog.tenant_id == None,
            MedicationCatalog.tenant_id == tenant_id,
        ),
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def create_catalog_medication(
    db: AsyncSession, tenant_id: UUID, data: MedicationCatalogCreate
) -> MedicationCatalog:
    new_entry = MedicationCatalog(tenant_id=tenant_id, **data.model_dump())
    db.add(new_entry)
    await db.commit()
    await db.refresh(new_entry)
    return new_entry


async def update_catalog_medication(
    db: AsyncSession,
    catalog_id: UUID,
    tenant_id: UUID,
    data: MedicationCatalogUpdate,
) -> Optional[MedicationCatalog]:
    query = select(MedicationCatalog).where(
        MedicationCatalog.id == catalog_id,
        or_(
            MedicationCatalog.tenant_id == None,
            MedicationCatalog.tenant_id == tenant_id,
        ),
    )
    result = await db.execute(query)
    med = result.scalar_one_or_none()

    if not med:
        return None

    # If it's a system medication (tenant_id is None),
    # we might want to prevent editing or only allow superusers.
    # For now, let's allow it if it belongs to the tenant or is system-wide.
    # Note: If tenant edits a system med, it should ideally create a custom version,
    # but here we'll just update it directly for simplicity.

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(med, key, value)

    await db.commit()
    await db.refresh(med)
    return med


async def get_patient_medications(
    db: AsyncSession, patient_id: UUID, tenant_id: UUID
) -> List[Medication]:
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
    db: AsyncSession, patient_id: UUID, tenant_id: UUID, data: MedicationRecordCreate
) -> Medication:
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

    new_record = Medication(
        patient_id=patient_id,
        tenant_id=tenant_id,
        subject={"reference": f"Patient/{patient_id}"},
        **data.model_dump(exclude={"frequency", "timing"}),
    )
    if timing_data:
        new_record.frequency = timing_data

    db.add(new_record)
    await db.commit()
    await db.refresh(new_record)

    # Automatically create triggers
    if timing_data:
        await NotificationManager.sync_medication_triggers(
            patient_id=patient_id,
            medication_id=new_record.id,
            medication_name=new_record.code.get("text", "medication"),
            timing_data=timing_data,
            tenant_id=tenant_id,
        )

    return new_record


async def update_patient_medication(
    db: AsyncSession,
    medication_id: UUID,
    tenant_id: UUID,
    data: MedicationRecordUpdate,
) -> Optional[Medication]:
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
    if "code" in update_data and update_data["code"]:
        # Ensure we don't accidentally wipe existing catalog_id if only text changed,
        # or vice versa (though current UI sends both)
        current_code = record.code or {}
        record.code = {**current_code, **update_data["code"]}
        del update_data["code"]

    # Handle frequency update
    timing_data = None
    if "frequency" in update_data and update_data["frequency"]:
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
    db: AsyncSession, medication_id: UUID, tenant_id: UUID
) -> bool:
    # Cleanup triggers first
    await NotificationManager.delete_triggers_by_reference(medication_id)

    query = delete(Medication).where(
        Medication.id == medication_id,
        Medication.tenant_id == tenant_id,
    )
    result = await db.execute(query)
    await db.commit()
    return result.rowcount > 0


async def get_medication_usage(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> List[Dict[str, Any]]:
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
) -> Optional[MedicationCatalog]:
    """Use AI to re-analyze and enrich medication catalog entry"""
    query = select(MedicationCatalog).where(
        MedicationCatalog.id == catalog_id,
        or_(
            MedicationCatalog.tenant_id == None,
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
