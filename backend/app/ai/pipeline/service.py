import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.pipeline.ontology import (
    process_unknown_biomarkers,
    process_unknown_medications,
)
from app.ai.pipeline.persistence import persist_results
from app.ai.providers.service import AIProviderService
from app.ai.schemas.nlp import ExaminationMetadataExtract
from app.models.biomarker_model import BiomarkerDefinition
from app.models.doctor_model import DoctorModel
from app.models.document_model import DocumentModel
from app.models.examination_category import ExaminationCategory
from app.models.examination_model import ExaminationModel
from app.models.fhir.medication import MedicationCatalog
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

    # ------------------------------------------------------------------
    # Phase 5b delegates.
    #
    # The ontology + persistence implementations live in ontology.py and
    # persistence.py. These stay as thin methods so the orchestrator's
    # ``self._process_unknown_biomarkers(...)`` / ``self._persist_results(...)``
    # calls keep working unchanged, AND so the savepoint regression tests
    # (``inst._persist_results(...)`` on a ``__new__`` instance with only
    # ``db`` set) keep passing. ``_find_source_doc`` / ``_save_observation``
    # are internal to ``persist_results`` and are NOT exposed as delegates.
    # ------------------------------------------------------------------

    async def _process_unknown_biomarkers(
        self, unknown_bios, nlp_extractor, tenant_id, slug_map
    ):
        await process_unknown_biomarkers(
            self.db, unknown_bios, nlp_extractor, tenant_id, slug_map
        )

    async def _process_unknown_medications(
        self, unknown_meds, nlp_extractor, tenant_id, name_map
    ):
        await process_unknown_medications(
            self.db, unknown_meds, nlp_extractor, tenant_id, name_map
        )

    async def _persist_results(
        self, exam, parsed_data, docs_with_text, slug_map, med_name_map
    ):
        """Persist LLM extraction results (Observations + Medications).

        Delegates to :func:`app.ai.pipeline.persistence.persist_results`, which
        wraps the delete + recreate in a SAVEPOINT (audit item C2) so a failure
        during re-extraction rolls back to the pre-delete state.
        """
        await persist_results(
            self.db, exam, parsed_data, docs_with_text, slug_map, med_name_map
        )
