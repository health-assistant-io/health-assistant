from typing import List, Optional, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.doctor_model import DoctorModel
from app.models.enums import ConceptKind
from app.services.concept_service import resolve_concept_by_slug, concepts_with_kind
from app.services.fhir_helpers import assert_valid_fhir


async def _resolve_specialty_concept(
    db: AsyncSession, specialty: Optional[str], tenant_id: Optional[UUID] = None
) -> Optional[UUID]:
    """Best-effort resolve a free-text specialty to a ``specialty`` concept.

    Tries by slug first (case-insensitive, slugified), then by exact name
    match. Returns ``None`` if no match — the specialty text is then lost
    (acceptable for greenfield per the taxonomy consolidation note).
    """
    if not specialty:
        return None
    import re

    candidate_slug = re.sub(r"[^a-z0-9]+", "-", specialty.strip().lower()).strip("-")
    if candidate_slug:
        cid = await resolve_concept_by_slug(
            db, candidate_slug, ConceptKind.SPECIALTY, tenant_id=tenant_id
        )
        if cid:
            return cid
    # Fallback: match by name (case-insensitive equality on the lowercase name)
    from app.models.concept_model import Concept
    from sqlalchemy import func as sa_func

    res = await db.execute(
        select(Concept.id).where(
            concepts_with_kind(ConceptKind.SPECIALTY),
            sa_func.lower(Concept.name) == specialty.strip().lower(),
            Concept.deleted_at.is_(None),
        )
    )
    row = res.first()
    return row[0] if row else None


async def list_doctors(
    tenant_id: UUID | None, db: AsyncSession, user_id: Optional[UUID] = None
) -> List[DoctorModel]:
    query = select(DoctorModel)
    if tenant_id:
        query = query.where(DoctorModel.tenant_id == tenant_id)

    if user_id:
        query = query.where(DoctorModel.user_id == user_id)

    result = await db.execute(query.order_by(DoctorModel.name))
    return list(result.scalars().all())


async def get_doctor(
    doctor_id: UUID, tenant_id: UUID, db: AsyncSession
) -> Optional[DoctorModel]:
    result = await db.execute(
        select(DoctorModel).where(
            DoctorModel.id == doctor_id, DoctorModel.tenant_id == tenant_id
        )
    )
    return result.scalar_one_or_none()


async def create_doctor(
    tenant_id: UUID,
    creator_id: UUID,
    name: str,
    specialty: Optional[str] = None,
    specialty_concept_id: Optional[UUID] = None,
    license_number: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    telecom: Optional[List[Any]] = None,
    address: Optional[Any] = None,
    office_number: Optional[str] = None,
    office_details: Optional[str] = None,
    user_id: Optional[UUID] = None,  # Linked identity
    db: AsyncSession = None,
) -> DoctorModel:
    # Prefer an explicit ``specialty_concept_id``; fall back to resolving the
    # legacy ``specialty`` free-text against the ``specialty`` concept catalog.
    if specialty_concept_id is None and specialty:
        specialty_concept_id = await _resolve_specialty_concept(
            db, specialty, tenant_id=tenant_id
        )
    doctor = DoctorModel(
        tenant_id=tenant_id,
        created_by=creator_id,
        user_id=user_id,
        name=name,
        specialty_concept_id=specialty_concept_id,
        license_number=license_number,
        email=email,
        phone=phone,
        telecom=telecom,
        address=address,
        office_number=office_number,
        office_details=office_details,
    )
    # FHIR validation gate (audit: write-time gate coverage). Catches invalid
    # Practitioner shapes (e.g. empty name) before persisting; raises
    # FhirSerializationError → mapped to HTTP 400 by the global handler.
    assert_valid_fhir(doctor)
    db.add(doctor)
    await db.commit()
    await db.refresh(doctor)
    return doctor


async def update_doctor(
    doctor_id: UUID, tenant_id: UUID, db: AsyncSession, **kwargs
) -> Optional[DoctorModel]:
    doctor = await get_doctor(doctor_id, tenant_id, db)
    if not doctor:
        return None

    # ``specialty_concept_id`` (the FK) is authoritative. ``specialty`` is a
    # read-only property — if a caller sends only the legacy free-text, resolve
    # it to a concept ID (best-effort) and drop the property name.
    if "specialty" in kwargs and "specialty_concept_id" not in kwargs:
        specialty_concept_id = await _resolve_specialty_concept(
            db, kwargs.pop("specialty"), tenant_id=tenant_id
        )
        kwargs["specialty_concept_id"] = specialty_concept_id
    elif "specialty" in kwargs:
        kwargs.pop("specialty")

    for key, value in kwargs.items():
        if hasattr(doctor, key):
            setattr(doctor, key, value)

    # FHIR validation gate (audit: write-time gate coverage). Verifies the
    # mutated DoctorModel still projects to a valid Practitioner before commit.
    assert_valid_fhir(doctor)
    await db.commit()
    await db.refresh(doctor)
    return doctor


async def delete_doctor(doctor_id: UUID, tenant_id: UUID, db: AsyncSession) -> bool:
    doctor = await get_doctor(doctor_id, tenant_id, db)
    if not doctor:
        return False

    await db.delete(doctor)
    await db.commit()
    return True
