"""Persistence of LLM extraction results: Observations + Medications.

Extracted from ``MedicalProcessingService`` (Phase 5b). The write path wraps
its delete + recreate in a nested transaction (SAVEPOINT, audit item C2) so a
failure during re-extraction rolls back to the pre-delete state instead of
permanently shrinking the patient's clinical record.

Public functions:
  * :func:`persist_results`  — the savepoint-scoped delete + recreate.
  * :func:`save_observation` — builds + writes one FHIR Observation (with
    unit normalization, FHIR coding, write-time FHIR gate).
  * :func:`find_source_doc`  — links a biomarker to the document whose text
    mentions it.

``MedicalProcessingService._persist_results`` is kept as a thin delegate so
the savepoint regression tests (``inst._persist_results(...)`` on a
``__new__`` instance with only ``db`` set) keep working unchanged.
"""

import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.nlp import KnownBiomarkerExtract
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.enums import CodingSystem, QuantityType
from app.models.fhir.medication import Medication, MedicationCatalog, MedicationStatus
from app.models.fhir.patient import Observation
from app.models.document_model import DocumentModel
from app.services.fhir_helpers import (
    FhirSerializationError,
    _normalize_interpretation,
    assert_valid_fhir,
)

logger = logging.getLogger(__name__)

_OBS_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"


def _fhir_observation_category(concept=None) -> List[Dict[str, Any]]:
    """Build a canonical FHIR ``category`` list from a biomarker's class concept.

    The FHIR observation-category code is stored on the concept itself in
    ``meta_data["fhir_observation_category"]`` (seeded via ``concepts.json``,
    editable via the Catalogs workspace at ``/catalogs?type=concept``). Falls
    back to ``"laboratory"`` when the concept or the meta_data key is missing.
    """
    code = "laboratory"
    if concept and concept.meta_data:
        code = concept.meta_data.get("fhir_observation_category", "laboratory")
    return [
        {
            "coding": [
                {
                    "system": _OBS_CATEGORY_SYSTEM,
                    "code": code,
                    "display": code.replace("-", " ").title(),
                }
            ]
        }
    ]


async def persist_results(
    db: AsyncSession,
    exam,
    parsed_data,
    docs_with_text: List[DocumentModel],
    slug_map: Dict[str, Any],
    med_name_map: Dict[str, Any],
) -> None:
    """Persist the LLM extraction results for an examination.

    Audit item C2 (data loss): the previous implementation deleted ALL of the
    exam's existing Observations + Medications BEFORE running the LLM
    re-extraction. If re-extraction produced fewer or invalid observations,
    previously-correct data was permanently gone — no savepoint, no provenance,
    no audit log. We now wrap the delete + recreate in a nested transaction
    (savepoint) so any failure during re-extraction rolls back to the pre-delete
    state. The caller's outer transaction is unaffected.
    """
    # Savepoint: any exception inside this block releases the savepoint with a
    # rollback, restoring the prior Observations + Medications. The outer
    # transaction stays open for the caller to handle the error.
    async with db.begin_nested():
        # Clear existing
        await db.execute(
            delete(Observation).where(Observation.examination_id == exam.id)
        )
        await db.execute(delete(Medication).where(Medication.examination_id == exam.id))

        # Refresh maps
        bio_res = await db.execute(select(BiomarkerDefinition))
        bio_map = {b.slug: b for b in bio_res.scalars().all()}
        med_res = await db.execute(select(MedicationCatalog))
        med_map = {m.name.lower(): m for m in med_res.scalars().all()}
        unit_res = await db.execute(select(Unit))
        unit_map = {u.symbol.lower(): u for u in unit_res.scalars().all()}

        patient_ref = f"Patient/{exam.patient_id}"

        # Examination date is guaranteed to be set at the start of the pipeline
        eff_date = datetime.datetime.combine(
            exam.examination_date or datetime.date.today(),
            datetime.time.min,
            tzinfo=datetime.timezone.utc,
        )

        # Save Biomarkers
        for b in parsed_data.known_biomarkers:
            source_doc_id = find_source_doc(b.name, docs_with_text)
            await save_observation(
                db,
                b,
                bio_map.get(b.matched_slug),
                unit_map,
                exam,
                patient_ref,
                eff_date,
                source_doc_id,
            )

        for b in parsed_data.unknown_biomarkers:
            slug = slug_map.get(b.raw_name)
            target = bio_map.get(slug) or next(
                (
                    bio
                    for bio in bio_map.values()
                    if bio.name.lower() == b.raw_name.lower()
                ),
                None,
            )
            source_doc_id = find_source_doc(b.raw_name, docs_with_text)

            wrapped = KnownBiomarkerExtract(
                name=b.raw_name,
                matched_slug=target.slug if target else "unknown",
                value=b.value,
                unit_symbol=b.unit_symbol,
                method=b.method,
                reference_range_min=b.reference_range_min,
                reference_range_max=b.reference_range_max,
                interpretation_flag=b.interpretation_flag,
            )
            await save_observation(
                db,
                wrapped,
                target,
                unit_map,
                exam,
                patient_ref,
                eff_date,
                source_doc_id,
            )

        # Save Medications
        for m in parsed_data.known_medications + parsed_data.unknown_medications:
            name = m.name if hasattr(m, "name") else m.raw_name
            mapped_name = med_name_map.get(name, name)
            catalog_item = med_map.get(mapped_name.lower())
            db.add(
                Medication(
                    patient_id=exam.patient_id,
                    tenant_id=exam.tenant_id,
                    examination_id=exam.id,
                    status=MedicationStatus.ACTIVE,
                    code={
                        "text": name,
                        "catalog_id": str(catalog_item.id) if catalog_item else None,
                    },
                    dosage=m.dosage,
                    reason=m.reason,
                    subject={"reference": patient_ref},
                    start_date=exam.examination_date,
                )
            )
    # end begin_nested — savepoint released cleanly, outer txn continues


def find_source_doc(text_to_find: str, docs: List[DocumentModel]) -> Optional[str]:
    if not docs:
        return None
    for d in docs:
        if text_to_find.lower() in str(d.extracted_text).lower():
            return str(d.id)
    return None


async def save_observation(
    db: AsyncSession,
    b: KnownBiomarkerExtract,
    target_bio: Optional[BiomarkerDefinition],
    units_by_symbol: Dict[str, Unit],
    exam,
    patient_ref: str,
    effective_date: datetime.datetime,
    document_id: Optional[str] = None,
) -> None:
    val_float = b.value
    biomarker_id = target_bio.id if target_bio else None
    unit_symbol = b.unit_symbol
    raw_unit_id = None
    # Default normalized_value to the raw value; refined below if we have
    # enough unit information to convert. The previous code only normalized
    # when the biomarker had a preferred unit *different* from the matched
    # unit, leaving auto-created biomarkers (no preferred unit) with mixed-
    # unit trends across labs. Now we always normalize at least to the
    # base SI unit so trends are consistent.
    normalized_val = val_float

    # --- relative_score ---------------------------------------------------
    # Mirror ObservationBuilder.build() (integrations path): the value's
    # position within the reference range as a [0.0, 1.0] float. The
    # previous OCR path never set relative_score, leaving the biomarker
    # engine's flagship scoring feature silently disabled for OCR data.
    relative_score: Optional[float] = None
    ref_min = b.reference_range_min
    ref_max = b.reference_range_max
    if (
        val_float is not None
        and ref_min is not None
        and ref_max is not None
        and ref_max > ref_min
    ):
        relative_score = (val_float - ref_min) / (ref_max - ref_min)
        relative_score = max(0.0, min(1.0, relative_score))
    elif ref_min is not None or ref_max is not None:
        # Incomplete range — middle score, matches ObservationBuilder.
        relative_score = 0.5

    if unit_symbol:
        unit_lower = unit_symbol.lower()
        if unit_lower in units_by_symbol:
            matched_unit = units_by_symbol[unit_lower]
            raw_unit_id = matched_unit.id
            # Convert raw value -> base SI unit. matched_unit.conversion_multiplier
            # is "multiply this unit's value by this number to get the base unit".
            base_value = val_float * matched_unit.conversion_multiplier
            # Express normalized_value in the biomarker's preferred unit
            # (fall back to base SI when no preferred unit is set, so trends
            # are at least consistent across labs reporting in different raw
            # units). The previous code skipped normalization entirely when
            # no preferred_unit_id was set, AND used the wrong direction
            # (raw * raw_mult = base, then labeled it as-preferred).
            preferred_unit: Optional[Unit] = None
            if target_bio and target_bio.preferred_unit_id:
                # Look up the preferred Unit object. units_by_symbol is the
                # only Unit collection passed in; scan it once (small set).
                for u in units_by_symbol.values():
                    if str(u.id) == str(target_bio.preferred_unit_id):
                        preferred_unit = u
                        break
            if preferred_unit is not None and preferred_unit.conversion_multiplier:
                normalized_val = base_value / preferred_unit.conversion_multiplier
            else:
                normalized_val = base_value
        else:
            new_unit = Unit(
                symbol=unit_symbol,
                name=unit_symbol,
                quantity_type=QuantityType.OTHER,
                conversion_multiplier=1.0,
            )
            db.add(new_unit)
            await db.flush()
            units_by_symbol[unit_lower] = new_unit
            raw_unit_id = new_unit.id

    lab_ref_range = (
        {"min": b.reference_range_min, "max": b.reference_range_max}
        if b.reference_range_min or b.reference_range_max
        else None
    )
    coding = []
    if target_bio:
        coding.append(
            {
                "system": target_bio.coding_system.fhir_system
                if target_bio.coding_system
                else CodingSystem.CUSTOM.fhir_system,
                "code": target_bio.code or target_bio.slug,
                "display": target_bio.name,
            }
        )

    obs = Observation(
        examination_id=exam.id,
        document_id=document_id,
        tenant_id=exam.tenant_id,
        status="final",
        code={"coding": coding, "text": b.name},
        subject={"reference": patient_ref},
        effective_datetime=effective_date,
        value_quantity={"value": val_float, "unit": unit_symbol},
        biomarker_id=biomarker_id,
        raw_value=val_float,
        raw_unit_id=raw_unit_id,
        normalized_value=normalized_val,
        relative_score=relative_score,
        lab_reference_range=lab_ref_range,
        method=b.method,
        interpretation=_normalize_interpretation(b.interpretation_flag),
        category=_fhir_observation_category(
            target_bio.class_concept if target_bio else None
        ),
    )
    # Write-time FHIR gate: never persist an Observation that cannot be
    # projected to valid FHIR. Skip-and-log so one bad row can't abort the
    # whole document extraction.
    try:
        assert_valid_fhir(obs)
    except FhirSerializationError as e:
        logger.warning("Skipping invalid OCR observation for %s: %s", b.name, e)
        return
    db.add(obs)
