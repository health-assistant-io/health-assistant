import json
import datetime
import logging
import sqlalchemy as sa
from uuid import UUID
from typing import List, Dict, Any, Optional, Tuple

from sqlalchemy import select, update, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.fhir.medication import Medication, MedicationCatalog, MedicationStatus
from app.models.fhir.patient import Observation
from app.models.enums import QuantityType, CodingSystem
from app.models.doctor_model import DoctorModel
from app.models.examination_category import ExaminationCategory
from app.schemas.ai_nlp import (
    DocumentEntitiesExtract,
    KnownBiomarkerExtract,
    ExaminationMetadataExtract,
)
from app.services.ai_provider_service import AIProviderService
from app.workers.task_logger import TaskLogger, TaskProgressTracker

logger = logging.getLogger(__name__)


class MedicalProcessingService:
    """Service for processing medical documents and extracting clinical data"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.ai_provider_service = AIProviderService(db)

    async def resolve_category(
        self, category_input: str, tenant_id: UUID
    ) -> ExaminationCategory:
        """Resolve a category slug to an ExaminationCategory entity, creating it if needed"""
        import re

        # Normalize input to slug format (kebab-case)
        # This handles cases where input might be 'Blood Laboratory' or 'blood-laboratory'
        target_slug = re.sub(r"[^a-z0-9]+", "-", category_input.lower()).strip("-")

        # 1. Try to find existing category by slug
        cat_res = await self.db.execute(
            select(ExaminationCategory).where(ExaminationCategory.slug == target_slug)
        )
        category_entity = cat_res.scalars().first()

        if not category_entity:
            # 2. Create new category
            # If the original input had spaces or capitals, it's likely a name
            # Otherwise, it's a slug, so we prettify it for the display name
            if " " in category_input or any(c.isupper() for c in category_input):
                name = category_input
            else:
                name = target_slug.replace("-", " ").title()

            category_entity = ExaminationCategory(
                name=name,
                slug=target_slug,
                tenant_id=tenant_id,
                color="#6b7280",  # Default gray
                icon="more-horizontal",  # Default icon
            )
            self.db.add(category_entity)
            await self.db.flush()

        return category_entity

    async def aggregate_examination_text(
        self, examination_id: UUID
    ) -> Tuple[str, List[DocumentModel]]:
        """Aggregate text from all documents in an examination marked for extraction"""
        result = await self.db.execute(
            select(DocumentModel).where(
                DocumentModel.examination_id == examination_id,
                DocumentModel.include_in_extraction == True,
                DocumentModel.status.in_(["completed", "failed"]),
            )
        )
        docs = result.scalars().all()
        docs_with_text = [
            d for d in docs if d.extracted_text and len(d.extracted_text.strip()) > 0
        ]

        if not docs_with_text:
            return "", []

        cumulative_text = "\n\n--- Document ---\n\n".join(
            [str(d.extracted_text) for d in docs_with_text]
        )
        return cumulative_text, docs_with_text

    async def get_clinical_context(
        self, examination: ExaminationModel
    ) -> Dict[str, Any]:
        """Get clinical context (previous findings, catalogs) for LLM extraction"""
        # 1. Reference Data (previous findings in this exam)
        reference_data = {
            "diagnoses": examination.diagnoses or [],
            "impressions": examination.impressions or "",
            "medications": [m.to_dict() for m in examination.medications],
            "biomarkers": [
                {"name": o.code.get("text"), "value": o.raw_value}
                for o in examination.observations
            ],
        }

        # 2. Catalogs
        bio_defs = await self.db.execute(select(BiomarkerDefinition))
        biomarker_catalog = [
            {"slug": b.slug, "name": b.name, "aliases": b.aliases}
            for b in bio_defs.scalars().all()
        ]

        med_defs = await self.db.execute(select(MedicationCatalog))
        medication_catalog = [
            {"id": str(m.id), "name": m.name} for m in med_defs.scalars().all()
        ]

        return {
            "reference_data": reference_data,
            "biomarker_catalog": biomarker_catalog,
            "medication_catalog": medication_catalog,
        }

    async def extract_examination_metadata(
        self, text: str, tenant_id: UUID, user_id: Optional[UUID] = None
    ) -> Optional[ExaminationMetadataExtract]:
        """Extract high-level examination details from aggregated text"""
        # Fetch existing categories to help the LLM match
        cat_res = await self.db.execute(
            select(ExaminationCategory).where(
                or_(
                    ExaminationCategory.tenant_id == tenant_id,
                    ExaminationCategory.tenant_id.is_(None),
                )
            )
        )
        existing_slugs = [c.slug for c in cat_res.scalars().all()]

        nlp_extractor = await self.ai_provider_service.get_nlp_extractor(
            tenant_id, user_id
        )
        return await nlp_extractor.parse_examination_metadata(
            text, known_categories=existing_slugs
        )

    async def run_extraction_pipeline(
        self,
        examination_id: UUID,
        task_logger: TaskLogger,
        progress_tracker: TaskProgressTracker,
        user_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """Main pipeline orchestration for cumulative extraction"""

        # Fetch Examination
        exam_res = await self.db.execute(
            select(ExaminationModel).where(ExaminationModel.id == examination_id)
        )
        exam = exam_res.scalar_one_or_none()
        if not exam:
            raise ValueError(f"Examination {examination_id} not found")

        tenant_id = exam.tenant_id

        # 0. Ensure examination has a date (fallback to today if missing)
        if not exam.examination_date:
            exam.examination_date = datetime.date.today()
            await self.db.commit()
            await self.db.refresh(exam)
            logger.info(f"Initialized missing date for exam {exam.id} to today.")

        # 1. Aggregate Text
        cumulative_text, docs_with_text = await self.aggregate_examination_text(
            examination_id
        )
        if not cumulative_text:
            await progress_tracker.update_examination_status("completed", 100)
            await task_logger.log_success(message="No text found for analysis")
            return {"status": "completed", "message": "No text found"}

        await progress_tracker.update_examination_status("clinical_analysis", 10)
        await task_logger.log_progress(
            "text_aggregated", 10, num_docs=len(docs_with_text)
        )

        # 1.5. Check if we need to auto-extract metadata (only if the exam is marked or has placeholders)
        if exam.auto_extract_metadata:
            await progress_tracker.update_examination_status("analyzing_metadata", 15)
            await task_logger.log_progress("analyzing_metadata", 15)
            try:
                metadata = await self.extract_examination_metadata(
                    cumulative_text, exam.tenant_id, user_id
                )
                if metadata:
                    # Update exam with extracted metadata
                    if metadata.examination_date:
                        try:
                            exam.examination_date = datetime.datetime.strptime(
                                metadata.examination_date, "%Y-%m-%d"
                            ).date()
                        except ValueError:
                            pass

                    if metadata.category:
                        category_entity = await self.resolve_category(
                            metadata.category, exam.tenant_id
                        )
                        exam.category_id = category_entity.id
                    if metadata.clinical_notes:
                        if exam.notes:
                            exam.notes = f"{exam.notes}\n\nAI Extracted Notes:\n{metadata.clinical_notes}"
                        else:
                            exam.notes = metadata.clinical_notes

                    # Process doctors
                    if metadata.doctor_names:
                        import re

                        for name in metadata.doctor_names:
                            # Clean name: remove "Dr.", "Doctor", "MD", "PhD" etc.
                            # Case-insensitive replacement of common titles
                            clean_name = re.sub(
                                r"^(dr\.?|doctor|prof\.?|professor)\s+",
                                "",
                                name,
                                flags=re.IGNORECASE,
                            )
                            # Remove common suffixes
                            clean_name = re.sub(
                                r",?\s+(md|ph\.?d|do|ms|m\.s\.|msc)\.?$",
                                "",
                                clean_name,
                                flags=re.IGNORECASE,
                            )
                            clean_name = clean_name.strip()

                            if not clean_name:
                                continue

                            # Try to match existing doctor by name
                            doc_res = await self.db.execute(
                                select(DoctorModel).where(
                                    DoctorModel.tenant_id == exam.tenant_id,
                                    or_(
                                        DoctorModel.name.ilike(f"%{clean_name}%"),
                                        DoctorModel.name.ilike(f"%{name}%"),
                                    ),
                                )
                            )
                            existing_doctor = doc_res.scalars().first()
                            if existing_doctor:
                                if existing_doctor not in exam.doctors:
                                    exam.doctors.append(existing_doctor)
                            else:
                                # Create new doctor with cleaned name
                                new_doctor = DoctorModel(
                                    name=clean_name, tenant_id=exam.tenant_id
                                )
                                self.db.add(new_doctor)
                                await self.db.flush()
                                exam.doctors.append(new_doctor)

                    await self.db.commit()
                    await self.db.refresh(exam)
                    await task_logger.log_progress("metadata_updated", 20)
            except Exception as e:
                logger.error(f"Failed to auto-extract metadata: {e}")
                await task_logger.log_error(e, "metadata_extraction")

        # 2. Get Context & NLP Extractor
        context = await self.get_clinical_context(exam)
        nlp_extractor = await self.ai_provider_service.get_nlp_extractor(
            tenant_id, user_id
        )

        await progress_tracker.update_examination_status("clinical_analysis", 25)

        # 3. NLP Pass 1: Extraction & Mapping
        parsed_data = await nlp_extractor.parse_document_pass_1(
            cumulative_text,
            context["biomarker_catalog"],
            context["medication_catalog"],
            reference_data=context["reference_data"],
        )
        await task_logger.log_progress("nlp_pass_1_completed", 45)

        # 4. Pass 2: Ontology Generation for Unknowns
        raw_to_slug_map = {}
        raw_to_med_name_map = {}

        if parsed_data.unknown_biomarkers:
            await progress_tracker.update_examination_status("defining_ontology", 50)
            await self._process_unknown_biomarkers(
                parsed_data.unknown_biomarkers,
                nlp_extractor,
                tenant_id,
                raw_to_slug_map,
            )
            await self.db.commit()

        if parsed_data.unknown_medications:
            await self._process_unknown_medications(
                parsed_data.unknown_medications,
                nlp_extractor,
                tenant_id,
                raw_to_med_name_map,
            )
            await self.db.commit()

        # 5. Persistence
        await progress_tracker.update_examination_status("persisting_results", 85)
        await self._persist_results(
            exam, parsed_data, docs_with_text, raw_to_slug_map, raw_to_med_name_map
        )

        # 6. Final Update
        await self.db.execute(
            update(ExaminationModel)
            .where(ExaminationModel.id == examination_id)
            .values(
                diagnoses=parsed_data.diagnoses,
                impressions=parsed_data.impressions,
                extraction_status="completed",
                extraction_progress=100,
                error_message=None,
            )
        )
        await self.db.commit()
        await task_logger.log_success()

        return {"status": "completed"}

    async def _process_unknown_biomarkers(
        self, unknown_bios, nlp_extractor, tenant_id, slug_map
    ):
        new_bio_defs = await nlp_extractor.parse_document_pass_2_biomarkers(
            unknown_bios
        )

        # Pre-fetch units
        unit_res = await self.db.execute(select(Unit))
        unit_map = {u.symbol.lower(): u for u in unit_res.scalars().all()}

        for def_data in new_bio_defs.definitions:
            slug_map[def_data.raw_name_match] = def_data.proposed_slug

            existing = await self.db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.slug == def_data.proposed_slug
                )
            )
            if not existing.scalar_one_or_none():
                preferred_unit_id = None
                if def_data.preferred_unit_symbol:
                    u_lower = def_data.preferred_unit_symbol.lower()
                    if u_lower in unit_map:
                        preferred_unit_id = unit_map[u_lower].id
                    else:
                        new_unit = Unit(
                            symbol=def_data.preferred_unit_symbol,
                            name=def_data.preferred_unit_symbol,
                            quantity_type=QuantityType.OTHER,
                            conversion_multiplier=1.0,
                        )
                        self.db.add(new_unit)
                        await self.db.flush()
                        unit_map[u_lower] = new_unit
                        preferred_unit_id = new_unit.id

                self.db.add(
                    BiomarkerDefinition(
                        slug=def_data.proposed_slug,
                        coding_system=def_data.proposed_coding_system,
                        code=def_data.proposed_code,
                        name=def_data.name,
                        category=def_data.category,
                        aliases=def_data.suggested_aliases,
                        reference_range_min=def_data.reference_range_min,
                        reference_range_max=def_data.reference_range_max,
                        preferred_unit_id=preferred_unit_id,
                        info=def_data.info,
                        tenant_id=tenant_id,
                    )
                )

    async def _process_unknown_medications(
        self, unknown_meds, nlp_extractor, tenant_id, name_map
    ):
        new_med_defs = await nlp_extractor.parse_document_pass_2_medications(
            unknown_meds
        )
        for def_data in new_med_defs.definitions:
            name_map[def_data.raw_name_match] = def_data.name
            existing = await self.db.execute(
                select(MedicationCatalog).where(
                    MedicationCatalog.name.ilike(def_data.name)
                )
            )
            if not existing.scalar_one_or_none():
                self.db.add(
                    MedicationCatalog(
                        name=def_data.name,
                        description=def_data.description,
                        indications=def_data.indications,
                        side_effects=def_data.side_effects,
                        contraindications=def_data.contraindications,
                        dosage_info=def_data.dosage_info,
                        tenant_id=tenant_id,
                    )
                )

    async def _persist_results(
        self, exam, parsed_data, docs_with_text, slug_map, med_name_map
    ):
        # Clear existing
        await self.db.execute(
            delete(Observation).where(Observation.examination_id == exam.id)
        )
        await self.db.execute(
            delete(Medication).where(Medication.examination_id == exam.id)
        )

        # Refresh maps
        bio_res = await self.db.execute(select(BiomarkerDefinition))
        bio_map = {b.slug: b for b in bio_res.scalars().all()}
        med_res = await self.db.execute(select(MedicationCatalog))
        med_map = {m.name.lower(): m for m in med_res.scalars().all()}
        unit_res = await self.db.execute(select(Unit))
        unit_map = {u.symbol.lower(): u for u in unit_res.scalars().all()}

        patient_ref = f"Patient/{exam.patient_id}"

        # Examination date is guaranteed to be set at the start of the pipeline
        eff_date = datetime.datetime.combine(
            exam.examination_date or datetime.date.today(), datetime.time.min
        )

        # Save Biomarkers
        for b in parsed_data.known_biomarkers:
            source_doc_id = self._find_source_doc(b.name, docs_with_text)
            await self._save_observation(
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
            source_doc_id = self._find_source_doc(b.raw_name, docs_with_text)

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
            await self._save_observation(
                wrapped, target, unit_map, exam, patient_ref, eff_date, source_doc_id
            )

        # Save Medications
        for m in parsed_data.known_medications + parsed_data.unknown_medications:
            name = m.name if hasattr(m, "name") else m.raw_name
            mapped_name = med_name_map.get(name, name)
            catalog_item = med_map.get(mapped_name.lower())
            self.db.add(
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

    def _find_source_doc(
        self, text_to_find: str, docs: List[DocumentModel]
    ) -> Optional[str]:
        if not docs:
            return None
        for d in docs:
            if text_to_find.lower() in str(d.extracted_text).lower():
                return str(d.id)
        return None

    async def _save_observation(
        self,
        b: KnownBiomarkerExtract,
        target_bio: Optional[BiomarkerDefinition],
        units_by_symbol: Dict[str, Unit],
        exam: ExaminationModel,
        patient_ref: str,
        effective_date: datetime.datetime,
        document_id: Optional[str] = None,
    ):
        val_float = b.value
        biomarker_id = target_bio.id if target_bio else None
        unit_symbol = b.unit_symbol
        raw_unit_id = None
        normalized_val = val_float

        if unit_symbol:
            unit_lower = unit_symbol.lower()
            if unit_lower in units_by_symbol:
                matched_unit = units_by_symbol[unit_lower]
                raw_unit_id = matched_unit.id
                if (
                    target_bio
                    and target_bio.preferred_unit_id
                    and str(target_bio.preferred_unit_id) != str(matched_unit.id)
                ):
                    normalized_val = val_float * matched_unit.conversion_multiplier
            else:
                new_unit = Unit(
                    symbol=unit_symbol,
                    name=unit_symbol,
                    quantity_type=QuantityType.OTHER,
                    conversion_multiplier=1.0,
                )
                self.db.add(new_unit)
                await self.db.flush()
                units_by_symbol[unit_lower] = new_unit
                raw_unit_id = new_unit.id

        lab_ref_range = (
            {"min": b.reference_range_min, "max": b.reference_range_max}
            if b.reference_range_min or b.reference_range_max
            else None
        )
        coding = []
        if target_bio:
            coding.append({
                "system": "http://loinc.org" if target_bio.coding_system == CodingSystem.LOINC else "http://snomed.info/sct" if target_bio.coding_system == CodingSystem.SNOMED else "urn:uuid:health-assistant:custom-biomarker",
                "code": target_bio.code or target_bio.slug,
                "display": target_bio.name
            })
            
        self.db.add(
            Observation(
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
                lab_reference_range=lab_ref_range,
                method=b.method,
                interpretation=b.interpretation_flag,
            )
        )
