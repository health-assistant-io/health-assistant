"""Vaccine catalog + patient-immunization service (Phase 5).

Mirrors ``medication_service``: catalog CRUD (tenant-scoped reads, RBAC writes,
FHIR write-time gate) and patient-instance CRUD (tenant + patient-access
scoped). All functions take the request ``db`` session.
"""

import logging
from typing import List, Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.access import check_patient_access
from app.catalogs.policy import DEFAULT_CATALOG_POLICY
from app.models.fhir.vaccine import PatientImmunization, VaccineCatalog
from app.schemas.user import TokenData
from app.schemas.vaccine import (
    PatientImmunizationCreate,
    PatientImmunizationUpdate,
    VaccineCatalogCreate,
    VaccineCatalogUpdate,
)
from app.services.fhir_helpers import assert_valid_fhir

logger = logging.getLogger(__name__)


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
    db: AsyncSession,
    current_user: TokenData,
    data: PatientImmunizationCreate,
    *,
    source_integration_id: Optional[UUID] = None,
    external_id: Optional[str] = None,
) -> PatientImmunization:
    """Create a patient immunization (vaccine dose).

    Refactored to the canonical write-chokepoint shape shared with the
    other patient-instance services (medications / allergies / events /
    exams): takes the request actor (``TokenData``) so patient-access
    checks, tenant scoping, and ``created_by`` audit provenance live
    here once. Called by ``POST /vaccines/patient/{id}`` and the
    integration-sync engine (Phase 4 of the fhir-server multi-resource
    sync plan, when a provider opts into ``supports_immunizations``).

    Integration-sourced dedup: when **both** ``source_integration_id``
    and ``external_id`` are supplied, returns the existing row instead
    of duplicating.
    """
    if data.patient_id is None:
        raise ValueError("PatientImmunizationCreate.patient_id is required.")
    await check_patient_access(data.patient_id, current_user, db)

    effective_external_id = external_id or data.external_id

    if source_integration_id is not None and effective_external_id is not None:
        existing = await _find_integration_immunization(
            db,
            tenant_id=current_user.tenant_id,
            patient_id=data.patient_id,
            source_integration_id=source_integration_id,
            external_id=effective_external_id,
        )
        if existing is not None:
            logger.info(
                "add_patient_immunization: returning existing %s (dedup "
                "hit on source_integration_id=%s external_id=%r)",
                existing.id, source_integration_id, effective_external_id,
            )
            return existing

    record = PatientImmunization(
        patient_id=data.patient_id,
        tenant_id=current_user.tenant_id,
        vaccine_catalog_id=data.vaccine_catalog_id,
        examination_id=data.examination_id,
        status=data.status,
        vaccine_code=data.vaccine_code.model_dump(mode="json"),
        administered_at=data.administered_at,
        dose_number=data.dose_number,
        lot_number=data.lot_number,
        manufacturer=data.manufacturer,
        location=data.location,
        note=data.note,
        created_by=current_user.user_id,
        source_integration_id=source_integration_id,
        external_id=effective_external_id,
    )
    assert_valid_fhir(record)  # write-time FHIR gate (projects to Immunization)
    db.add(record)
    try:
        await db.flush()
    except IntegrityError:
        # Race window — the partial unique index
        # ``uq_patient_immunizations_integration_dedup`` caught a concurrent
        # insert. Roll back and return the winner.
        await db.rollback()
        existing = await _find_integration_immunization(
            db,
            tenant_id=current_user.tenant_id,
            patient_id=data.patient_id,
            source_integration_id=source_integration_id,
            external_id=effective_external_id,
        )
        if existing is not None:
            return existing
        raise
    await db.commit()
    await db.refresh(record)
    return record


async def _find_integration_immunization(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    patient_id: UUID,
    source_integration_id: UUID,
    external_id: str,
) -> Optional[PatientImmunization]:
    """Look up an existing integration-sourced immunization by dedup key.

    Backed by the partial unique index
    ``uq_patient_immunizations_integration_dedup`` (migration f1m2u3l4t5i6).
    """
    stmt = select(PatientImmunization).where(
        PatientImmunization.tenant_id == tenant_id,
        PatientImmunization.patient_id == patient_id,
        PatientImmunization.source_integration_id == source_integration_id,
        PatientImmunization.external_id == external_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


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
