"""Human-in-the-loop proposal tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3). Each ``propose_*`` tool does NOT
write; it returns a ``{"__hitl__": True, "task": ...}`` payload that the chat
reasoning loop renders as an interactive review card. The user must confirm
before anything is saved.
"""
import json
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import uuid4

from langchain_core.tools import tool
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import ClinicalEventType
from app.models.enums import HitlTaskStatus
from app.models.examination_model import ExaminationModel


@register_chat_tool("hitl_proposals")
def build(ctx: ToolContext) -> List[Any]:
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
        type_result = await ctx.db.execute(
            select(ClinicalEventType).where(
                and_(
                    ClinicalEventType.slug == type_slug,
                    (ClinicalEventType.tenant_id == ctx.tenant_id)
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
                "patient_id": str(ctx.patient_id),
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
        candidate = examination_id or (str(ctx.examination_id) if ctx.examination_id else None)
        if not candidate:
            return json.dumps({
                "error": "No active examination. Call get_recent_examinations to find the exam "
                         "the user means, then pass its id as examination_id."
            })
        try:
            from uuid import UUID

            exam_uuid = UUID(candidate)
        except (ValueError, AttributeError, TypeError):
            return json.dumps({"error": f"Invalid examination_id '{candidate}' (expected a UUID)."})

        exam_result = await ctx.db.execute(
            select(ExaminationModel)
            .options(selectinload(ExaminationModel.category_entity))
            .where(
                and_(
                    ExaminationModel.id == exam_uuid,
                    ExaminationModel.patient_id == ctx.patient_id,
                    ExaminationModel.tenant_id == ctx.tenant_id,
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

        bio_result = await ctx.db.execute(
            select(BiomarkerDefinition).where(
                and_(
                    or_(
                        BiomarkerDefinition.name.ilike(biomarker_name),
                        BiomarkerDefinition.slug.ilike(biomarker_name),
                    ),
                    (BiomarkerDefinition.tenant_id == ctx.tenant_id)
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
                "patient_id": str(ctx.patient_id),
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
        matches = await _search(ctx.db, ctx.tenant_id, medication_name, limit=5)
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
                "patient_id": str(ctx.patient_id),
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
                "patient_id": str(ctx.patient_id) if ctx.patient_id else None,
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
                "patient_id": str(ctx.patient_id) if ctx.patient_id else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        return json.dumps({"__hitl__": True, "task": task})

    @tool
    async def propose_anatomy_graph_generation(target_structure: str) -> str:
        """Propose generating an anatomical graph expansion (nodes and edges) for a
        specific body part, organ, or system (e.g., 'Heart', 'Cardiovascular System').

        This does NOT generate the graph immediately. It renders a human-in-the-loop
        review card which will trigger the AI graph orchestrator if the user confirms.

        Args:
            target_structure: The name of the anatomical structure to generate (e.g. 'Heart').
        """
        task = {
            "schema_version": 1,
            "proposal_id": str(uuid4()),
            "task_type": "generate_anatomy_graph",
            "title": f"Generate Anatomy Graph: {target_structure}",
            "status": HitlTaskStatus.PROPOSED,
            "proposed_payload": {
                "target_structure": target_structure,
            },
            "context": {
                "patient_id": str(ctx.patient_id) if ctx.patient_id else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        return json.dumps({"__hitl__": True, "task": task})

    return [
        propose_create_clinical_event,
        propose_add_biomarker_to_examination,
        propose_add_medication,
        propose_create_biomarker_definition,
        propose_create_medication_definition,
        propose_anatomy_graph_generation,
    ]
