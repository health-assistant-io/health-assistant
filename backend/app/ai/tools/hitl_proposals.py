"""Human-in-the-loop proposal tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3). Each ``propose_*`` tool does NOT
write; it returns a ``{"__hitl__": True, "task": ...}`` payload that the chat
reasoning loop renders as an interactive review card. The user must confirm
before anything is saved.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from langchain_core.tools import tool
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import ClinicalEventType
from app.models.enums import HitlTaskStatus
from app.models.examination_model import ExaminationModel


async def _notify_hitl_proposal(ctx: ToolContext, task: dict) -> None:
    """Surface a HITL proposal in the recipient's notification inbox so it
    survives a closed chat window. Best-effort — never breaks the tool call.
    """
    if not ctx.user_id:
        return
    try:
        from app.services.notification_service import emit
        from app.models.enums import (
            NotificationCategory,
            NotificationSeverity,
            NotificationSource,
            NotificationType,
            RecipientKind,
        )

        await emit(
            source=NotificationSource.AGENT,
            type=NotificationType.HITL_TASK,
            category=NotificationCategory.HITL,
            severity=NotificationSeverity.WARNING,
            title=task.get("title", "AI proposal needs your review"),
            body="The assistant prepared an action for your confirmation.",
            patient_id=ctx.patient_id,
            tenant_id=ctx.tenant_id,
            targets=[{"kind": RecipientKind.USER.value, "id": str(ctx.user_id)}],
            payload={
                "task_type": task.get("task_type"),
                "proposal_id": task.get("proposal_id"),
                "actions": [
                    {
                        "id": "open_chat",
                        "label": "Open chat",
                        "type": "link",
                        "url": "/ai-assistant",
                        "style": "primary",
                    }
                ],
            },
            source_ref={
                "proposal_id": task.get("proposal_id"),
                "task_type": task.get("task_type"),
            },
            sender_user_id=ctx.user_id,
            link_communication=ctx.patient_id is not None,
        )
    except Exception:
        # Tool results must never fail because of a notification side-effect.
        import logging

        logging.getLogger(__name__).exception("HITL notification emit failed")


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
        links: Optional[List[dict]] = None,
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
            links: Optional list of related-items links to create alongside, once
                this event exists. Each item: ``{dst_type, dst_id, relation, properties?}``.
                The destination must already exist (catalog or instance). Valid
                combinations are returned by ``get_link_schema(src_type="clinical_event_type")``.
                Invalid combinations are silently dropped (kept vs dropped count is
                reported in the tool result).
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

        # Validate + snapshot any proposed links (clinical_event_type is the src).
        link_specs: Dict[str, Any] = {"kept": [], "dropped": []}
        if links:
            from app.ai.tools.propose_link import build_link_specs
            from app.models.enums import EdgeEndpointType

            link_specs = await build_link_specs(
                ctx.db,
                ctx.tenant_id,
                EdgeEndpointType.CLINICAL_EVENT_TYPE,
                links,
            )

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
                "links": link_specs["kept"],
            },
            "context": {
                "patient_id": str(ctx.patient_id),
                "reason": reason,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        await _notify_hitl_proposal(ctx, task)
        drop_count = len(link_specs["dropped"])
        result = {"__hitl__": True, "task": task}
        if drop_count:
            result["links_dropped"] = drop_count
            result["links_dropped_reasons"] = [
                d.get("reason", "unknown") for d in link_specs["dropped"]
            ]
        return json.dumps(result)

    @tool
    async def propose_record_biomarker_result(
        biomarker_name: str,
        value: float,
        unit: Optional[str] = None,
        interpretation: Optional[str] = None,
        note: Optional[str] = None,
        examination_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> str:
        """Propose recording a biomarker measurement (a lab result) on an examination —
        a PATIENT-INSTANCE data point.

        Use `propose_define_biomarker` INSTEAD when the user wants to add a NEW
        biomarker to the catalog itself (a reference definition, not a measurement).

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
        candidate = examination_id or (
            str(ctx.examination_id) if ctx.examination_id else None
        )
        if not candidate:
            return json.dumps(
                {
                    "error": "No active examination. Call get_recent_examinations to find the exam "
                    "the user means, then pass its id as examination_id."
                }
            )
        try:
            from uuid import UUID

            exam_uuid = UUID(candidate)
        except (ValueError, AttributeError, TypeError):
            return json.dumps(
                {"error": f"Invalid examination_id '{candidate}' (expected a UUID)."}
            )

        exam_result = await ctx.db.execute(
            select(ExaminationModel)
            .options(selectinload(ExaminationModel.category_concept))
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
            return json.dumps(
                {
                    "error": f"Examination {candidate} was not found or is not accessible for this patient."
                }
            )

        resolved_exam_id = str(exam.id)
        examination_date = (
            exam.examination_date.isoformat() if exam.examination_date else None
        )
        examination_category = (
            exam.category_concept.name if exam.category_concept else None
        )

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
        await _notify_hitl_proposal(ctx, task)
        return json.dumps({"__hitl__": True, "task": task})

    @tool
    async def propose_prescribe_medication(
        medication_name: str,
        dosage: Optional[str] = None,
        frequency_label: Optional[str] = None,
        reason: Optional[str] = None,
        note: Optional[str] = None,
        start_date: Optional[str] = None,
        links: Optional[List[dict]] = None,
    ) -> str:
        """Propose prescribing a medication to the patient — a PATIENT-INSTANCE
        prescription (the patient takes this drug).

        Use `propose_define_medication` INSTEAD when the user wants to add a NEW
        drug to the catalog itself (a reference definition, not a prescription).
        Typical trigger: "add a new medication called X to the catalog" or
        "X isn't in the system yet" — use define, not prescribe.

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
            links: Optional list of related-items links to attach to the medication
                catalog entry once it's resolved (e.g. ``{dst_type:"concept",
                dst_id:"<disease-uuid>", relation:"TREATS"}``). The destination
                must already exist. Valid combinations are returned by
                ``get_link_schema(src_type="medication")``. Invalid combinations
                are silently dropped (kept vs dropped count is reported in the
                tool result). Links are committed to the medication **catalog**
                entry, not the patient prescription row.
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

        # Validate + snapshot any proposed links (medication catalog entry is src).
        # When the catalog entry already exists, pass primary_existing_id so dedup
        # surfaces "Link exists" badges in the form. When it's a new custom entry,
        # dedup is skipped (there can't be a pre-existing edge to a not-yet-row).
        link_specs: Dict[str, Any] = {"kept": [], "dropped": []}
        if links:
            from uuid import UUID

            from app.ai.tools.propose_link import build_link_specs
            from app.models.enums import EdgeEndpointType

            primary_existing_id = UUID(catalog_id) if catalog_id else None
            link_specs = await build_link_specs(
                ctx.db,
                ctx.tenant_id,
                EdgeEndpointType.MEDICATION,
                links,
                primary_existing_id=primary_existing_id,
            )

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
                # Related-items links (validated + snapshotted server-side)
                "links": link_specs["kept"],
            },
            "context": {
                "patient_id": str(ctx.patient_id),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        await _notify_hitl_proposal(ctx, task)
        drop_count = len(link_specs["dropped"])
        result = {"__hitl__": True, "task": task}
        if drop_count:
            result["links_dropped"] = drop_count
            result["links_dropped_reasons"] = [
                d.get("reason", "unknown") for d in link_specs["dropped"]
            ]
        return json.dumps(result)

    @tool
    async def propose_define_biomarker(
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
        links: Optional[List[dict]] = None,
    ) -> str:
        """Propose defining a NEW biomarker in the tenant catalog — a reference
        definition (the metric itself, not a measurement).

        Use `propose_record_biomarker_result` INSTEAD when the user wants to
        record an actual measurement for an EXISTING biomarker on an examination
        (a lab result).

        This does NOT save anything. It renders a human-in-the-loop review card
        prefilled with your suggestion; the user edits and explicitly confirms
        before anything is saved. Call this ONCE per request, then explain what
        you prepared and wait for the user.

        Use this when the user asks to track a metric that does not yet exist
        in the catalog (e.g., a novel lab value, a custom wearable metric, or
        any biomarker not returned by `search_available_biomarkers`). Do NOT
        use it to record a value for an existing biomarker (use
        `propose_record_biomarker_result` for that).

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
            links: Optional list of related-items links to create alongside, once
                this definition exists (e.g. panel membership via ``relation:"MEMBER_OF"``
                or organ affected via ``relation:"AFFECTS"``). Each item:
                ``{dst_type, dst_id, relation, properties?}``. The destination
                must already exist. Valid combinations are returned by
                ``get_link_schema(src_type="biomarker")``. Invalid combinations
                are silently dropped (kept vs dropped count is reported in the
                tool result).
        """
        slug = name.lower().replace(" ", "-").replace("/", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")

        # Validate + snapshot any proposed links (biomarker is the src).
        link_specs: Dict[str, Any] = {"kept": [], "dropped": []}
        if links:
            from app.ai.tools.propose_link import build_link_specs
            from app.models.enums import EdgeEndpointType

            link_specs = await build_link_specs(
                ctx.db,
                ctx.tenant_id,
                EdgeEndpointType.BIOMARKER,
                links,
            )

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
                "links": link_specs["kept"],
            },
            "context": {
                "patient_id": str(ctx.patient_id) if ctx.patient_id else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        await _notify_hitl_proposal(ctx, task)
        drop_count = len(link_specs["dropped"])
        result = {"__hitl__": True, "task": task}
        if drop_count:
            result["links_dropped"] = drop_count
            result["links_dropped_reasons"] = [
                d.get("reason", "unknown") for d in link_specs["dropped"]
            ]
        return json.dumps(result)

    @tool
    async def propose_define_medication(
        name: str,
        description: Optional[str] = None,
        indications: Optional[str] = None,
        dosage_info: Optional[str] = None,
        contraindications: Optional[str] = None,
        side_effects: Optional[List[str]] = None,
        links: Optional[List[dict]] = None,
    ) -> str:
        """Propose defining a NEW medication in the tenant catalog — a reference
        definition (the drug itself, not a prescription).

        Use `propose_prescribe_medication` INSTEAD when the user wants the
        patient to actually take the drug (a prescription — a patient-instance
        record). Typical trigger: "add Metformin to my medications" / "I'm taking
        X" / "prescribe X" — use prescribe, not define.

        This does NOT save anything. It renders a human-in-the-loop review card
        prefilled with your suggestion; the user edits and explicitly confirms
        before anything is saved. Call this ONCE per request, then explain what
        you prepared and wait for the user.

        Use this when the user asks to add a drug to the catalog that
        `search_medications` cannot find (e.g. a new or rarely-prescribed drug).
        Do NOT use it to prescribe an existing catalog drug to a patient (use
        `propose_prescribe_medication` for that).

        Args:
            name: Canonical drug name (e.g. "Amoxicillin").
            description: Optional short description / overview.
            indications: Optional main indications (what it treats).
            dosage_info: Optional typical dosage guidance (free text).
            contraindications: Optional contraindications / warnings.
            side_effects: Optional list of common side effects.
            links: Optional list of related-items links to create alongside, once
                this definition exists (e.g. ``{dst_type:"concept", dst_id:"<disease>",
                relation:"TREATS"}``). The destination must already exist. Valid
                combinations are returned by
                ``get_link_schema(src_type="medication")``. Invalid combinations
                are silently dropped (kept vs dropped count is reported in the
                tool result).
        """
        # Validate + snapshot any proposed links (medication is the src).
        link_specs: Dict[str, Any] = {"kept": [], "dropped": []}
        if links:
            from app.ai.tools.propose_link import build_link_specs
            from app.models.enums import EdgeEndpointType

            link_specs = await build_link_specs(
                ctx.db,
                ctx.tenant_id,
                EdgeEndpointType.MEDICATION,
                links,
            )

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
                "links": link_specs["kept"],
            },
            "context": {
                "patient_id": str(ctx.patient_id) if ctx.patient_id else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        await _notify_hitl_proposal(ctx, task)
        drop_count = len(link_specs["dropped"])
        result = {"__hitl__": True, "task": task}
        if drop_count:
            result["links_dropped"] = drop_count
            result["links_dropped_reasons"] = [
                d.get("reason", "unknown") for d in link_specs["dropped"]
            ]
        return json.dumps(result)

    @tool
    async def propose_record_allergy(
        allergen_name: str,
        criticality: Optional[str] = "low",
        category: Optional[str] = None,
        note: Optional[str] = None,
        onset_date: Optional[str] = None,
        reactions: Optional[List[dict]] = None,
        reason: Optional[str] = None,
        links: Optional[List[dict]] = None,
    ) -> str:
        """Propose recording an allergy / intolerance on the patient's chart —
        a PATIENT-INSTANCE record (the patient reacts to this substance).

        Use `propose_define_allergy` INSTEAD when the user wants to add a NEW
        allergen to the catalog itself (a reference definition, not a record
        on a patient). Typical trigger: "X isn't in the system yet" → use
        define, not record.

        This does NOT save anything. It renders a human-in-the-loop review card
        prefilled with your suggestion; the user edits and explicitly confirms
        before anything is saved. Call this ONCE per request, after gathering
        enough context, then explain what you prepared and wait for the user.

        Catalog resolution: call `search_allergens` FIRST. If a close match
        exists, pass its canonical name as `allergen_name` (the proposal will
        reuse the catalog_id). If no match exists, pass the name as-is and the
        user will be offered a "define custom catalog entry" path on confirm.

        Args:
            allergen_name: The allergen name (e.g. "Peanuts", "Penicillin"). Use
                the name returned by `search_allergens` when there is a match.
            criticality: One of "low", "high", "unable_to_assess" (default "low").
                "high" flags a life-threatening reaction (anaphylaxis risk).
            category: Optional FHIR allergy category — one of "food",
                "medication", "environment", "biologic". Guessed from the
                catalog entry when omitted.
            note: Optional free-text clinical note.
            onset_date: Optional ISO date (YYYY-MM-DD) when the allergy started.
            reactions: Optional list of reaction episodes. Each item:
                ``{"manifestation": str, "severity": "mild"|"moderate"|"severe",
                "date": "YYYY-MM-DD"?}``.
            reason: Optional clinical rationale for the proposal.
            links: Optional list of related-items links to attach to the
                allergy catalog entry once it's resolved (e.g.
                ``{dst_type:"anatomy", dst_id:"<uuid>", relation:"AFFECTS"}``).
                The destination must already exist. Valid combinations are
                returned by ``get_link_schema(src_type="allergy")``. Invalid
                combinations are silently dropped (kept vs dropped count is
                reported in the tool result). Links are committed to the
                allergy **catalog** entry, not the patient intolerance row.
        """
        from app.services.catalog_search_service import search_allergies as _search

        # Resolve the catalog entry (tenant-scoped + globals).
        matches = await _search(ctx.db, ctx.tenant_id, allergen_name, limit=5)
        best = matches[0] if matches else None

        catalog_id = str(best.id) if best else None
        resolved_name = best.name if best else allergen_name
        matched = best is not None
        resolved_category = (
            category
            or (getattr(best, "category", None) if best else None)
            or "OTHER"
        ).upper()
        typical_reactions = (
            list(getattr(best, "typical_reactions", None) or []) if best else []
        )

        # Normalize criticality.
        crit = (criticality or "low").upper()
        if crit not in {"LOW", "HIGH", "UNABLE_TO_ASSESS"}:
            crit = "LOW"

        # Validate + snapshot any proposed links (allergy catalog entry is src).
        link_specs: Dict[str, Any] = {"kept": [], "dropped": []}
        if links:
            from uuid import UUID

            from app.ai.tools.propose_link import build_link_specs
            from app.models.enums import EdgeEndpointType

            primary_existing_id = UUID(catalog_id) if catalog_id else None
            link_specs = await build_link_specs(
                ctx.db,
                ctx.tenant_id,
                EdgeEndpointType.ALLERGY,
                links,
                primary_existing_id=primary_existing_id,
            )

        task = {
            "schema_version": 1,
            "proposal_id": str(uuid4()),
            "task_type": "add_allergy",
            "title": f"Add Allergy: {resolved_name}",
            "status": HitlTaskStatus.PROPOSED,
            "proposed_payload": {
                # Identity / catalog resolution
                "name": resolved_name,
                "catalog_id": catalog_id,
                "matched": matched,
                "is_new": not matched,
                # Catalog detail snapshot
                "category": resolved_category,
                "typical_reactions": typical_reactions,
                # Intolerance fields
                "clinical_status": "ACTIVE",
                "criticality": crit,
                "verification_status": "confirmed",
                "onset_date": onset_date or "",
                "resolved_date": "",
                "last_occurrence": "",
                "note": note or "",
                "reactions": reactions or [],
                # Related-items links (validated + snapshotted server-side)
                "links": link_specs["kept"],
            },
            "context": {
                "patient_id": str(ctx.patient_id),
                "reason": reason,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        await _notify_hitl_proposal(ctx, task)
        drop_count = len(link_specs["dropped"])
        result = {"__hitl__": True, "task": task}
        if drop_count:
            result["links_dropped"] = drop_count
            result["links_dropped_reasons"] = [
                d.get("reason", "unknown") for d in link_specs["dropped"]
            ]
        return json.dumps(result)

    @tool
    async def propose_define_allergy(
        name: str,
        category: Optional[str] = "OTHER",
        description: Optional[str] = None,
        typical_reactions: Optional[List[str]] = None,
        links: Optional[List[dict]] = None,
    ) -> str:
        """Propose defining a NEW allergen in the tenant catalog — a reference
        definition (the substance itself, not a patient's reaction).

        Use `propose_record_allergy` INSTEAD when the user wants to record a
        reaction on the patient's chart (a patient-instance intolerance).
        Typical trigger: "I'm allergic to X" / "add X to my allergies" → use
        record, not define.

        This does NOT save anything. It renders a human-in-the-loop review card
        prefilled with your suggestion; the user edits and explicitly confirms
        before anything is saved. Call this ONCE per request, then explain what
        you prepared and wait for the user.

        Use this when the user asks to track an allergen that
        `search_allergens` cannot find (e.g. a niche substance). Do NOT use it
        to record a reaction to an existing catalog allergen (use
        `propose_record_allergy` for that).

        Args:
            name: Canonical allergen name (e.g. "Peanuts", "Latex").
            category: One of "FOOD", "MEDICATION", "ENVIRONMENT", "BIOLOGIC",
                "OTHER" (default "OTHER").
            description: Optional short description.
            typical_reactions: Optional list of common reaction symptoms
                (e.g. ["Hives", "Anaphylaxis"]).
            links: Optional list of related-items links to create alongside,
                once this definition exists (e.g. anatomy affected via
                ``relation:"AFFECTS"``). The destination must already exist.
                Valid combinations are returned by
                ``get_link_schema(src_type="allergy")``. Invalid combinations
                are silently dropped (kept vs dropped count is reported in the
                tool result).
        """
        # Normalize category to upper-case enum value.
        cat = (category or "OTHER").upper()

        # Validate + snapshot any proposed links (allergy is the src).
        link_specs: Dict[str, Any] = {"kept": [], "dropped": []}
        if links:
            from app.ai.tools.propose_link import build_link_specs
            from app.models.enums import EdgeEndpointType

            link_specs = await build_link_specs(
                ctx.db,
                ctx.tenant_id,
                EdgeEndpointType.ALLERGY,
                links,
            )

        task = {
            "schema_version": 1,
            "proposal_id": str(uuid4()),
            "task_type": "create_allergy_definition",
            "title": f"Define Allergy: {name}",
            "status": HitlTaskStatus.PROPOSED,
            "proposed_payload": {
                "name": name,
                "category": cat,
                "description": description or "",
                "typical_reactions": list(typical_reactions or []),
                "links": link_specs["kept"],
            },
            "context": {
                "patient_id": str(ctx.patient_id) if ctx.patient_id else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        await _notify_hitl_proposal(ctx, task)
        drop_count = len(link_specs["dropped"])
        result = {"__hitl__": True, "task": task}
        if drop_count:
            result["links_dropped"] = drop_count
            result["links_dropped_reasons"] = [
                d.get("reason", "unknown") for d in link_specs["dropped"]
            ]
        return json.dumps(result)

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
        await _notify_hitl_proposal(ctx, task)
        return json.dumps({"__hitl__": True, "task": task})

    return [
        propose_create_clinical_event,
        propose_record_biomarker_result,
        propose_prescribe_medication,
        propose_define_medication,
        propose_record_allergy,
        propose_define_allergy,
        propose_define_biomarker,
        propose_anatomy_graph_generation,
    ]
