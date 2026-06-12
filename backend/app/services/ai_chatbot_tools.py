import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import UUID
from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.tools import tool

from app.models.fhir.patient import Observation, Patient
from app.models.fhir.medication import Medication, MedicationCatalog
from sqlalchemy.orm import selectinload
from app.models.examination_model import ExaminationModel
from app.models.alert_model import AlertModel
from app.models.document_model import DocumentModel
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import ClinicalEvent, ClinicalEventType, EventExaminationLink, EventObservationLink


class ChatbotTools:
    def __init__(self, db: AsyncSession, tenant_id: UUID, patient_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.patient_id = patient_id

    def get_tools(self):
        """Returns a list of tools bound to the current context"""

        @tool
        async def get_document_content(document_id: str) -> str:
            """Fetch the full extracted text content of a specific document (e.g., a lab report or clinical note).
            Use this to read detailed findings that aren't available in the structured summary."""
            try:
                doc_uuid = UUID(document_id)
            except ValueError:
                return "Invalid document ID format."

            result = await self.db.execute(
                select(DocumentModel).where(
                    and_(
                        DocumentModel.id == doc_uuid,
                        DocumentModel.tenant_id == self.tenant_id,
                        DocumentModel.patient_id == self.patient_id,
                    )
                )
            )
            doc = result.scalars().first()
            if not doc:
                return "Document not found or access denied."

            if not doc.extracted_text:
                return f"Document '{doc.filename}' has no extracted text content (Status: {doc.status})."

            return json.dumps(
                {
                    "id": str(doc.id),
                    "filename": doc.filename,
                    "content": doc.extracted_text,
                }
            )

        @tool
        async def get_system_time() -> str:
            """Get the current system date and time. Use this to provide context for relative dates like 'today' or 'yesterday'."""
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        @tool
        async def get_patient_summary() -> str:
            """Fetch a high-level summary of the patient's profile."""
            result = await self.db.execute(
                select(Patient).where(
                    and_(
                        Patient.id == self.patient_id,
                        Patient.tenant_id == self.tenant_id,
                    )
                )
            )
            patient = result.scalars().first()
            if not patient:
                return "Patient not found."

            data = patient.to_dict()
            # Remove heavy UI config fields from AI context
            data.pop("dashboard_layout", None)
            return json.dumps(data)

        @tool
        async def get_recent_biomarkers(limit: int = 15) -> str:
            """Fetch the most recent biomarker observations (lab results) for the patient.
            Returns a list of results with values, units, and interpretations."""
            patient_ref = f"Patient/{self.patient_id}"
            result = await self.db.execute(
                select(Observation)
                .where(
                    and_(
                        Observation.subject["reference"].astext == patient_ref,
                        Observation.tenant_id == self.tenant_id,
                    )
                )
                .order_by(desc(Observation.effective_datetime))
                .limit(limit)
            )
            observations = result.scalars().all()

            # Lightweight mapping: avoid repeating heavy biomarker_info
            summary = []
            for obs in observations:
                summary.append(
                    {
                        "id": str(obs.id),
                        "biomarker_id": str(obs.biomarker_id)
                        if obs.biomarker_id
                        else None,
                        "date": obs.effective_datetime.isoformat()
                        if obs.effective_datetime
                        else None,
                        "name": obs.code.get("text"),
                        "value": obs.value_quantity.get("value")
                        if obs.value_quantity
                        else obs.value_string,
                        "unit": obs.value_quantity.get("unit")
                        if obs.value_quantity
                        else None,
                        "interpretation": obs.interpretation,
                        "biomarker_slug": obs.biomarker.slug if obs.biomarker else None,
                    }
                )

            return json.dumps(summary)

        @tool
        async def get_biomarker_history(biomarker_slug: str, limit: int = 10) -> str:
            """Fetch the historical trend for a specific biomarker using its slug (e.g., 'glucose', 'hemoglobin')."""
            patient_ref = f"Patient/{self.patient_id}"
            result = await self.db.execute(
                select(Observation)
                .where(
                    and_(
                        Observation.subject["reference"].astext == patient_ref,
                        Observation.tenant_id == self.tenant_id,
                        Observation.biomarker.has(slug=biomarker_slug),
                    )
                )
                .order_by(desc(Observation.effective_datetime))
                .limit(limit)
            )
            observations = result.scalars().all()

            history = []
            for obs in observations:
                history.append(
                    {
                        "id": str(obs.id),
                        "biomarker_id": str(obs.biomarker_id)
                        if obs.biomarker_id
                        else None,
                        "date": obs.effective_datetime.isoformat()
                        if obs.effective_datetime
                        else None,
                        "name": obs.code.get("text"),
                        "value": obs.value_quantity.get("value")
                        if obs.value_quantity
                        else obs.value_string,
                        "unit": obs.value_quantity.get("unit")
                        if obs.value_quantity
                        else None,
                        "interpretation": obs.interpretation,
                    }
                )
            return json.dumps(history)

        @tool
        async def get_recent_examinations(limit: int = 5) -> str:
            """Fetch a list of recent clinical examinations/visits for the patient.
            Returns exam dates, categories, and summary notes."""
            result = await self.db.execute(
                select(ExaminationModel)
                .options(selectinload(ExaminationModel.category_entity))
                .where(
                    and_(
                        ExaminationModel.patient_id == self.patient_id,
                        ExaminationModel.tenant_id == self.tenant_id,
                    )
                )
                .order_by(desc(ExaminationModel.examination_date))
                .limit(limit)
            )
            exams = result.scalars().all()

            # Lightweight mapping: avoid deeply nested observations/medications
            summary = []
            for exam in exams:
                summary.append(
                    {
                        "id": str(exam.id),
                        "date": exam.examination_date.isoformat()
                        if exam.examination_date
                        else None,
                        "category": exam.category_entity.name
                        if exam.category_entity
                        else None,
                        "notes": exam.notes[:500]
                        if exam.notes
                        else None,  # Truncate long notes
                        "diagnoses": exam.diagnoses,
                    }
                )
            return json.dumps(summary)

        @tool
        async def get_current_medications() -> str:
            """Fetch the list of medications currently prescribed to the patient."""
            from app.models.enums import MedicationStatus
            result = await self.db.execute(
                select(Medication).where(
                    and_(
                        Medication.patient_id == self.patient_id,
                        Medication.tenant_id == self.tenant_id,
                        Medication.status == MedicationStatus.ACTIVE,
                    )
                )
            )
            meds = result.scalars().all()

            summary = []
            for med in meds:
                summary.append(
                    {
                        "id": str(med.id),
                        "name": med.code.get("text"),
                        "dosage": med.dosage,
                        "frequency": med.frequency,
                        "start_date": med.start_date.isoformat()
                        if med.start_date
                        else None,
                        "reason": med.reason,
                    }
                )
            return json.dumps(summary)

        @tool
        async def get_examination_details(examination_id: str) -> str:
            """Fetch comprehensive details of a specific examination (clinical visit).
            Returns notes, diagnoses, impressions, and lists of associated biomarkers and medications."""
            try:
                exam_uuid = UUID(examination_id)
            except ValueError:
                return "Invalid examination ID format."

            result = await self.db.execute(
                select(ExaminationModel)
                .options(selectinload(ExaminationModel.category_entity))
                .where(
                    and_(
                        ExaminationModel.id == exam_uuid,
                        ExaminationModel.tenant_id == self.tenant_id,
                        ExaminationModel.patient_id == self.patient_id,
                    )
                )
            )
            exam = result.scalars().first()
            if not exam:
                return "Examination not found or access denied."

            # Map the core examination data
            summary = {
                "id": str(exam.id),
                "date": exam.examination_date.isoformat()
                if exam.examination_date
                else None,
                "category": exam.category_entity.name if exam.category_entity else None,
                "notes": exam.notes,
                "patient_notes": exam.patient_notes,
                "diagnoses": exam.diagnoses,
                "impressions": exam.impressions,
                "biomarkers": [],
                "medications": [],
                "documents": [],
            }

            # Map associated documents
            for doc in exam.documents:
                summary["documents"].append(
                    {
                        "id": str(doc.id),
                        "filename": doc.filename,
                        "status": doc.status,
                    }
                )

            # Map associated biomarkers (Observations)
            # Limit to 40 for efficiency in the context window
            for obs in exam.observations[:40]:
                summary["biomarkers"].append(
                    {
                        "id": str(obs.id),
                        "biomarker_id": str(obs.biomarker_id)
                        if obs.biomarker_id
                        else None,
                        "name": obs.code.get("text"),
                        "value": obs.value_quantity.get("value")
                        if obs.value_quantity
                        else obs.value_string,
                        "unit": obs.value_quantity.get("unit")
                        if obs.value_quantity
                        else None,
                        "interpretation": obs.interpretation,
                        "date": obs.effective_datetime.isoformat()
                        if obs.effective_datetime
                        else None,
                    }
                )

            # Map associated medications
            for med in exam.medications:
                summary["medications"].append(
                    {
                        "id": str(med.id),
                        "name": med.code.get("text"),
                        "status": med.status.value if med.status else None,
                        "dosage": med.dosage,
                        "frequency": med.frequency,
                        "reason": med.reason,
                    }
                )

            return json.dumps(summary)

        @tool
        async def get_patient_medication_history(limit: int = 20) -> str:
            """Fetch the historical list of all medications prescribed to the patient, including inactive or completed ones."""
            result = await self.db.execute(
                select(Medication)
                .where(
                    and_(
                        Medication.patient_id == self.patient_id,
                        Medication.tenant_id == self.tenant_id,
                    )
                )
                .order_by(desc(Medication.start_date))
                .limit(limit)
            )
            meds = result.scalars().all()

            history = []
            for med in meds:
                history.append(
                    {
                        "id": str(med.id),
                        "name": med.code.get("text"),
                        "status": med.status.value if med.status else "unknown",
                        "dosage": med.dosage,
                        "frequency": med.frequency,
                        "start_date": med.start_date.isoformat()
                        if med.start_date
                        else None,
                        "end_date": med.end_date.isoformat() if med.end_date else None,
                        "reason": med.reason,
                    }
                )
            return json.dumps(history)

        @tool
        async def update_examination_notes(examination_id: str, notes: str) -> str:
            """Update the clinician notes for a specific examination."""
            result = await self.db.execute(
                select(ExaminationModel).where(
                    and_(
                        ExaminationModel.id == UUID(examination_id),
                        ExaminationModel.tenant_id == self.tenant_id,
                        ExaminationModel.patient_id == self.patient_id,
                    )
                )
            )
            exam = result.scalars().first()
            if not exam:
                return "Examination not found or access denied."

            exam.notes = notes
            await self.db.commit()
            return f"Successfully updated notes for examination on {exam.examination_date}."

        @tool
        async def get_patient_alerts() -> str:
            """Fetch active clinical alerts and monitoring thresholds for the patient."""
            result = await self.db.execute(
                select(AlertModel).where(
                    and_(
                        AlertModel.patient_id == self.patient_id,
                        AlertModel.tenant_id == self.tenant_id,
                        AlertModel.enabled == True,
                    )
                )
            )
            alerts = result.scalars().all()
            return json.dumps([a.to_dict() for a in alerts])

        @tool
        async def get_patient_documents(limit: int = 10) -> str:
            """Fetch a list of documents (PDFs, images) uploaded for the patient.
            Returns filenames, upload dates, and status."""
            result = await self.db.execute(
                select(DocumentModel)
                .where(
                    and_(
                        DocumentModel.patient_id == self.patient_id,
                        DocumentModel.tenant_id == self.tenant_id,
                    )
                )
                .order_by(desc(DocumentModel.created_at))
                .limit(limit)
            )
            docs = result.scalars().all()
            summary = []
            for d in docs:
                summary.append(
                    {
                        "id": str(d.id),
                        "filename": d.filename,
                        "status": d.status,
                        "date": d.created_at.isoformat() if d.created_at else None,
                    }
                )
            return json.dumps(summary)

        @tool
        async def get_biomarker_details(biomarker_id_or_slug: str) -> str:
            """Fetch full clinical definition, reference ranges, and informational text for a specific biomarker."""
            try:
                # Try by UUID first
                bio_uuid = UUID(biomarker_id_or_slug)
                query = select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == bio_uuid
                )
            except ValueError:
                # Try by slug
                query = select(BiomarkerDefinition).where(
                    BiomarkerDefinition.slug == biomarker_id_or_slug
                )

            result = await self.db.execute(query)
            bio = result.scalars().first()
            if not bio:
                return "Biomarker definition not found."

            return json.dumps(
                {
                    "id": str(bio.id),
                    "name": bio.name,
                    "slug": bio.slug,
                    "category": bio.category,
                    "description": bio.description,
                    "info": bio.info,
                    "reference_range": {
                        "min": bio.reference_range_min,
                        "max": bio.reference_range_max,
                    },
                }
            )

        @tool
        async def get_clinical_events(limit: int = 10) -> str:
            """Fetch a list of health journeys and clinical events for the patient (e.g., pregnancies, chronic pain cycles, surgical recoveries).
            Returns event titles, types, status, and dates."""
            result = await self.db.execute(
                select(ClinicalEvent)
                .options(selectinload(ClinicalEvent.type_entity))
                .where(
                    and_(
                        ClinicalEvent.patient_id == self.patient_id,
                        ClinicalEvent.tenant_id == self.tenant_id,
                    )
                )
                .order_by(desc(ClinicalEvent.onset_date))
                .limit(limit)
            )
            events = result.scalars().all()

            summary = []
            for event in events:
                summary.append(
                    {
                        "id": str(event.id),
                        "title": event.title,
                        "type": event.type_entity.name if event.type_entity else "Unknown",
                        "status": event.status.value,
                        "onset_date": event.onset_date.isoformat() if event.onset_date else None,
                        "resolved_date": event.resolved_date.isoformat() if event.resolved_date else None,
                        "description": event.description[:200] if event.description else None,
                    }
                )
            return json.dumps(summary)

        @tool
        async def get_clinical_event_details(event_id: str) -> str:
            """Fetch comprehensive details of a specific clinical event or health journey.
            Returns full description, occurrences/episodes, metadata, and linked examinations or biomarkers."""
            try:
                event_uuid = UUID(event_id)
            except ValueError:
                return "Invalid event ID format."

            result = await self.db.execute(
                select(ClinicalEvent)
                .options(
                    selectinload(ClinicalEvent.type_entity),
                    selectinload(ClinicalEvent.examination_links).selectinload(EventExaminationLink.examination),
                    selectinload(ClinicalEvent.observation_links).selectinload(EventObservationLink.observation).selectinload(Observation.biomarker).selectinload(BiomarkerDefinition.preferred_unit)
                )
                .where(
                    and_(
                        ClinicalEvent.id == event_uuid,
                        ClinicalEvent.tenant_id == self.tenant_id,
                        ClinicalEvent.patient_id == self.patient_id,
                    )
                )
            )
            event = result.scalars().first()
            if not event:
                return "Clinical event not found or access denied."

            return json.dumps(event.to_dict())

        @tool
        async def get_medication_catalog_details(medication_id: str) -> str:
            """Fetch informational details about a medication from the clinical catalog, including indications and side effects."""
            try:
                med_uuid = UUID(medication_id)
            except ValueError:
                return "Invalid medication ID format."

            result = await self.db.execute(
                select(MedicationCatalog).where(MedicationCatalog.id == med_uuid)
            )
            med = result.scalars().first()
            if not med:
                return "Medication not found in catalog."

            return json.dumps(med.to_dict())

        return [
            get_patient_summary,
            get_recent_biomarkers,
            get_biomarker_history,
            get_recent_examinations,
            get_current_medications,
            get_patient_medication_history,
            get_examination_details,
            get_patient_alerts,
            get_patient_documents,
            get_biomarker_details,
            get_medication_catalog_details,
            get_clinical_events,
            get_clinical_event_details,
            update_examination_notes,
            get_system_time,
            get_document_content,
        ]
