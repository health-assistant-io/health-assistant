"""Allergy catalog + patient-instance intolerance service.

Mirrors :mod:`app.services.medication_service`: db-injected async functions
with Pydantic-schema params (not raw dicts), write-time FHIR validation gate,
and scope+ownership RBAC on catalog mutations. Adds the parity surface that
was missing vs medications — single-instance fetch, cross-patient usage, and
AI reprocess.
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance
from app.schemas.allergy import (
    AllergyCatalogCreate,
    AllergyCatalogUpdate,
    AllergyIntoleranceCreate,
    AllergyIntoleranceUpdate,
)
from app.services.fhir_helpers import assert_valid_fhir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


async def get_allergy_catalog(
    db: AsyncSession, tenant_id: UUID, search: Optional[str] = None
) -> List[AllergyCatalog]:
    """Search the global + tenant allergy catalog (hybrid search)."""
    from app.services.catalog_search_service import search_allergies

    return await search_allergies(db, tenant_id, search)


async def get_catalog_allergy(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> Optional[AllergyCatalog]:
    query = select(AllergyCatalog).where(
        AllergyCatalog.id == catalog_id,
        or_(
            AllergyCatalog.tenant_id.is_(None),
            AllergyCatalog.tenant_id == tenant_id,
        ),
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def create_catalog_allergy(
    db: AsyncSession, actor, data: AllergyCatalogCreate
) -> AllergyCatalog:
    from app.catalogs.policy import DEFAULT_CATALOG_POLICY

    new_entry = AllergyCatalog(**data.model_dump())
    DEFAULT_CATALOG_POLICY.assign_create_scope(
        actor.role, new_entry, actor.tenant_id, actor.user_id
    )
    # Write-time FHIR gate (parity with medication_service). AllergyCatalog
    # projects to FHIR Substance; invalid shapes never persist.
    assert_valid_fhir(new_entry)
    db.add(new_entry)
    await db.commit()
    await db.refresh(new_entry)
    return new_entry


async def update_catalog_allergy(
    db: AsyncSession,
    catalog_id: UUID,
    actor,
    data: AllergyCatalogUpdate,
) -> Optional[AllergyCatalog]:
    from app.catalogs.policy import DEFAULT_CATALOG_POLICY

    query = select(AllergyCatalog).where(
        AllergyCatalog.id == catalog_id,
        or_(
            AllergyCatalog.tenant_id.is_(None),
            AllergyCatalog.tenant_id == actor.tenant_id,
        ),
    )
    result = await db.execute(query)
    entry = result.scalar_one_or_none()
    if entry is None:
        return None

    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        entry.scope,
        item_created_by=entry.created_by,
        actor_user_id=actor.user_id,
    )

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(entry, key, value)

    assert_valid_fhir(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def delete_catalog_allergy(
    db: AsyncSession,
    catalog_id: UUID,
    actor,
) -> bool:
    """Delete an allergy catalog entry (scope + ownership enforced).

    Returns ``False`` if the entry is missing or out of tenant scope.
    Raises :class:`CatalogPermissionDenied` (→ HTTP 403) on insufficient role.
    """
    from app.catalogs.policy import DEFAULT_CATALOG_POLICY

    query = select(AllergyCatalog).where(
        AllergyCatalog.id == catalog_id,
        or_(
            AllergyCatalog.tenant_id.is_(None),
            AllergyCatalog.tenant_id == actor.tenant_id,
        ),
    )
    result = await db.execute(query)
    entry = result.scalar_one_or_none()
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
# Patient-instance intolerances
# ---------------------------------------------------------------------------


async def get_patient_allergies(
    db: AsyncSession, patient_id: UUID, tenant_id: UUID
) -> List[AllergyIntolerance]:
    query = (
        select(AllergyIntolerance)
        .where(
            AllergyIntolerance.patient_id == patient_id,
            AllergyIntolerance.tenant_id == tenant_id,
            AllergyIntolerance.deleted_at.is_(None),
        )
        .order_by(
            AllergyIntolerance.clinical_status.asc(),
            AllergyIntolerance.onset_date.desc().nullslast(),
            AllergyIntolerance.created_at.desc(),
        )
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_allergy(
    db: AsyncSession, allergy_id: UUID, tenant_id: UUID
) -> Optional[AllergyIntolerance]:
    """Fetch one patient-instance allergy, tenant-scoped + non-deleted."""
    result = await db.execute(
        select(AllergyIntolerance).where(
            AllergyIntolerance.id == allergy_id,
            AllergyIntolerance.tenant_id == tenant_id,
            AllergyIntolerance.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def add_patient_allergy(
    db: AsyncSession,
    patient_id: UUID,
    tenant_id: UUID,
    data: AllergyIntoleranceCreate,
) -> AllergyIntolerance:
    new_record = AllergyIntolerance(
        patient_id=patient_id,
        tenant_id=tenant_id,
        **data.model_dump(exclude_unset=True),
    )
    # Write-time FHIR gate (audit C13): invalid FHIR never persists.
    assert_valid_fhir(new_record)
    db.add(new_record)
    await db.commit()
    await db.refresh(new_record)
    return new_record


async def update_patient_allergy(
    db: AsyncSession,
    allergy_id: UUID,
    tenant_id: UUID,
    data: AllergyIntoleranceUpdate,
) -> Optional[AllergyIntolerance]:
    result = await db.execute(
        select(AllergyIntolerance).where(
            AllergyIntolerance.id == allergy_id,
            AllergyIntolerance.tenant_id == tenant_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        return None

    update_data = data.model_dump(exclude_unset=True)

    # Merge code partially so a text-only update doesn't wipe catalog_id (and
    # vice versa) — mirrors medication_service.update_patient_medication.
    if "code" in update_data and update_data["code"]:
        current_code = record.code or {}
        record.code = {**current_code, **update_data["code"]}
        del update_data["code"]

    for key, value in update_data.items():
        setattr(record, key, value)

    assert_valid_fhir(record)
    await db.commit()
    await db.refresh(record)
    return record


async def delete_patient_allergy(
    db: AsyncSession, allergy_id: UUID, tenant_id: UUID
) -> bool:
    """Hard-delete the row (parity with medication_service.delete_patient_medication).

    Note: the FHIR R4 facade soft-deletes via ``deleted_at`` (audit C5). This
    domain endpoint removes the row entirely, matching the medications path.
    """
    result = await db.execute(
        select(AllergyIntolerance).where(
            AllergyIntolerance.id == allergy_id,
            AllergyIntolerance.tenant_id == tenant_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        return False
    await db.delete(record)
    await db.commit()
    return True


async def get_active_allergies_by_tenant(
    db: AsyncSession, tenant_id: UUID
) -> List[Dict[str, Any]]:
    """All ACTIVE intolerances in the tenant + the patient display name.

    Used by the dashboard ``AllergyAlertsCard`` and the legacy cross-patient
    alerts feed (now reachable at ``/allergies/active``).
    """
    from app.models.enums import AllergyClinicalStatus
    from app.models.fhir.patient import Patient

    query = (
        select(AllergyIntolerance, Patient.name.label("patient_name"))
        .join(Patient, AllergyIntolerance.patient_id == Patient.id)
        .where(
            AllergyIntolerance.tenant_id == tenant_id,
            AllergyIntolerance.clinical_status == AllergyClinicalStatus.ACTIVE,
            AllergyIntolerance.deleted_at.is_(None),
        )
        .order_by(AllergyIntolerance.criticality.desc())
    )
    result = await db.execute(query)
    rows = result.all()

    output: List[Dict[str, Any]] = []
    for allergy, p_name in rows:
        data = allergy.to_dict()
        data["patient_name_display"] = (
            f"{p_name.get('given', [''])[0]} {p_name.get('family', '')}"
        )
        output.append(data)
    return output


async def get_allergy_usage(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> List[Dict[str, Any]]:
    """All patient intolerances pointing at the given allergy catalog entry.

    Mirrors ``medication_service.get_medication_usage``: drives the "patients
    affected" tab on the allergy detail page and surfaces cross-patient
    impact for clinicians reviewing the allergen.
    """
    from app.models.fhir.patient import Patient

    query = (
        select(AllergyIntolerance, Patient)
        .join(Patient, AllergyIntolerance.patient_id == Patient.id)
        .where(
            AllergyIntolerance.code["catalog_id"].astext == str(catalog_id),
            AllergyIntolerance.tenant_id == tenant_id,
            AllergyIntolerance.deleted_at.is_(None),
        )
    )
    result = await db.execute(query)
    rows = result.all()

    usage: List[Dict[str, Any]] = []
    for allergy, patient in rows:
        usage.append(
            {
                "allergy": allergy.to_dict(),
                "patient": {
                    "id": str(patient.id),
                    "name": patient.name,
                    "mrn": patient.mrn,
                },
            }
        )
    return usage


async def reprocess_allergy(
    db: AsyncSession, catalog_id: UUID, tenant_id: UUID
) -> Optional[AllergyCatalog]:
    """AI re-enrich an existing allergy catalog entry's description and
    typical_reactions.

    Parity with ``medication_service.reprocess_medication``: pulls the catalog
    row, runs the NLP extractor's pass-2 allergen enrichment, and writes the
    enriched fields back. Falls back gracefully (returns the unchanged row)
    when AI is unavailable, the extractor has no allergen enrichment method,
    or it returns nothing. This keeps the endpoint usable today while leaving
    a seam for future NLP enrichment.
    """
    query = select(AllergyCatalog).where(
        AllergyCatalog.id == catalog_id,
        or_(
            AllergyCatalog.tenant_id.is_(None),
            AllergyCatalog.tenant_id == tenant_id,
        ),
    )
    result = await db.execute(query)
    entry = result.scalar_one_or_none()
    if entry is None:
        return None

    try:
        from app.ai.processors.nlp import get_nlp_extractor_from_db

        nlp = await get_nlp_extractor_from_db(
            db, task_type="nlp", tenant_id=tenant_id
        )
    except Exception as exc:
        logger.warning("Allergy reprocess: NLP extractor unavailable (%s).", exc)
        return entry

    # The allergen-enrichment pass is optional on the extractor contract; only
    # invoke it when the configured backend implements it.
    enrich = getattr(nlp, "parse_document_pass_2_allergies", None)
    if enrich is None:
        logger.info(
            "Allergy reprocess: extractor has no allergen enrichment; no-op."
        )
        return entry

    try:
        new_defs = await enrich([{"raw_name": entry.name}])
    except Exception as exc:
        logger.warning("Allergy reprocess: NLP pass-2 raised %s", exc)
        return entry

    definitions = getattr(new_defs, "definitions", None) or []
    if not definitions:
        return entry

    enriched = definitions[0]
    if getattr(enriched, "description", None):
        entry.description = enriched.description
    typical = getattr(enriched, "typical_reactions", None)
    if typical:
        entry.typical_reactions = list(typical)
    category_raw = getattr(enriched, "category", None)
    if category_raw:
        try:
            from app.models.enums import AllergyCategory

            entry.category = AllergyCategory(str(category_raw).upper())
        except (ValueError, KeyError):
            pass

    await db.commit()
    await db.refresh(entry)
    return entry
