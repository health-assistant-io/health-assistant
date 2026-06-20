import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from sqlalchemy import select, desc, and_, or_
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
from app.models.enums import HitlTaskStatus


class ChatbotTools:
    def __init__(self, db: AsyncSession, tenant_id: UUID, patient_id: UUID, examination_id: Optional[UUID] = None):
        self.db = db
        self.tenant_id = tenant_id
        self.patient_id = patient_id
        self.examination_id = examination_id

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
        async def get_biomarker_history(biomarker_id_or_slug: str, limit: int = 10) -> str:
            """Fetch the historical trend for a specific biomarker using its ID (or slug).
            Do NOT use this for high-frequency telemetry (like heart rate or steps). Use it only for exact, discrete clinical lab results."""
            patient_ref = f"Patient/{self.patient_id}"
            
            # Check if it's a UUID
            try:
                bio_uuid = UUID(biomarker_id_or_slug)
                biomarker_filter = Observation.biomarker_id == bio_uuid
            except ValueError:
                biomarker_filter = Observation.biomarker.has(slug=biomarker_id_or_slug)
                
            result = await self.db.execute(
                select(Observation)
                .where(
                    and_(
                        Observation.subject["reference"].astext == patient_ref,
                        Observation.tenant_id == self.tenant_id,
                        biomarker_filter,
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
        async def search_available_biomarkers(search_term: Optional[str] = None) -> str:
            """Search the clinical catalog to find the exact ID and type (telemetry vs clinical) of a biomarker.
            Use this tool BEFORE querying data if you are unsure of the exact ID or whether it is high-frequency telemetry.
            Typo-tolerant (trigram similarity) over name/slug/code; tenant-scoped + globals.
            If search_term is omitted, returns a list of common biomarkers."""
            from app.services.catalog_search_service import search_biomarkers

            biomarkers = await search_biomarkers(self.db, self.tenant_id, search_term, limit=20)

            summary = []
            for b in biomarkers:
                summary.append({
                    "id": str(b.id),
                    "name": b.name,
                    "slug": b.slug,
                    "category": b.category,
                    "is_telemetry": b.is_telemetry,
                    "preferred_unit": b.preferred_unit.symbol if b.preferred_unit else None
                })
            return json.dumps(summary)

        @tool
        async def search_medications(
            search_term: str,
            limit: int = 10,
        ) -> str:
            """Search the medication catalog (tenant-scoped + globals) by name or indication.
            Typo-tolerant (trigram similarity + substring fallback): returns the closest
            matches ranked by similarity so you can resolve "ibuprofin" / "paracetamol" /
            "Glucophage" to the canonical catalog entry. Use this BEFORE proposing to add
            a medication so you reuse an existing catalog_id instead of creating a duplicate.

            Args:
                search_term: Drug name, generic name, or symptom/indication fragment.
                limit: Max results (default 10).
            """
            from app.services.catalog_search_service import search_medications as _search

            meds = await _search(self.db, self.tenant_id, search_term, limit=limit)
            return json.dumps([m.to_dict() for m in meds])

        @tool
        async def get_aggregated_biomarker_trends(biomarker_id_or_slug: str, start_date_iso: Optional[str] = None, end_date_iso: Optional[str] = None, period: str = "last-30-days", aggregation: Optional[str] = None, limit: int = 100) -> str:
            """Fetch historical, aggregated timeseries data for a biomarker (especially telemetry like heart rate or steps).
            Specify a 'period' (e.g., 'last-7-days', 'last-6-months', 'all-time') OR explicit 'start_date_iso' and 'end_date_iso'.
            Optionally specify 'aggregation' bucket (e.g. '1 hour', '1 day').
            Returns averaged OHLC data. Do NOT use this for exact single point-in-time lab results; use get_biomarker_history for those.
            Returns up to the `limit` most recent aggregated records within the range to protect context size."""
            from datetime import datetime
            from app.services.analytics_service import get_biomarker_trends
            
            start_date = None
            end_date = None
            if start_date_iso:
                try:
                    start_date = datetime.fromisoformat(start_date_iso.replace('Z', '+00:00'))
                except ValueError:
                    return "Invalid start_date_iso format. Use ISO 8601."
            if end_date_iso:
                try:
                    end_date = datetime.fromisoformat(end_date_iso.replace('Z', '+00:00'))
                except ValueError:
                    return "Invalid end_date_iso format. Use ISO 8601."

            result = await get_biomarker_trends(
                tenant_id=str(self.tenant_id),
                biomarker_codes=biomarker_id_or_slug,
                period=period,
                aggregation=aggregation,
                patient_id=str(self.patient_id),
                start_date=start_date,
                end_date=end_date,
                db=self.db
            )
            
            trends = result.get("biomarkers", {})
            
            target_data = []
            # First try exact match
            for key, data in trends.items():
                if biomarker_slug.lower() == key.lower():
                    target_data = data
                    break
            
            # Fallback to substring match if exact match fails
            if not target_data:
                for key, data in trends.items():
                    if biomarker_slug.lower() in key.lower() or key.lower() in biomarker_slug.lower():
                        target_data = data
                        break
            
            if not target_data:
                # If exact match fails, return the first one if there is only one
                if len(trends) == 1:
                    target_data = list(trends.values())[0]
                else:
                    return json.dumps([])

            # Apply record limit, keeping the most recent records
            target_data = target_data[-limit:]

            # Strip heavy UI metadata to save tokens
            lightweight_data = []
            for item in target_data:
                # Ensure we only keep what's essential
                clean_item = {
                    "date": item.get("date"),
                    "value": item.get("value"),
                    "unit": item.get("unit"),
                    "status": item.get("status")
                }
                if item.get("min_value") is not None:
                    clean_item["min_value"] = item.get("min_value")
                if item.get("max_value") is not None:
                    clean_item["max_value"] = item.get("max_value")
                lightweight_data.append(clean_item)

            return json.dumps(lightweight_data)

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

        @tool
        async def propose_create_clinical_event(
            title: str,
            type_slug: str,
            onset_date: Optional[str] = None,
            description: Optional[str] = None,
            status: str = "ACTIVE",
            reason: Optional[str] = None,
        ) -> str:
            """Propose creating a new clinical event (a longitudinal health journey such as
            a pregnancy, chronic pain cycle, surgical recovery, or allergy episode).

            This does NOT create the event. It renders a human-in-the-loop review card
            prefilled with your suggestion; the user edits and explicitly confirms before
            anything is saved. Call this ONCE per request, after gathering enough context,
            then explain what you prepared and wait for the user.

            Args:
                title: Human-readable event title (e.g. "Third Pregnancy", "Chronic Migraines").
                type_slug: The slug of the ClinicalEventType (e.g. "pregnancy", "pain-episode",
                           "surgical-recovery"). Use `get_clinical_events` or known slugs.
                onset_date: Optional ISO date (YYYY-MM-DD) when the event started.
                description: Optional narrative description.
                status: One of ACTIVE, RESOLVED, ON_HOLD, UNKNOWN (default ACTIVE).
                reason: Optional clinical rationale for the proposal.
            """
            # Resolve the type by slug (tenant-scoped or global)
            type_result = await self.db.execute(
                select(ClinicalEventType).where(
                    and_(
                        ClinicalEventType.slug == type_slug,
                        (ClinicalEventType.tenant_id == self.tenant_id)
                        | (ClinicalEventType.tenant_id.is_(None)),
                    )
                )
            )
            event_type = type_result.scalars().first()

            type_id = str(event_type.id) if event_type else None
            type_name = event_type.name if event_type else type_slug

            task = {
                "schema_version": 1,
                "proposal_id": str(uuid4()),
                "task_type": "create_clinical_event",
                "title": f"Create Clinical Event: {title}",
                "status": HitlTaskStatus.PROPOSED,
                "proposed_payload": {
                    "type_id": type_id,
                    "type_slug": type_slug,
                    "type_name": type_name,
                    "title": title,
                    "description": description or "",
                    "status": status.upper(),
                    "onset_date": onset_date or "",
                    "resolved_date": "",
                    "event_metadata": {},
                    "occurrences": [],
                    "coding_system": "custom",
                    "code": "",
                },
                "context": {
                    "patient_id": str(self.patient_id),
                    "reason": reason,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
                "resolved": None,
            }
            return json.dumps({"__hitl__": True, "task": task})

        @tool
        async def propose_add_biomarker_to_examination(
            biomarker_name: str,
            value: float,
            unit: Optional[str] = None,
            interpretation: Optional[str] = None,
            note: Optional[str] = None,
            examination_id: Optional[str] = None,
            reason: Optional[str] = None,
        ) -> str:
            """Propose adding a biomarker measurement (a lab result) to an examination.

            This does NOT save anything. It renders a human-in-the-loop review card
            prefilled with your suggestion; the user edits and explicitly confirms
            before anything is saved. Call this ONCE per request, after gathering
            enough context, then explain what you prepared and wait for the user.

            Targeting the examination:
            - If the user is currently viewing an exam, omit `examination_id`.
            - Otherwise, resolve the exam they mean by calling `get_recent_examinations`
              (returns each exam's id + date + category) and pass its `id` here. You
              may also pass an ID the user gave you directly.
            The exam MUST belong to the current patient; otherwise the proposal is
            rejected (no card) and you'll get an error to act on.

            Use `search_available_biomarkers` first if you are unsure of the exact
            biomarker name/slug.

            Args:
                biomarker_name: The biomarker name or slug (e.g. "Cholesterol", "glucose").
                value: The numeric measurement value.
                unit: Optional unit symbol (e.g. "mg/dL"). Defaults to the biomarker's preferred unit.
                interpretation: Optional - one of "low", "normal", "high". Defaults to "normal".
                note: Optional free-text note.
                examination_id: Optional exam UUID to target. Required when no exam is open in the chat.
                reason: Optional clinical rationale for the proposal.
            """
            # --- Resolve + authorize the target examination (hard-fail on miss) ---
            candidate = examination_id or (str(self.examination_id) if self.examination_id else None)
            if not candidate:
                return json.dumps({
                    "error": "No active examination. Call get_recent_examinations to find the exam "
                             "the user means, then pass its id as examination_id."
                })
            try:
                exam_uuid = UUID(candidate)
            except (ValueError, AttributeError, TypeError):
                return json.dumps({"error": f"Invalid examination_id '{candidate}' (expected a UUID)."})

            exam_result = await self.db.execute(
                select(ExaminationModel)
                .options(selectinload(ExaminationModel.category_entity))
                .where(
                    and_(
                        ExaminationModel.id == exam_uuid,
                        ExaminationModel.patient_id == self.patient_id,
                        ExaminationModel.tenant_id == self.tenant_id,
                    )
                )
            )
            exam = exam_result.scalars().first()
            if not exam:
                return json.dumps({
                    "error": f"Examination {candidate} was not found or is not accessible for this patient."
                })

            resolved_exam_id = str(exam.id)
            examination_date = exam.examination_date.isoformat() if exam.examination_date else None
            examination_category = exam.category_entity.name if exam.category_entity else None

            # --- Resolve the biomarker by name/slug (tenant-scoped or global) ---
            interp = (interpretation or "normal").lower()
            if interp not in {"low", "normal", "high"}:
                interp = "normal"

            biomarker_id = None
            biomarker_slug = None
            resolved_name = biomarker_name
            matched = False

            bio_result = await self.db.execute(
                select(BiomarkerDefinition).where(
                    and_(
                        or_(
                            BiomarkerDefinition.name.ilike(biomarker_name),
                            BiomarkerDefinition.slug.ilike(biomarker_name),
                        ),
                        (BiomarkerDefinition.tenant_id == self.tenant_id)
                        | (BiomarkerDefinition.tenant_id.is_(None)),
                    )
                )
            )
            biomarker = bio_result.scalars().first()
            if biomarker:
                biomarker_id = str(biomarker.id)
                biomarker_slug = biomarker.slug
                resolved_name = biomarker.name
                matched = True

            task = {
                "schema_version": 1,
                "proposal_id": str(uuid4()),
                "task_type": "add_biomarker_to_examination",
                "title": f"Add Biomarker: {resolved_name} = {value}",
                "status": HitlTaskStatus.PROPOSED,
                "proposed_payload": {
                    "biomarker_id": biomarker_id,
                    "biomarker_name": resolved_name,
                    "biomarker_slug": biomarker_slug,
                    "value": value,
                    "unit": unit or "",
                    "interpretation": interp,
                    "note": note or "",
                    "matched": matched,
                },
                "context": {
                    "patient_id": str(self.patient_id),
                    "examination_id": resolved_exam_id,
                    "examination_date": examination_date,
                    "examination_category": examination_category,
                    "reason": reason,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
                "resolved": None,
            }
            return json.dumps({"__hitl__": True, "task": task})

        @tool
        async def propose_add_medication(
            medication_name: str,
            dosage: Optional[str] = None,
            frequency_label: Optional[str] = None,
            reason: Optional[str] = None,
            note: Optional[str] = None,
            start_date: Optional[str] = None,
        ) -> str:
            """Propose adding a new medication to the patient's record.

            This does NOT save anything. It renders a human-in-the-loop review card
            prefilled with your suggestion; the user edits and explicitly confirms
            before anything is saved. Call this ONCE per request, after gathering
            enough context, then explain what you prepared and wait for the user.

            Catalog resolution: call `search_medications` FIRST. If a close match
            exists, pass its canonical name as `medication_name` (the proposal will
            reuse the catalog_id). If no match exists, pass the name as-is and the
            user will be offered a "define custom catalog entry" path on confirm.

            Args:
                medication_name: Canonical drug name (e.g. "Metformin"). Use the name
                    returned by `search_medications` when there is a match.
                dosage: Optional free-text dosage (e.g. "500 mg", "1 tablet").
                frequency_label: Optional short frequency hint (e.g. "twice daily",
                    "every 8 hours", "as needed"). Translated by the user in the form.
                reason: Optional indication / why it's being taken (e.g. "Type 2 diabetes").
                note: Optional free-text note.
                start_date: Optional ISO date (YYYY-MM-DD) when the medication started.
            """
            from app.services.catalog_search_service import search_medications as _search

            # Resolve the catalog entry (tenant-scoped + globals).
            matches = await _search(self.db, self.tenant_id, medication_name, limit=5)
            best = matches[0] if matches else None

            catalog_id = str(best.id) if best else None
            resolved_name = best.name if best else medication_name
            matched = best is not None
            indications = best.indications if best else None
            side_effects = list(best.side_effects or []) if best else []
            contraindications = best.contraindications if best else None
            dosage_info = best.dosage_info if best else None

            task = {
                "schema_version": 1,
                "proposal_id": str(uuid4()),
                "task_type": "add_medication",
                "title": f"Add Medication: {resolved_name}",
                "status": HitlTaskStatus.PROPOSED,
                "proposed_payload": {
                    # Identity / catalog resolution
                    "name": resolved_name,
                    "catalog_id": catalog_id,
                    "matched": matched,
                    "is_new": not matched,  # form opens "define custom" path when True
                    # Catalog detail snapshot (so the form doesn't have to refetch)
                    "indications": indications,
                    "side_effects": side_effects,
                    "contraindications": contraindications,
                    "dosage_info": dosage_info,
                    # Prescription fields
                    "dosage": dosage or "",
                    "frequency_label": frequency_label or "",
                    "reason": reason or "",
                    "note": note or "",
                    "start_date": start_date or "",
                    "end_date": "",
                    "status": "active",
                },
                "context": {
                    "patient_id": str(self.patient_id),
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
                "resolved": None,
            }
            return json.dumps({"__hitl__": True, "task": task})

        @tool
        async def propose_create_biomarker_definition(
            name: str,
            category: Optional[str] = None,
            unit_symbol: Optional[str] = None,
            reference_range_min: Optional[float] = None,
            reference_range_max: Optional[float] = None,
            coding_system: Optional[str] = "loinc",
            code: Optional[str] = None,
            aliases: Optional[List[str]] = None,
            info: Optional[str] = None,
            is_telemetry: Optional[bool] = False,
        ) -> str:
            """Propose creating a NEW biomarker definition in the tenant catalog.

            This does NOT save anything. It renders a human-in-the-loop review card
            prefilled with your suggestion; the user edits and explicitly confirms
            before anything is saved. Call this ONCE per request, then explain what
            you prepared and wait for the user.

            Use this when the user asks to track a metric that does not yet exist
            in the catalog (e.g., a novel lab value, a custom wearable metric, or
            any biomarker not returned by `search_available_biomarkers`). Do NOT
            use it to record a value for an existing biomarker (use
            `propose_add_biomarker_to_examination` for that).

            Tenant-uniqueness: pass a clear `name`; the slug is derived from it.
            The user can edit the slug in the review form if needed.

            Args:
                name: Human-readable biomarker name (e.g. "White Blood Cell Count").
                category: Optional grouping (e.g. "Hematology", "Lipids").
                unit_symbol: Optional preferred unit symbol (e.g. "mg/dL", "x10^9/L").
                    Pass the symbol you expect values to arrive in.
                reference_range_min: Optional lower bound of the normal range.
                reference_range_max: Optional upper bound of the normal range.
                coding_system: "loinc" (default) or "custom".
                code: Optional code in the coding system (e.g. LOINC "6690-2").
                aliases: Optional synonyms / alternate names patients or labs use.
                info: Optional clinical context / significance (markdown ok).
                is_telemetry: True if this is a high-frequency IoT/wearable metric
                    (heart rate, steps, SpO2). False for standard discrete labs.
            """
            slug = name.lower().replace(" ", "-").replace("/", "-")
            slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")

            task = {
                "schema_version": 1,
                "proposal_id": str(uuid4()),
                "task_type": "create_biomarker_definition",
                "title": f"Define Biomarker: {name}",
                "status": HitlTaskStatus.PROPOSED,
                "proposed_payload": {
                    "name": name,
                    "slug": slug,
                    "category": category or "",
                    "coding_system": coding_system or "loinc",
                    "code": code or "",
                    "preferred_unit_symbol": unit_symbol or "",
                    "reference_range_min": reference_range_min,
                    "reference_range_max": reference_range_max,
                    "aliases": list(aliases or []),
                    "info": info or "",
                    "is_telemetry": bool(is_telemetry),
                },
                "context": {
                    "patient_id": str(self.patient_id) if self.patient_id else None,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
                "resolved": None,
            }
            return json.dumps({"__hitl__": True, "task": task})

        @tool
        async def propose_create_medication_definition(
            name: str,
            description: Optional[str] = None,
            indications: Optional[str] = None,
            dosage_info: Optional[str] = None,
            contraindications: Optional[str] = None,
            side_effects: Optional[List[str]] = None,
        ) -> str:
            """Propose creating a NEW medication definition in the tenant catalog.

            This does NOT save anything. It renders a human-in-the-loop review card
            prefilled with your suggestion; the user edits and explicitly confirms
            before anything is saved. Call this ONCE per request, then explain what
            you prepared and wait for the user.

            Use this when the user asks to add a drug to the catalog that
            `search_medications` cannot find (e.g. a new or rarely-prescribed drug).
            Do NOT use it to prescribe an existing catalog drug to a patient (use
            `propose_add_medication` for that).

            Args:
                name: Canonical drug name (e.g. "Amoxicillin").
                description: Optional short description / overview.
                indications: Optional main indications (what it treats).
                dosage_info: Optional typical dosage guidance (free text).
                contraindications: Optional contraindications / warnings.
                side_effects: Optional list of common side effects.
            """
            task = {
                "schema_version": 1,
                "proposal_id": str(uuid4()),
                "task_type": "create_medication_definition",
                "title": f"Define Medication: {name}",
                "status": HitlTaskStatus.PROPOSED,
                "proposed_payload": {
                    "name": name,
                    "description": description or "",
                    "indications": indications or "",
                    "dosage_info": dosage_info or "",
                    "contraindications": contraindications or "",
                    "side_effects": list(side_effects or []),
                },
                "context": {
                    "patient_id": str(self.patient_id) if self.patient_id else None,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
                "resolved": None,
            }
            return json.dumps({"__hitl__": True, "task": task})

        return [
            get_patient_summary,
            search_available_biomarkers,
            get_recent_biomarkers,
            get_biomarker_history,
            get_aggregated_biomarker_trends,
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
            propose_create_clinical_event,
            propose_add_biomarker_to_examination,
            propose_add_medication,
            propose_create_biomarker_definition,
            propose_create_medication_definition,
            search_medications,
        ]
