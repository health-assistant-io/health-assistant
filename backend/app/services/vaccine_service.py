"""Vaccine catalog + patient-immunization service (Phase 5).

Mirrors ``medication_service``: catalog CRUD (tenant-scoped reads, RBAC writes,
FHIR write-time gate) and patient-instance CRUD (tenant + patient-access
scoped). All functions take the request ``db`` session.
"""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs.policy import DEFAULT_CATALOG_POLICY
from app.models.fhir.vaccine import PatientImmunization, VaccineCatalog
from app.schemas.vaccine import (
    PatientImmunizationCreate,
    PatientImmunizationUpdate,
    VaccineCatalogCreate,
    VaccineCatalogUpdate,
)
from app.services.fhir_helpers import assert_valid_fhir


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


async def get_vaccine_catalog(
    db: AsyncSession, tenant_id: UUID, search: Optional[str] = None
) -> List[VaccineCatalog]:
    """Tenant-scoped catalog read (global + tenant). Simple ilike search — the
    trigram dispatcher in ``search_catalogs`` handles typo-tolerant search."""
    stmt = select(VaccineCatalog).where(
        or_(
            VaccineCatalog.tenant_id.is_(None),
            VaccineCatalog.tenant_id == tenant_id,
        )
    )
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                VaccineCatalog.name.ilike(term),
                VaccineCatalog.description.ilike(term),
                VaccineCatalog.code.ilike(term),
            )
        )
    stmt = stmt.order_by(VaccineCatalog.name.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_catalog_vaccine(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> Optional[VaccineCatalog]:
    stmt = select(VaccineCatalog).where(
        VaccineCatalog.id == catalog_id,
        or_(
            VaccineCatalog.tenant_id.is_(None),
            VaccineCatalog.tenant_id == tenant_id,
        ),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_catalog_vaccine(
    db: AsyncSession, actor, data: VaccineCatalogCreate
) -> VaccineCatalog:
    entry = VaccineCatalog(**data.model_dump())
    DEFAULT_CATALOG_POLICY.assign_create_scope(
        actor.role, entry, actor.tenant_id, actor.user_id
    )
    assert_valid_fhir(entry)  # write-time FHIR gate (projects to Medication)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def update_catalog_vaccine(
    db: AsyncSession,
    catalog_id: UUID,
    actor,
    data: VaccineCatalogUpdate,
) -> Optional[VaccineCatalog]:
    entry = await get_catalog_vaccine(db, catalog_id, actor.tenant_id)
    if entry is None:
        return None
    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        entry.scope,
        item_created_by=entry.created_by,
        actor_user_id=actor.user_id,
    )
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(entry, key, value)
    assert_valid_fhir(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def delete_catalog_vaccine(
    db: AsyncSession,
    catalog_id: UUID,
    actor,
) -> bool:
    entry = await get_catalog_vaccine(db, catalog_id, actor.tenant_id)
    if entry is None:
        return False
    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        entry.scope,
        item_created_by=entry.created_by,
        actor_user_id=actor.user_id,
    )
    await db.delete(entry)
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Patient immunizations (instances)
# ---------------------------------------------------------------------------


async def get_patient_immunizations(
    db: AsyncSession, patient_id: UUID, tenant_id: UUID
) -> List[PatientImmunization]:
    stmt = (
        select(PatientImmunization)
        .where(
            PatientImmunization.patient_id == patient_id,
            PatientImmunization.tenant_id == tenant_id,
            PatientImmunization.deleted_at.is_(None),
        )
        .order_by(
            PatientImmunization.administered_at.desc(),
            PatientImmunization.created_at.desc(),
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def add_patient_immunization(
    db: AsyncSession, patient_id: UUID, tenant_id: UUID, data: PatientImmunizationCreate
) -> PatientImmunization:
    record = PatientImmunization(
        patient_id=patient_id,
        tenant_id=tenant_id,
        vaccine_catalog_id=data.vaccine_catalog_id,
        status=data.status,
        vaccine_code=data.vaccine_code.model_dump(mode="json"),
        administered_at=data.administered_at,
        dose_number=data.dose_number,
        lot_number=data.lot_number,
        manufacturer=data.manufacturer,
        location=data.location,
        note=data.note,
    )
    assert_valid_fhir(record)  # write-time FHIR gate (projects to Immunization)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def update_patient_immunization(
    db: AsyncSession,
    immunization_id: UUID,
    tenant_id: UUID,
    data: PatientImmunizationUpdate,
) -> Optional[PatientImmunization]:
    record = await get_immunization_for_access(db, immunization_id, tenant_id)
    if record is None:
        return None
    update_data = data.model_dump(exclude_unset=True)
    if "vaccine_code" in update_data and update_data["vaccine_code"] is not None:
        update_data["vaccine_code"] = dict(update_data["vaccine_code"])
    for key, value in update_data.items():
        setattr(record, key, value)
    assert_valid_fhir(record)
    await db.commit()
    await db.refresh(record)
    return record


async def delete_patient_immunization(
    db: AsyncSession, immunization_id: UUID, tenant_id: UUID
) -> bool:
    record = await get_immunization_for_access(db, immunization_id, tenant_id)
    if record is None:
        return False
    await db.delete(record)
    await db.commit()
    return True


async def get_immunization_for_access(
    db: AsyncSession, immunization_id: UUID, tenant_id: UUID
) -> Optional[PatientImmunization]:
    """Fetch one patient-immunization row scoped to the caller's tenant."""
    return (
        await db.execute(
            select(PatientImmunization).where(
                PatientImmunization.id == immunization_id,
                PatientImmunization.tenant_id == tenant_id,
                PatientImmunization.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
