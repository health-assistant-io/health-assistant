import json
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import logging
from sqlalchemy import select
from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance
from app.core.database import AsyncSessionLocal
from app.services.fhir_helpers import assert_valid_fhir


logger = logging.getLogger(__name__)


def _parse_iso_datetime(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        # Handle Z suffix for UTC if present
        if isinstance(date_str, str) and date_str.endswith("Z"):
            date_str = date_str.replace("Z", "+00:00")

        dt = None
        if isinstance(date_str, datetime):
            dt = date_str
        else:
            dt = datetime.fromisoformat(date_str)

        # Convert to naive UTC if it has timezone info
        # because the database columns are currently TIMESTAMP WITHOUT TIME ZONE
        if dt.tzinfo is not None:
            dt = dt.astimezone(None).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def _ensure_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


async def list_allergy_catalog(
    search: Optional[str] = None, tenant_id: Optional[UUID] = None
) -> List[AllergyCatalog]:
    from app.services.catalog_search_service import search_allergies

    # Delegate to the unified catalog search service (trigram + ilike fallback,
    # tenant-scoped, ranked). This service manages its own session; we accept
    # the previous signature (no db arg) for back-compat.
    if tenant_id is None:
        # No tenant context: fall back to a plain unranked listing of globals.
        async with AsyncSessionLocal() as db:
            query = select(AllergyCatalog).where(AllergyCatalog.tenant_id == None)
            if search:
                query = query.where(AllergyCatalog.name.ilike(f"%{search}%"))
            result = await db.execute(query.order_by(AllergyCatalog.name.asc()))
            return result.scalars().all()

    async with AsyncSessionLocal() as db:
        return await search_allergies(db, tenant_id, search)


async def get_active_allergies_by_tenant(tenant_id: UUID) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        from app.models.fhir.allergy import AllergyClinicalStatus
        from app.models.fhir.patient import Patient

        query = (
            select(AllergyIntolerance, Patient.name.label("patient_name"))
            .join(Patient, AllergyIntolerance.patient_id == Patient.id)
            .where(
                AllergyIntolerance.tenant_id == tenant_id,
                AllergyIntolerance.clinical_status == AllergyClinicalStatus.ACTIVE,
            )
            .order_by(AllergyIntolerance.criticality.desc())
        )

        result = await db.execute(query)
        rows = result.all()

        output = []
        for allergy, p_name in rows:
            data = allergy.to_dict()
            data["patient_name_display"] = (
                f"{p_name.get('given', [''])[0]} {p_name.get('family', '')}"
            )
            output.append(data)

        return output


async def add_to_catalog(
    name: str, category: str, tenant_id: UUID, description: Optional[str] = None
) -> AllergyCatalog:
    async with AsyncSessionLocal() as db:
        new_entry = AllergyCatalog(
            name=name, category=category, description=description, tenant_id=tenant_id
        )
        db.add(new_entry)
        await db.commit()
        await db.refresh(new_entry)
        return new_entry


async def get_patient_allergies(
    patient_id: UUID, tenant_id: UUID
) -> List[AllergyIntolerance]:
    async with AsyncSessionLocal() as db:
        query = (
            select(AllergyIntolerance)
            .where(
                AllergyIntolerance.patient_id == patient_id,
                AllergyIntolerance.tenant_id == tenant_id,
            )
            .order_by(
                AllergyIntolerance.clinical_status.asc(),
                AllergyIntolerance.onset_date.desc(),
            )
        )

        result = await db.execute(query)
        return result.scalars().all()


async def add_patient_allergy(
    patient_id: UUID, tenant_id: UUID, data: Dict[str, Any]
) -> AllergyIntolerance:
    async with AsyncSessionLocal() as db:
        new_allergy = AllergyIntolerance(
            patient_id=patient_id,
            tenant_id=tenant_id,
            clinical_status=data.get("clinical_status", "ACTIVE"),
            category=data.get("category"),
            criticality=data.get("criticality"),
            code=_ensure_json(data.get("code")),  # {"text": "...", "catalog_id": "..."}
            onset_date=_parse_iso_datetime(data.get("onset_date")),
            resolved_date=_parse_iso_datetime(data.get("resolved_date")),
            last_occurrence=_parse_iso_datetime(data.get("last_occurrence")),
            note=data.get("note"),
            reactions=_ensure_json(data.get("reactions", [])),
        )
        # Write-time FHIR validation gate: invalid FHIR can never persist via
        # this path. Parity with fhir_service's gate for Observation/Medication/
        # DiagnosticReport.
        assert_valid_fhir(new_allergy)
        db.add(new_allergy)
        await db.commit()
        await db.refresh(new_allergy)
        return new_allergy


async def update_patient_allergy(
    allergy_id: UUID, tenant_id: UUID, data: Dict[str, Any]
) -> Optional[AllergyIntolerance]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AllergyIntolerance).where(
                AllergyIntolerance.id == allergy_id,
                AllergyIntolerance.tenant_id == tenant_id,
            )
        )
        allergy = result.scalar_one_or_none()

        if not allergy:
            return None

        # Handle specific fields
        date_fields = ["onset_date", "resolved_date", "last_occurrence"]
        json_fields = ["code", "reactions"]

        for key, value in data.items():
            if hasattr(allergy, key):
                if key in date_fields:
                    setattr(allergy, key, _parse_iso_datetime(value))
                elif key in json_fields:
                    setattr(allergy, key, _ensure_json(value))
                else:
                    setattr(allergy, key, value)

        # Validate the projected FHIR shape after mutation, before commit.
        # Catches malformed JSON in code/reactions that would otherwise only
        # surface at export time.
        assert_valid_fhir(allergy)
        await db.commit()
        await db.refresh(allergy)
        return allergy


async def delete_patient_allergy(allergy_id: UUID, tenant_id: UUID) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AllergyIntolerance).where(
                AllergyIntolerance.id == allergy_id,
                AllergyIntolerance.tenant_id == tenant_id,
            )
        )
        allergy = result.scalar_one_or_none()

        if not allergy:
            return False

        await db.delete(allergy)
        await db.commit()
        return True
