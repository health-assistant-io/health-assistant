import json
import logging
from typing import Dict, Any, List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, or_
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from pydantic import BaseModel, Field

from app.services.ai_provider_service import AIProviderService
from app.services.ai_chatbot_tools import ChatbotTools
from app.services.chat_session_service import ChatSessionService
from app.core.config import settings
from app.models.tenant_model import TenantModel
from app.models.system_setting import SystemSetting
from app.models.enums import HitlTaskStatus
from app.utils.svg import sanitize_svg
from app.utils.prompt_guard import DEFENSE_PREAMBLE, check_user_input_safety
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.patient import Observation
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.examination_category import ExaminationCategory
from app.core.constants import CATEGORY_NAMES

logger = logging.getLogger(__name__)


def _parse_hitl_proposal(observation: Any) -> Optional[Dict[str, Any]]:
    """Inspect a chatbot tool result for a human-in-the-loop proposal.

    Proposal tools return a JSON string (or dict) shaped like:
        {"__hitl__": True, "task": { ...full task payload... }}
    Returns the task dict when a proposal is detected, otherwise None.
    Never raises — malformed observations are treated as non-HITL.
    """
    parsed = observation
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (ValueError, TypeError):
            return None
    if not isinstance(parsed, dict):
        return None
    if not parsed.get("__hitl__"):
        return None
    task = parsed.get("task") or parsed.get("proposal")
    if not isinstance(task, dict):
        return None
    return task


def _hitl_llm_feedback(task: Dict[str, Any]) -> str:
    """Concise note appended to the LLM history so the agent knows a proposal
    was emitted and that it must wait for human confirmation (no auto-retry)."""
    return (
        f"[HITL] A {task.get('task_type', 'action')} proposal has been rendered "
        f"as a review card for the user ({task.get('title', '')}). "
        f"The user must explicitly confirm or edit it before it takes effect. "
        f"Do NOT call the same proposal tool again for this request; continue "
        f"your explanation and wait for the user's response."
    )


def _hitl_resolution_summary(tasks: List[Dict[str, Any]]) -> str:
    """Build the structured outcomes message fed back to the agent when a HITL
    continuation turn is triggered. Reads status + final_payload + result +
    error from each resolved task. Returned text becomes the body of a synthetic
    user message that drives the continuation turn.

    Handles three cases per task:
      - CONFIRMED: surfaces trimmed draft + result ids
      - DISMISSED: notes the user rejected it
      - FAILED: surfaces the error
      - still PROPOSED: the user chose to continue WITHOUT answering (partial
        resume via the Continue button). Clearly labeled so the LLM knows the
        user hasn't acted on it yet.
    """
    lines = []
    confirmed = 0
    dismissed = 0
    failed = 0
    unanswered = 0
    terminal = HitlTaskStatus.terminal()
    for t in tasks:
        # Status may arrive as a HitlTaskStatus enum (fresh from a propose_*
        # tool) or as a plain string (loaded from JSONB). Normalize to a
        # plain string for display — f-string interpolation of an enum renders
        # as "HitlTaskStatus.CONFIRMED" which we don't want exposed to the LLM.
        status_raw = t.get("status", HitlTaskStatus.PROPOSED)
        status = status_raw.value if isinstance(status_raw, HitlTaskStatus) else status_raw
        resolved = t.get("resolved") or {}
        title = t.get("title") or t.get("task_type", "action")
        task_type = t.get("task_type", "action")
        proposal_id = t.get("proposal_id", "?")
        if status_raw == HitlTaskStatus.CONFIRMED:
            confirmed += 1
            parts = [f"CONFIRMED"]
            final = resolved.get("final_payload")
            if isinstance(final, dict) and final:
                # Trim large payloads — surface only short identifying fields.
                keys = ("name", "title", "slug", "coding_system", "code")
                trimmed = {k: final[k] for k in keys if k in final}
                if trimmed:
                    parts.append(f"draft={json.dumps(trimmed, ensure_ascii=False)}")
            result = resolved.get("result")
            if isinstance(result, dict) and result:
                keys = ("id", "biomarker_id", "catalog_id", "event_id", "slug")
                trimmed = {k: result[k] for k in keys if k in result}
                if trimmed:
                    parts.append(f"result={json.dumps(trimmed, ensure_ascii=False)}")
            err = resolved.get("error")
            if err:
                parts.append(f"error={err}")
            lines.append(
                f"- [{task_type}] {title} ({proposal_id[:8]}): " + ", ".join(parts) + "."
            )
        elif status_raw == HitlTaskStatus.DISMISSED:
            dismissed += 1
            lines.append(
                f"- [{task_type}] {title} ({proposal_id[:8]}): DISMISSED by the user."
            )
        elif status_raw == HitlTaskStatus.FAILED:
            failed += 1
            err = resolved.get("error", "unknown error")
            lines.append(
                f"- [{task_type}] {title} ({proposal_id[:8]}): FAILED ({err})."
            )
        else:
            unanswered += 1
            lines.append(
                f"- [{task_type}] {title} ({proposal_id[:8]}): NOT YET ANSWERED "
                f"(the user continued without resolving this item)."
            )

    # Build header with counts.
    counts = []
    if confirmed:
        counts.append(f"{confirmed} confirmed")
    if dismissed:
        counts.append(f"{dismissed} dismissed")
    if failed:
        counts.append(f"{failed} failed")
    if unanswered:
        counts.append(f"{unanswered} left unanswered")
    counts_str = ", ".join(counts) if counts else "no items"

    header = (
        f"[HITL RESOLUTION FEEDBACK] The user has finished acting on your "
        f"proposals ({counts_str})."
    )

    # Guidance varies based on whether there are unanswered items.
    if unanswered:
        guidance = (
            "Continue the conversation based on these outcomes. For confirmed "
            "items, briefly acknowledge what was saved. For dismissed/failed "
            "items, ask how the user wants to proceed. For items left "
            "UNANSWERED, do NOT re-propose them automatically — the user chose "
            "to skip them. You may ask if they want to address them now, or "
            "simply move on. Do not parrot payload data back verbatim."
        )
    else:
        guidance = (
            "Continue the conversation based on these outcomes: "
            "(a) if all confirmed, briefly acknowledge what was saved and offer "
            "any natural follow-up; (b) if any were dismissed, ask the user how "
            "they'd like to proceed instead; (c) do NOT re-propose the same "
            "actions. Do not repeat the payload details back to the user verbatim."
        )
    return f"{header}\n" + "\n".join(lines) + f"\n{guidance}"


def _hitl_resolved_brief(tasks: List[Dict[str, Any]]) -> Optional[str]:
    """Compact one-line summary of resolved tasks on a past assistant message,
    injected into history reconstruction so the agent remembers prior HITL
    outcomes across user turns. Returns None if no tasks were resolved."""
    bits = []
    terminal = HitlTaskStatus.terminal()
    for t in tasks:
        status_raw = t.get("status")
        if status_raw not in terminal:
            continue
        # Normalize enum -> plain string for display.
        status = status_raw.value if isinstance(status_raw, HitlTaskStatus) else status_raw
        task_type = t.get("task_type", "action")
        title = t.get("title") or task_type
        result = (t.get("resolved") or {}).get("result") or {}
        rid = ""
        if isinstance(result, dict):
            for k in ("id", "biomarker_id", "catalog_id", "event_id", "slug"):
                if k in result:
                    rid = f" (id={str(result[k])[:8]})"
                    break
        bits.append(f"{task_type} '{title}': {status}{rid}")
    if not bits:
        return None
    return "; ".join(bits)


class ExaminationMagicFillOutput(BaseModel):
    examination_date: Optional[str] = Field(
        None, description="The date of the examination (ISO format YYYY-MM-DD)"
    )
    notes: Optional[str] = Field(None, description="Clinical or doctor's notes")
    patient_notes: Optional[str] = Field(
        None, description="Patient's notes or reasons for the visit"
    )
    category: Optional[str] = Field(
        None, description="The clinical category SLUG of the examination"
    )
    doctor_names: List[str] = Field(
        default_factory=list, description="Names of doctors involved"
    )


class BiomarkerFormOutput(BaseModel):
    biomarker_name: Optional[str] = Field(
        None, description="The name of the biomarker identified (e.g. Glucose, WBC)"
    )
    value: Optional[float] = Field(
        None, description="The numerical value of the biomarker"
    )
    unit: Optional[str] = Field(
        None, description="The unit symbol (e.g., mg/dL, mmol/L)"
    )
    interpretation: Optional[str] = Field(
        None, description="One of: 'low', 'normal', 'high'"
    )
    note: Optional[str] = Field(
        None, description="A brief clinical note or observation"
    )


class MedicationFormOutput(BaseModel):
    medication_name: Optional[str] = Field(
        None, description="The name of the medication identified"
    )
    dosage: Optional[str] = Field(None, description="e.g., 500mg, 1 tablet")
    frequency_label: Optional[str] = Field(
        None, description="Human readable frequency, e.g., 'Once Daily', 'Twice Daily'"
    )
    reason: Optional[str] = Field(
        None, description="The reason for taking the medication"
    )
    note: Optional[str] = Field(None, description="Additional instructions or notes")


class BiomarkerDefinitionOutput(BaseModel):
    name: str = Field(..., description="The full clinical name of the biomarker")
    category: str = Field(
        ..., description="Clinical category (e.g., Hematology, Metabolic)"
    )
    unit_symbol: str = Field(..., description="Preferred unit (e.g., mg/dL, mmol/L)")
    coding_system: str = Field("loinc", description="The medical coding system to use (loinc, snomed, or custom)")
    code: Optional[str] = Field(None, description="The specific code from the coding system (e.g., '2345-7' for LOINC glucose)")
    aliases: List[str] = Field(default_factory=list, description="Common abbreviations")
    reference_range_min: Optional[float] = Field(None, description="Lower bound")
    reference_range_max: Optional[float] = Field(None, description="Upper bound")
    is_telemetry: bool = Field(False, description="Set to true if this metric is continuously tracked via IoT/wearables (e.g., heart rate, continuous glucose, steps)")
    info: str = Field(..., description="Detailed clinical significance and info")


class MedicationDefinitionOutput(BaseModel):
    name: str = Field(..., description="Full name of the medication")
    description: str = Field(..., description="Brief overview of the medication")
    indications: str = Field(..., description="What the drug is used for")
    dosage_info: str = Field(..., description="Typical dosage instructions")
    contraindications: str = Field(..., description="When the drug should not be used")
    side_effects: List[str] = Field(
        default_factory=list, description="List of common side effects"
    )


class CategoryIconSuggestionOutput(BaseModel):
    suggested_icons: List[str] = Field(
        ...,
        description="List of Lucide icon names (PascalCase, e.g. 'Activity', 'Droplet')",
    )


class CategoryIconGenerationOutput(BaseModel):
    svg_content: str = Field(..., description="Clean, minimalist SVG code for the icon")
    justification: Optional[str] = Field(
        None, description="Short explanation of why this icon design was chosen"
    )


class AIAssistanceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ai_provider_service = AIProviderService(db)
        self.chat_session_service = ChatSessionService(db)

    async def _get_max_iterations(self, tenant_id: UUID) -> int:
        """Get the maximum iterations for the AI reasoning loop"""
        # 1. Check Tenant-specific setting
        if tenant_id:
            result = await self.db.execute(
                select(TenantModel.settings).where(TenantModel.id == tenant_id)
            )
            tenant_settings = result.scalar_one_or_none()
            if tenant_settings and "ai_agent_max_iterations" in tenant_settings:
                try:
                    return int(tenant_settings["ai_agent_max_iterations"])
                except (ValueError, TypeError):
                    pass
        
        # 2. Check System-wide DB setting
        system_max = await SystemSetting.get_value(self.db, "ai_agent_max_iterations")
        if system_max is not None:
            try:
                return int(system_max)
            except (ValueError, TypeError):
                pass

        # 3. Fallback to ENV setting
        return settings.AI_AGENT_MAX_ITERATIONS

    async def _get_recent_biomarkers_context(
        self, patient_id: UUID, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get lightweight context of recent biomarkers for style matching"""
        patient_ref = f"Patient/{patient_id}"
        result = await self.db.execute(
            select(Observation)
            .where(Observation.subject["reference"].astext == patient_ref)
            .order_by(desc(Observation.effective_datetime))
            .limit(limit)
        )
        observations = result.scalars().all()
        return [
            {
                "name": obs.code.get("text"),
                "unit": obs.value_quantity.get("unit") if obs.value_quantity else None,
            }
            for obs in observations
        ]

    async def _get_recent_medications_context(
        self, patient_id: UUID, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get lightweight context of recent medications for style matching"""
        result = await self.db.execute(
            select(Medication)
            .where(Medication.patient_id == patient_id)
            .order_by(desc(Medication.updated_at))
            .limit(limit)
        )
        meds = result.scalars().all()
        return [{"name": med.code.get("text"), "dosage": med.dosage} for med in meds]

    async def assist(
        self,
        task_type: str,
        user_input: str,
        reference_image: Optional[str] = None,
        context: Dict[str, Any] = None,
        tenant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        stream: bool = False,
    ):
        """Main entry point for AI assistance.

        Every task_type passes user_input through the prompt-injection guard
        before it reaches the LLM. The guard is non-blocking by default
        (logs WARNING, proceeds) — the HITL wall remains the structural
        protection for clinical writes. ``high``-risk input is still processed
        but the signal is available in the logs for audit correlation.
        """
        from app.utils.prompt_guard import check_user_input_safety

        if user_input:
            check_user_input_safety(user_input, context=f"assist:{task_type}")

        llm = await self.ai_provider_service.get_llm(task_type, tenant_id, user_id)

        if task_type == "fill_biomarker_form":
            return await self._fill_biomarker_form(llm, user_input, context)
        elif task_type == "fill_medication_form":
            return await self._fill_medication_form(llm, user_input, context)
        elif task_type == "magic_fill_examination":
            return await self._magic_fill_examination(llm, user_input, context)
        elif task_type == "define_biomarker":
            return await self._define_biomarker(llm, user_input, context)
        elif task_type == "define_medication":
            return await self._define_medication(llm, user_input, context)
        elif task_type == "suggest_category_icon":
            return await self._suggest_category_icon(llm, user_input, context)
        elif task_type == "generate_category_icon":
            return await self._generate_category_icon(
                llm, user_input, reference_image, context
            )
        elif task_type == "chat":
            if stream:
                return self._chat_stream(llm, user_input, context, tenant_id, user_id)
            return await self._general_chat(
                llm, user_input, context, tenant_id, user_id
            )
        else:
            raise ValueError(f"Unknown task type: {task_type}")

    async def _generate_session_title(self, llm, user_input: str) -> str:
        """Generate a short title for a chat session"""
        prompt = f"Generate a very short (max 5 words) descriptive title for a medical chat session starting with this message: '{user_input}'. Return ONLY the title text, no quotes."
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            return response.content.strip().strip('"')
        except Exception:
            return "New Chat"

    async def _chat_stream(
        self,
        llm,
        user_input: str,
        context: Dict[str, Any],
        tenant_id: UUID,
        user_id: UUID,
    ):
        """Stream a chat response with tool support and delta-only yielding to prevent duplication"""
        patient_id = context.get("patient_id")
        examination_id = context.get("examination_id")
        biomarker_id = context.get("biomarker_id")
        medication_id = context.get("medication_id")
        current_tab = context.get("current_tab")
        session_id_str = context.get("session_id")
        session_id = UUID(session_id_str) if session_id_str else None

        # Auto-create session if not provided
        if not session_id and user_id and tenant_id:
            title = await self._generate_session_title(llm, user_input)
            p_id = UUID(patient_id) if patient_id else None
            session = await self.chat_session_service.create_session(
                user_id=user_id, tenant_id=tenant_id, patient_id=p_id, title=title
            )
            session_id = session.id
            yield f"[SESSION_ID] {session_id}"

        # Save user message
        if session_id:
            await self.chat_session_service.save_message(
                session_id=session_id, role="user", content={"text": user_input}
            )

        tools = []
        if patient_id and tenant_id:
            chatbot_tools = ChatbotTools(self.db, tenant_id, UUID(patient_id), examination_id=UUID(examination_id) if examination_id else None)
            tools = chatbot_tools.get_tools()
            try:
                from app.services.integration_tool_aggregator import aggregate as integration_aggregate
                integration_tools = await integration_aggregate(
                    self.db, user_id, tenant_id, UUID(patient_id)
                )
                tools = tools + integration_tools
            except Exception as e:
                logger.warning(f"Failed to load integration tools for chat (continuing with built-ins): {e}")

        llm_with_tools = llm.bind_tools(tools) if tools else llm

        system_prompt = f"""{DEFENSE_PREAMBLE}

        You are Health Assistant AI, a professional medical data assistant.
        Answer the user's questions clearly and professionally using Markdown.
        
        FORMATTING RULES:
        - Use Markdown TABLES for presenting lists of data (biomarkers, medications, examinations) when multiple records are involved. This makes the data easier to read.
        - For scientific units with exponents (like 10^3, 10^6, m^2), use UNICODE superscript characters (e.g., ³, ⁶, ⁹, ²) instead of the caret (^) symbol.
        
        BIOMARKER & TELEMETRY RULES:
        1. Discovery: If asked about a health metric, ALWAYS use the `search_available_biomarkers` tool first to find its exact `id` and verify its `is_telemetry` type (unless you already know it).
        2. Clinical Data: If `is_telemetry` is FALSE, you MUST fetch the data using `get_biomarker_history`. This targets standard FHIR laboratory records.
        3. Telemetry Data: If `is_telemetry` is TRUE (e.g., heart rate, steps, continuous monitors), you MUST fetch the data using `get_aggregated_biomarker_trends`. This targets high-frequency TimescaleDB records. NEVER use `get_biomarker_history` for telemetry data.
        
        REPETITION POLICY:
        - DO NOT repeat your own preamble or any text you generated before calling a tool.
        - Focus ONLY on the new information derived from the tool results.

        CITATION POLICY:
        - When you use information from a tool result, you MUST cite it inline using the format [Ref: type=uuid].
        - Use "observation" for a specific lab result value (use the 'id' field).
        - Use "biomarker" for general biomarker info or TELEMETRY TRENDS. You MUST use the 'id' (UUID), not the slug.
        - Use "medication" for prescriptions, "examination" for clinical visits, "event" for health journeys, and "document" for uploaded reports/images.
        - GRANULARITY: If you report multiple data points (e.g., several biomarkers from a single visit), cite EACH one individually with its specific "observation" ID. 
        - TELEMETRY: For high-frequency telemetry (heart rate, steps), individual observation IDs are not available. Cite the "biomarker" ID instead.
        - PREFERENCE: Always prefer a specific "observation" citation over a general "examination" or "document" citation when reporting numerical lab results or specific findings.

        - Example: "Total Cholesterol was 225 mg/dL [Ref: observation=uuid1] and LDL was 149 mg/dL [Ref: observation=uuid2]."
        - Example Telemetry: "Your heart rate averaged 72 bpm [Ref: biomarker=uuid3] during your last workout."
        - ALWAYS provide the FULL UUID or SLUG. NEVER truncate it with dots.

        HUMAN-IN-THE-LOOP (PROPOSED ACTIONS):
        - You CANNOT create, modify, or delete clinical data directly. For any CREATE/write request, call the matching `propose_*` tool:
          * `propose_create_clinical_event` — a new health journey / event (e.g. "track my pregnancy", "log a surgery recovery").
          * `propose_add_biomarker_to_examination` — record a value for an EXISTING biomarker on an examination.
          * `propose_add_medication` — prescribe an EXISTING catalog drug to the patient.
          * `propose_create_biomarker_definition` — define a NEW biomarker in the catalog (the metric does not exist yet).
          * `propose_create_medication_definition` — define a NEW drug in the catalog (the drug does not exist yet).
        - A `propose_*` tool renders an interactive review card prefilled with your suggestion. The user edits and must explicitly confirm before anything is saved. You are preparing a DRAFT, not performing the action.
        - NEVER claim the action succeeded. Say "I've prepared a draft for your review" — not "I created the event".
        - Before proposing a *definition* (create_*_definition), first call `search_available_biomarkers` / `search_medications` to confirm the entity truly does not exist. If it does, use `propose_add_*` instead.

        MULTIPLE PROPOSALS IN ONE TURN:
        - You MAY call more than one `propose_*` tool in a single turn when the actions are INDEPENDENT (e.g. "add medications X, Y, and Z" → three `propose_add_medication` calls). Each renders its own review card; the user resolves them in any order.
        - When actions are DEPENDENT — one cannot validly be committed without another first being saved — split them across turns. Example: the user asks to record a value for biomarker X on this exam, but X does not exist in the catalog. First call `propose_create_biomarker_definition` for X and STOP. After the user confirms it, the next turn (auto-triggered) will let you call `propose_add_biomarker_to_examination` for the now-existing biomarker. Do NOT try to emit both in the same turn.
        - After emitting one or more proposals, briefly explain what you prepared (one or two sentences) and STOP — wait for the user to confirm, edit, or reject. The user's resolution will automatically trigger your next turn; you do not need to ask them to "type continue".

        RESOLUTION FEEDBACK:
        - When you receive a `[HITL RESOLUTION FEEDBACK]` message, the user has finished acting on your prior proposals. Read the outcomes carefully: each line tells you whether a step was CONFIRMED (with the saved resource id), DISMISSED, or FAILED.
        - Use confirmed results (e.g. a newly created biomarker_id or slug) to drive dependent next steps.
        - Keep your continuation concise and useful; do not parrot the payload data back at the user.
        """

        if examination_id:
            system_prompt += f"\n\nCONTEXT: The user is currently viewing examination {examination_id}. If they ask about 'this visit' or 'this examination', use this ID to fetch details."

        if biomarker_id:
            system_prompt += f"\n\nCONTEXT: The user is currently viewing biomarker {biomarker_id}. If they ask about 'this metric' or 'this biomarker', use this ID to fetch definition and info."

        if medication_id:
            system_prompt += f"\n\nCONTEXT: The user is currently viewing medication {medication_id}. If they ask about 'this medicine' or 'this drug', use this ID to fetch catalog details."

        if current_tab:
            system_prompt += f"\n\nCONTEXT: The user is currently in the '{current_tab}' tab of the chat assistant interface. This might help you prioritize certain types of information or actions."

        # Bind tools if available
        llm_with_tools = llm.bind_tools(tools) if tools else llm

        current_history = [SystemMessage(content=system_prompt)]

        # Load history from DB
        if session_id:
            past_messages = await self.chat_session_service.get_session_messages(
                session_id, user_id, tenant_id
            )
            # Exclude the message we just saved (which is the current user input) to avoid duplication
            for msg in past_messages[:-1][-10:]:
                if msg.role == "user":
                    current_history.append(
                        HumanMessage(content=msg.content.get("text"))
                    )
                elif msg.role == "assistant":
                    # Reconstruct tool calls if any, filtering out extra keys like 'result'
                    # that LangChain's AIMessage doesn't accept
                    t_calls = []
                    for tc in msg.tool_calls or []:
                        t_calls.append(
                            {
                                "name": tc["name"],
                                "args": tc["args"],
                                "id": tc.get("id", f"call_{msg.id.hex[:8]}"),
                            }
                        )
                    current_history.append(
                        AIMessage(content=msg.content.get("text"), tool_calls=t_calls)
                    )
                    # Inject a compact HITL outcome note so the agent remembers
                    # what the user confirmed/dismissed on prior turns. Linked
                    # to the last tool_call id of this message when available.
                    msg_tasks = msg.tasks or []
                    brief = _hitl_resolved_brief(msg_tasks)
                    if brief:
                        last_tc_id = (
                            t_calls[-1]["id"] if t_calls else f"hitl_{msg.id.hex[:8]}"
                        )
                        current_history.append(
                            ToolMessage(
                                content=f"[HITL outcomes for this turn: {brief}]",
                                tool_call_id=last_tc_id,
                            )
                        )

        # Add current user input as the last message
        current_history.append(HumanMessage(content=user_input))

        # TRACKING ACROSS ITERATIONS: Keep track of what we've yielded to the user
        total_content_yielded = ""
        all_tool_calls = []
        all_citations = []
        all_tasks = []
        # Tracks the ChatMessage row we proactively saved when the first HITL
        # task was emitted. If non-None, the final save UPDATES this row instead
        # of inserting a new one. This ensures tasks survive stream interruptions.
        proactive_message = None

        # Reasoning loop
        max_iterations = await self._get_max_iterations(tenant_id)
        for i in range(max_iterations):
            final_chunk = None
            tool_name_yielded = set()

            # TRACKING WITHIN ITERATION: Handle accumulated chunks from some providers
            content_received_this_iter = ""
            content_yielded_this_iter = ""

            async for chunk in llm_with_tools.astream(current_history):
                if chunk.tool_call_chunks:
                    for tc_chunk in chunk.tool_call_chunks:
                        tc_name = tc_chunk.get("name")
                        if tc_name and tc_name not in tool_name_yielded:
                            yield f"[TOOL_CALL_START] {tc_name}"
                            tool_name_yielded.add(tc_name)

                if chunk.content:
                    if (
                        content_received_this_iter
                        and chunk.content.startswith(content_received_this_iter)
                        and len(chunk.content) > len(content_received_this_iter)
                    ):
                        content_received_this_iter = chunk.content
                    else:
                        content_received_this_iter += chunk.content

                    if not total_content_yielded:
                        delta = content_received_this_iter
                    elif content_received_this_iter.startswith(total_content_yielded):
                        delta = content_received_this_iter[len(total_content_yielded) :]
                    elif total_content_yielded.startswith(content_received_this_iter):
                        delta = ""
                    else:
                        delta = content_received_this_iter

                    if content_yielded_this_iter and delta.startswith(
                        content_yielded_this_iter
                    ):
                        actual_yield = delta[len(content_yielded_this_iter) :]
                    else:
                        actual_yield = delta

                    if actual_yield:
                        yield actual_yield
                        content_yielded_this_iter += actual_yield
                        total_content_yielded += actual_yield

                final_chunk = chunk if final_chunk is None else final_chunk + chunk

            if not final_chunk or not final_chunk.tool_calls:
                break

            logger.info(
                f"AI Assistance: Tool calls detected, entering reasoning loop iteration {i + 1}"
            )

            message_for_history = AIMessage(
                content=content_received_this_iter, tool_calls=final_chunk.tool_calls
            )
            current_history.append(message_for_history)

            for tool_call in final_chunk.tool_calls:
                tool_name = tool_call["name"]
                selected_tool = next((t for t in tools if t.name == tool_name), None)
                if selected_tool:
                    yield f"[TOOL_CALL_EXEC] {tool_name}"
                    observation = await selected_tool.ainvoke(tool_call["args"])

                    # Detect human-in-the-loop proposals
                    hitl_task = _parse_hitl_proposal(observation)

                    if hitl_task:
                        all_tasks.append(hitl_task)
                        feedback = _hitl_llm_feedback(hitl_task)
                        # Trimmed tool result so the chip resolves to "finished"
                        trimmed = {
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": feedback,
                        }
                        yield f"[TOOL_CALL_RESULT] {json.dumps(trimmed)}"
                        # Dedicated sentinel drives the interactive task card
                        yield f"[HITL_TASK] {json.dumps(hitl_task)}"
                        all_tool_calls.append(
                            {
                                "id": tool_call.get("id"),
                                "name": tool_name,
                                "args": tool_call["args"],
                                "result": feedback,
                            }
                        )
                        # Proposals are NOT data sources — no citation.
                        current_history.append(
                            ToolMessage(
                                content=feedback, tool_call_id=tool_call["id"]
                            )
                        )
                        # PROACTIVE PERSISTENCE: save the message immediately
                        # so the task card survives stream interruptions (LLM
                        # errors, client disconnects, etc). Without this, the
                        # save at the end of the generator would never run and
                        # the task would be lost from the DB — breaking both
                        # /resolve (404) and /resume ("No task-bearing message").
                        if session_id and proactive_message is None:
                            try:
                                proactive_message = (
                                    await self.chat_session_service.save_message(
                                        session_id=session_id,
                                        role="assistant",
                                        content={"text": total_content_yielded},
                                        tool_calls=list(all_tool_calls),
                                        citations=list(all_citations),
                                        tasks=list(all_tasks),
                                    )
                                )
                                logger.info(
                                    f"HITL task proactively saved to message "
                                    f"{proactive_message.id} (task_type="
                                    f"{hitl_task.get('task_type')})"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to proactively save HITL task: {e}",
                                    exc_info=True,
                                )
                    else:
                        args_str = json.dumps(tool_call["args"])
                        result_str = str(observation)
                        payload_dict = {
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": result_str,
                        }
                        yield f"[TOOL_CALL_RESULT] {json.dumps(payload_dict)}"
                        yield f"[CITATION] {selected_tool.name}"

                        all_tool_calls.append(
                            {
                                "id": tool_call.get("id"),
                                "name": tool_name,
                                "args": tool_call["args"],
                                "result": result_str,
                            }
                        )
                        all_citations.append(selected_tool.name)

                        current_history.append(
                            ToolMessage(
                                content=str(observation), tool_call_id=tool_call["id"]
                            )
                        )
                else:
                    current_history.append(
                        ToolMessage(
                            content=f"Tool {tool_name} not found.",
                            tool_call_id=tool_call["id"],
                        )
                    )
            yield "[TOOL_CALL_FINISHED]"

        # Final save: if we proactively saved a message when the first HITL
        # task was emitted, UPDATE it with the complete content/tool_calls/
        # citations/tasks. Otherwise, insert a new message as usual.
        if session_id:
            if proactive_message is not None:
                await self.chat_session_service.update_message_fields(
                    proactive_message,
                    content={"text": total_content_yielded},
                    tool_calls=all_tool_calls,
                    citations=all_citations,
                    tasks=all_tasks or None,
                )
            else:
                await self.chat_session_service.save_message(
                    session_id=session_id,
                    role="assistant",
                    content={"text": total_content_yielded},
                    tool_calls=all_tool_calls,
                    citations=all_citations,
                    tasks=all_tasks or None,
                )

    async def resume_after_hitl(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        message_id: Optional[UUID] = None,
    ):
        """Stream a continuation turn after the user has resolved one or more
        HITL task cards. Reads the resolved tasks from the target message's
        `tasks` JSONB, builds a structured outcomes summary, persists it as a
        user message, then runs the same reasoning loop as `_chat_stream`.

        Guards:
          * Session must belong to (user_id, tenant_id).
          * Target message must exist and have tasks.
          * Every task on the target message must be in a terminal state
            (confirmed | dismissed | failed) — otherwise 409.

        Yields the same SSE sentinel vocabulary as `_chat_stream`.
        """
        from app.models.chat_model import ChatSession as _ChatSession

        # Verify ownership + pull patient_id for tool context.
        session_result = await self.db.execute(
            select(_ChatSession).where(
                _ChatSession.id == session_id,
                _ChatSession.user_id == user_id,
                _ChatSession.tenant_id == tenant_id,
            )
        )
        session = session_result.scalars().first()
        if not session:
            raise ValueError("Session not found or access denied.")

        target = await self.chat_session_service.find_resumable_message(
            session_id, user_id, tenant_id, message_id=message_id
        )

        if target and target.tasks:
            # Check for pending (unanswered) tasks. We do NOT hard-block the
            # resume — the user may have clicked the "Continue" button to
            # proceed with partial answers. Just log it for diagnostics; the
            # summary will label unanswered items as "NOT YET ANSWERED".
            terminal = HitlTaskStatus.terminal()
            pending = [
                t for t in target.tasks
                if isinstance(t, dict) and t.get("status") not in terminal
            ]
            if pending:
                logger.info(
                    f"HITL resume: {len(pending)} task(s) still pending in "
                    f"session {session_id} — proceeding with partial resume."
                )
            summary = _hitl_resolution_summary(target.tasks)
        else:
            # GRACEFUL FALLBACK: no task-bearing message was found (e.g. the
            # proposing stream was interrupted before the proactive-save fix,
            # or the message was from a very old session). Don't error — run
            # the continuation with a generic prompt. The audit note appended
            # by /resolve ("✓ Confirmed and saved: ...") will be visible in
            # the session history, so the LLM can still acknowledge the
            # user's action.
            logger.warning(
                f"HITL resume: no task-bearing message found in session "
                f"{session_id}; falling back to generic continuation."
            )
            summary = (
                "[HITL RESOLUTION FEEDBACK] The user has resolved a proposed "
                "action from your previous turn. The outcome is recorded in "
                "the session history (look for the '✓ Confirmed and saved:' "
                "or dismissal note). Please acknowledge the outcome and "
                "offer any natural follow-up."
            )

        # Persist the summary as a user message so it reconstructs naturally
        # into LLM history on subsequent turns.
        await self.chat_session_service.save_message(
            session_id=session_id,
            role="user",
            content={"text": summary},
        )

        patient_id = str(session.patient_id) if session.patient_id else None
        examination_id = None  # Resume turns don't carry exam context.

        # Build tools (same as _chat_stream).
        tools = []
        if patient_id and tenant_id:
            chatbot_tools = ChatbotTools(
                self.db, tenant_id, UUID(patient_id),
                examination_id=None,
            )
            tools = chatbot_tools.get_tools()
            try:
                from app.services.integration_tool_aggregator import (
                    aggregate as integration_aggregate,
                )
                integration_tools = await integration_aggregate(
                    self.db, user_id, tenant_id, UUID(patient_id)
                )
                tools = tools + integration_tools
            except Exception as e:
                logger.warning(
                    f"Failed to load integration tools for resume (continuing with built-ins): {e}"
                )

        llm = await self.ai_provider_service.get_llm(
            "chat", tenant_id=tenant_id, user_id=user_id
        )
        llm_with_tools = llm.bind_tools(tools) if tools else llm

        system_prompt = f"""{DEFENSE_PREAMBLE}

        You are Health Assistant AI, a professional medical data assistant.
        Answer the user's questions clearly and professionally using Markdown.

        FORMATTING RULES:
        - Use Markdown TABLES for presenting lists of data when multiple records are involved.
        - For scientific units with exponents (like 10^3, 10^6, m^2), use UNICODE superscript characters (e.g., ³, ⁶, ⁹, ²) instead of the caret (^) symbol.

        BIOMARKER & TELEMETRY RULES:
        1. Discovery: If asked about a health metric, ALWAYS use `search_available_biomarkers` first to find its exact `id` and verify `is_telemetry`.
        2. Clinical Data: If `is_telemetry` is FALSE, you MUST use `get_biomarker_history`.
        3. Telemetry Data: If `is_telemetry` is TRUE, you MUST use `get_aggregated_biomarker_trends`. NEVER use `get_biomarker_history` for telemetry.

        REPETITION POLICY:
        - DO NOT repeat your own preamble or any text generated before calling a tool.

        CITATION POLICY:
        - Cite tool data inline using [Ref: type=uuid]. Use "observation" for specific lab values, "biomarker" for telemetry trends, "medication"/"examination"/"event"/"document" otherwise. Always provide the FULL UUID.

        HUMAN-IN-THE-LOOP (PROPOSED ACTIONS):
        - You CANNOT write clinical data directly. Use `propose_*` tools to render review cards.
        - You MAY propose multiple INDEPENDENT actions in one turn. For DEPENDENT actions, propose the prerequisite first and STOP; the user's resolution auto-triggers your next turn.
        - NEVER claim an action succeeded until the user confirms it.

        RESOLUTION FEEDBACK:
        - When you receive a `[HITL RESOLUTION FEEDBACK]` message, the user has finished acting on your prior proposals. Use confirmed results (resource ids) to drive dependent next steps. If actions were dismissed, ask the user how to proceed. Do NOT re-propose the same actions or parrot payload data back.
        """

        current_history = [SystemMessage(content=system_prompt)]

        # Load history (mirrors _chat_stream reconstruction, including HITL
        # outcome injection for past turns).
        past_messages = await self.chat_session_service.get_session_messages(
            session_id, user_id, tenant_id
        )
        # Exclude the summary we just saved (it's the last message) — we'll
        # append it explicitly below.
        for msg in past_messages[:-1][-10:]:
            if msg.role == "user":
                current_history.append(
                    HumanMessage(content=msg.content.get("text"))
                )
            elif msg.role == "assistant":
                t_calls = []
                for tc in msg.tool_calls or []:
                    t_calls.append(
                        {
                            "name": tc["name"],
                            "args": tc["args"],
                            "id": tc.get("id", f"call_{msg.id.hex[:8]}"),
                        }
                    )
                current_history.append(
                    AIMessage(content=msg.content.get("text"), tool_calls=t_calls)
                )
                msg_tasks = msg.tasks or []
                brief = _hitl_resolved_brief(msg_tasks)
                if brief:
                    last_tc_id = (
                        t_calls[-1]["id"] if t_calls else f"hitl_{msg.id.hex[:8]}"
                    )
                    current_history.append(
                        ToolMessage(
                            content=f"[HITL outcomes for this turn: {brief}]",
                            tool_call_id=last_tc_id,
                        )
                    )

        # The summary is the driving input for this continuation turn.
        current_history.append(HumanMessage(content=summary))

        total_content_yielded = ""
        all_tool_calls = []
        all_citations = []
        all_tasks = []
        # Proactive-save tracker (same pattern as _chat_stream — ensures HITL
        # tasks proposed during the continuation survive stream interruptions).
        proactive_message = None

        max_iterations = await self._get_max_iterations(tenant_id)
        for i in range(max_iterations):
            final_chunk = None
            tool_name_yielded = set()
            content_received_this_iter = ""
            content_yielded_this_iter = ""

            async for chunk in llm_with_tools.astream(current_history):
                if chunk.tool_call_chunks:
                    for tc_chunk in chunk.tool_call_chunks:
                        tc_name = tc_chunk.get("name")
                        if tc_name and tc_name not in tool_name_yielded:
                            yield f"[TOOL_CALL_START] {tc_name}"
                            tool_name_yielded.add(tc_name)

                if chunk.content:
                    if (
                        content_received_this_iter
                        and chunk.content.startswith(content_received_this_iter)
                        and len(chunk.content) > len(content_received_this_iter)
                    ):
                        content_received_this_iter = chunk.content
                    else:
                        content_received_this_iter += chunk.content

                    if not total_content_yielded:
                        delta = content_received_this_iter
                    elif content_received_this_iter.startswith(total_content_yielded):
                        delta = content_received_this_iter[len(total_content_yielded):]
                    elif total_content_yielded.startswith(content_received_this_iter):
                        delta = ""
                    else:
                        delta = content_received_this_iter

                    if content_yielded_this_iter and delta.startswith(
                        content_yielded_this_iter
                    ):
                        actual_yield = delta[len(content_yielded_this_iter):]
                    else:
                        actual_yield = delta

                    if actual_yield:
                        yield actual_yield
                        content_yielded_this_iter += actual_yield
                        total_content_yielded += actual_yield

                final_chunk = chunk if final_chunk is None else final_chunk + chunk

            if not final_chunk or not final_chunk.tool_calls:
                break

            logger.info(
                f"AI Assistance (resume): tool calls detected, entering reasoning loop iteration {i + 1}"
            )

            message_for_history = AIMessage(
                content=content_received_this_iter, tool_calls=final_chunk.tool_calls
            )
            current_history.append(message_for_history)

            for tool_call in final_chunk.tool_calls:
                tool_name = tool_call["name"]
                selected_tool = next((t for t in tools if t.name == tool_name), None)
                if selected_tool:
                    yield f"[TOOL_CALL_EXEC] {tool_name}"
                    observation = await selected_tool.ainvoke(tool_call["args"])

                    hitl_task = _parse_hitl_proposal(observation)
                    if hitl_task:
                        all_tasks.append(hitl_task)
                        feedback = _hitl_llm_feedback(hitl_task)
                        trimmed = {
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": feedback,
                        }
                        yield f"[TOOL_CALL_RESULT] {json.dumps(trimmed)}"
                        yield f"[HITL_TASK] {json.dumps(hitl_task)}"
                        all_tool_calls.append(
                            {
                                "id": tool_call.get("id"),
                                "name": tool_name,
                                "args": tool_call["args"],
                                "result": feedback,
                            }
                        )
                        current_history.append(
                            ToolMessage(
                                content=feedback, tool_call_id=tool_call["id"]
                            )
                        )
                        # PROACTIVE PERSISTENCE: save immediately so the task
                        # survives stream interruptions (same rationale as
                        # _chat_stream).
                        if proactive_message is None:
                            try:
                                proactive_message = (
                                    await self.chat_session_service.save_message(
                                        session_id=session_id,
                                        role="assistant",
                                        content={"text": total_content_yielded},
                                        tool_calls=list(all_tool_calls),
                                        citations=list(all_citations),
                                        tasks=list(all_tasks),
                                    )
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to proactively save resume HITL task: {e}",
                                    exc_info=True,
                                )
                    else:
                        args_str = json.dumps(tool_call["args"])
                        result_str = str(observation)
                        payload_dict = {
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": result_str,
                        }
                        yield f"[TOOL_CALL_RESULT] {json.dumps(payload_dict)}"
                        yield f"[CITATION] {selected_tool.name}"
                        all_tool_calls.append(
                            {
                                "id": tool_call.get("id"),
                                "name": tool_name,
                                "args": tool_call["args"],
                                "result": result_str,
                            }
                        )
                        all_citations.append(selected_tool.name)
                        current_history.append(
                            ToolMessage(
                                content=str(observation), tool_call_id=tool_call["id"]
                            )
                        )
                else:
                    current_history.append(
                        ToolMessage(
                            content=f"Tool {tool_name} not found.",
                            tool_call_id=tool_call["id"],
                        )
                    )
            yield "[TOOL_CALL_FINISHED]"

        # Final save: update the proactively-saved message (if any) with the
        # complete content, or insert a new message as usual.
        if proactive_message is not None:
            await self.chat_session_service.update_message_fields(
                proactive_message,
                content={"text": total_content_yielded},
                tool_calls=all_tool_calls,
                citations=all_citations,
                tasks=all_tasks or None,
            )
        else:
            await self.chat_session_service.save_message(
                session_id=session_id,
                role="assistant",
                content={"text": total_content_yielded},
                tool_calls=all_tool_calls,
                citations=all_citations,
                tasks=all_tasks or None,
            )

    async def _general_chat(
        self,
        llm,
        user_input: str,
        context: Dict[str, Any],
        tenant_id: UUID,
        user_id: UUID,
    ) -> Dict[str, Any]:
        """Non-streaming chat with tool support"""
        patient_id = context.get("patient_id")
        examination_id = context.get("examination_id")
        biomarker_id = context.get("biomarker_id")
        medication_id = context.get("medication_id")
        current_tab = context.get("current_tab")
        session_id_str = context.get("session_id")
        session_id = UUID(session_id_str) if session_id_str else None

        # Auto-create session if not provided
        if not session_id and user_id and tenant_id:
            title = await self._generate_session_title(llm, user_input)
            p_id = UUID(patient_id) if patient_id else None
            session = await self.chat_session_service.create_session(
                user_id=user_id, tenant_id=tenant_id, patient_id=p_id, title=title
            )
            session_id = session.id

        # Save user message
        if session_id:
            await self.chat_session_service.save_message(
                session_id=session_id, role="user", content={"text": user_input}
            )

        tools = []
        if patient_id and tenant_id:
            chatbot_tools = ChatbotTools(self.db, tenant_id, UUID(patient_id), examination_id=UUID(examination_id) if examination_id else None)
            tools = chatbot_tools.get_tools()
            try:
                from app.services.integration_tool_aggregator import aggregate as integration_aggregate
                integration_tools = await integration_aggregate(
                    self.db, user_id, tenant_id, UUID(patient_id)
                )
                tools = tools + integration_tools
            except Exception as e:
                logger.warning(f"Failed to load integration tools for chat (continuing with built-ins): {e}")

        llm_with_tools = llm.bind_tools(tools) if tools else llm

        system_prompt = """You are Health Assistant AI, a helpful medical data assistant.
        Always answer using Markdown. Use Markdown TABLES for lists of biomarkers or medications to make them readable.
        
        BIOMARKER & TELEMETRY RULES:
        1. Discovery: If asked about a health metric, ALWAYS use the `search_available_biomarkers` tool first to find its exact `id` and verify its `is_telemetry` type (unless you already know it).
        2. Clinical Data: If `is_telemetry` is FALSE, you MUST fetch the data using `get_biomarker_history`. This targets standard FHIR laboratory records.
        3. Telemetry Data: If `is_telemetry` is TRUE (e.g., heart rate, steps, continuous monitors), you MUST fetch the data using `get_aggregated_biomarker_trends`. This targets high-frequency TimescaleDB records. NEVER use `get_biomarker_history` for telemetry data.

        CITATION POLICY:
        - When you use information from a tool result, you MUST cite it inline using the format [Ref: type=uuid].
        - Use "observation" for a specific lab result value (use the 'id' field).
        - Use "biomarker" for general biomarker info or TELEMETRY TRENDS. You MUST use the 'id' (UUID), not the slug.
        - Use "medication" for prescriptions, "examination" for clinical visits, "event" for health journeys, and "document" for uploaded reports/images.
        - GRANULARITY: If you report multiple data points (e.g., several biomarkers from a single visit), cite EACH one individually with its specific "observation" ID. 
        - TELEMETRY: For high-frequency telemetry (heart rate, steps), individual observation IDs are not available. Cite the "biomarker" ID instead.
        - PREFERENCE: Always prefer a specific "observation" citation over a general "examination" or "document" citation when reporting numerical lab results or specific findings.

        HUMAN-IN-THE-LOOP (PROPOSED ACTIONS):
        - You CANNOT create, modify, or delete clinical data directly. For any CREATE/write request, call the matching `propose_*` tool (`propose_create_clinical_event`, `propose_add_biomarker_to_examination`, `propose_add_medication`, `propose_create_biomarker_definition`, `propose_create_medication_definition`).
        - A `propose_*` tool renders an interactive review card; the user must explicitly confirm before anything is saved. You prepare a DRAFT, never perform the action.
        - NEVER claim the action succeeded. Say "I've prepared a draft for your review".
        - You MAY propose multiple INDEPENDENT actions in one turn. For DEPENDENT actions (one requires another to be saved first), propose the prerequisite first and STOP; the user's resolution auto-triggers your next turn.
        """
        if examination_id:
            system_prompt += f"\n\nCONTEXT: The user is currently viewing examination {examination_id}. If they ask about 'this visit' or 'this examination', use this ID to fetch details."

        if biomarker_id:
            system_prompt += f"\n\nCONTEXT: The user is currently viewing biomarker {biomarker_id}. If they ask about 'this metric' or 'this biomarker', use this ID to fetch definition and info."

        if medication_id:
            system_prompt += f"\n\nCONTEXT: The user is currently viewing medication {medication_id}. If they ask about 'this medicine' or 'this drug', use this ID to fetch catalog details."

        if current_tab:
            system_prompt += f"\n\nCONTEXT: The user is currently in the '{current_tab}' tab of the chat assistant interface. This might help you prioritize certain types of information or actions."

        current_history = [SystemMessage(content=system_prompt)]

        # Load history
        if session_id:
            past_messages = await self.chat_session_service.get_session_messages(
                session_id, user_id, tenant_id
            )
            # Exclude the message we just saved
            for msg in past_messages[:-1][-10:]:
                if msg.role == "user":
                    current_history.append(
                        HumanMessage(content=msg.content.get("text"))
                    )
                elif msg.role == "assistant":
                    t_calls = []
                    for tc in msg.tool_calls or []:
                        t_calls.append(
                            {
                                "name": tc["name"],
                                "args": tc["args"],
                                "id": tc.get("id", f"call_{msg.id.hex[:8]}"),
                            }
                        )
                    current_history.append(
                        AIMessage(content=msg.content.get("text"), tool_calls=t_calls)
                    )

        current_history.append(HumanMessage(content=user_input))

        full_message = ""
        all_tool_calls = []
        all_citations = []
        all_tasks = []

        max_iterations = await self._get_max_iterations(tenant_id)
        for _ in range(max_iterations):
            response = await llm_with_tools.ainvoke(current_history)
            current_history.append(response)

            if response.content:
                if not full_message:
                    full_message = response.content
                elif response.content.startswith(full_message):
                    full_message = response.content
                else:
                    full_message += response.content

            if not response.tool_calls:
                # Save assistant message
                if session_id:
                    await self.chat_session_service.save_message(
                        session_id=session_id,
                        role="assistant",
                        content={"text": full_message},
                        tool_calls=all_tool_calls,
                        citations=all_citations,
                        tasks=all_tasks or None,
                    )
                return {
                    "message": full_message,
                    "session_id": session_id,
                    "success": True,
                }

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                selected_tool = next((t for t in tools if t.name == tool_name), None)
                if selected_tool:
                    observation = await selected_tool.ainvoke(tool_call["args"])

                    hitl_task = _parse_hitl_proposal(observation)
                    if hitl_task:
                        all_tasks.append(hitl_task)
                        feedback = _hitl_llm_feedback(hitl_task)
                        all_tool_calls.append(
                            {
                                "id": tool_call.get("id"),
                                "name": tool_name,
                                "args": tool_call["args"],
                                "result": feedback,
                            }
                        )
                        current_history.append(
                            ToolMessage(
                                content=feedback, tool_call_id=tool_call["id"]
                            )
                        )
                    else:
                        all_tool_calls.append(
                            {
                                "id": tool_call.get("id"),
                                "name": tool_name,
                                "args": tool_call["args"],
                                "result": str(observation),
                            }
                        )
                        all_citations.append(selected_tool.name)
                        current_history.append(
                            ToolMessage(
                                content=str(observation), tool_call_id=tool_call["id"]
                            )
                        )
                else:
                    current_history.append(
                        ToolMessage(
                            content=f"Tool {tool_name} not found.",
                            tool_call_id=tool_call["id"],
                        )
                    )

        return {
            "message": "I'm sorry, I reached my maximum reasoning limit.",
            "success": False,
        }

    async def _define_biomarker(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """AI-driven biomarker definition builder"""
        system_prompt = """You are a medical data expert assisting in creating a new biomarker definition for a clinical catalog.
        
        You MUST provide a valid JSON object with the following fields:
        - name: Full official medical name.
        - category: Clinical category.
        - unit_symbol: Preferred unit symbol.
        - coding_system: The medical coding system to use (e.g., "loinc", "custom"). Try to map standard labs to "loinc".
        - code: The specific code from the coding system (e.g., "2345-7").
        - aliases: List of synonyms.
        - reference_range_min: Typical lower bound (float).
        - reference_range_max: Typical upper bound (float).
        - is_telemetry: Boolean. True only if this metric is tracked continuously via IoT/wearables (e.g. heart rate, steps).
        - info: Detailed clinical explanation.
        
        Suggested values are mandatory for all fields even if the user only provides a name.
        """

        structured_llm = llm.with_structured_output(BiomarkerDefinitionOutput)
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("human", "Fully define this biomarker: {user_input}"),
            ]
        )

        chain = prompt | structured_llm
        result = await chain.ainvoke({"user_input": user_input})

        # Use logger.debug so diagnostic output only surfaces when DEBUG
        # logging is enabled and never leaks to stdout in production deployments.
        logger.debug("AI biomarker definition generated for %r", user_input)

        return {"suggested_data": result.model_dump(), "success": True}

    async def _define_medication(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """AI-driven medication definition builder"""
        system_prompt = """You are a medical pharmacology expert assisting in creating a new medication entry for a clinical catalog.
        
        You MUST provide a valid JSON object with the following fields:
        - name: Full generic or brand name of the medication.
        - description: A brief but informative overview of the drug and its class.
        - indications: Main medical uses for this drug.
        - dosage_info: Standard dosage forms and typical instructions.
        - contraindications: Major reasons why this drug should NOT be used.
        - side_effects: A list of common adverse reactions.
        
        Suggested values are mandatory for all fields even if the user only provides a name.
        """

        structured_llm = llm.with_structured_output(MedicationDefinitionOutput)
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("human", "Fully define this medication: {user_input}"),
            ]
        )

        chain = prompt | structured_llm
        result = await chain.ainvoke({"user_input": user_input})

        # Routed through logger.debug so diagnostic output is gated on DEBUG.
        logger.debug("AI medication definition generated for %r", user_input)

        return {"suggested_data": result.model_dump(), "success": True}

    async def _magic_fill_examination(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """AI-driven examination form filler"""
        tenant_id = context.get("tenant_id")

        # Fetch custom categories from the database
        cat_res = await self.db.execute(
            select(ExaminationCategory).where(
                or_(
                    ExaminationCategory.tenant_id == tenant_id,
                    ExaminationCategory.tenant_id.is_(None),
                )
            )
        )
        existing_slugs = [c.slug for c in cat_res.scalars().all()]
        if not existing_slugs:
            from app.core.constants import DOCUMENT_CATEGORIES

            existing_slugs = [c["id"] for c in DOCUMENT_CATEGORIES]

        slugs_str = ", ".join(existing_slugs)

        # Inject the live date so relative-date parsing stays accurate.
        from datetime import datetime, timezone

        _now = datetime.now(timezone.utc)
        today_iso = _now.strftime("%Y-%m-%d")
        current_year = _now.year

        system_prompt = f"""You are a medical assistant helping to record a new examination visit.
Extract the examination date, clinical notes, patient notes, category slug, and any doctor names from the user's input.

Doctor Names: Omit titles like 'Dr.', 'Doctor', 'MD', etc. Only return the actual name (e.g. return 'Smith' or 'John Smith' instead of 'Dr. Smith').

Available Category SLUGS: {slugs_str}
Pick EXACTLY one most appropriate category SLUG from the list above. 
If unsure, you may suggest a new compact clinical specialty slug (e.g., 'dermatology').
Do NOT concatenate multiple categories. Pick the primary one.
Ensure the slug is lowercase and uses kebab-case.

Output the date in ISO format (YYYY-MM-DD). If no year is mentioned, assume {current_year}.
If no month or day is mentioned, use today's date if appropriate or leave null.
Today's date is {today_iso}.
"""

        structured_llm = llm.with_structured_output(ExaminationMagicFillOutput)
        prompt = ChatPromptTemplate.from_messages(
            [("system", system_prompt), ("human", "{user_input}")]
        )

        chain = prompt | structured_llm
        result = await chain.ainvoke({"user_input": user_input})

        return {"suggested_data": result.model_dump(), "success": True}

    async def _fill_biomarker_form(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        patient_id = context.get("patient_id")
        recent_bios = []
        if patient_id:
            recent_bios = await self._get_recent_biomarkers_context(UUID(patient_id))

        system_prompt = """You are a medical assistant helping to fill a biomarker entry form.
Extract the biomarker name, value, unit, and interpretation from the user's input.
Style Matching: The user has previously recorded these biomarkers: {recent_bios}.
If the user mentions a biomarker that matches one of these, prefer the unit they used before.

Interpretation Rules:
- 'low': if the value is below normal
- 'normal': if the value is within normal range
- 'high': if the value is above normal
If not explicitly mentioned, assume 'normal' unless the value is obviously pathological.
"""

        structured_llm = llm.with_structured_output(BiomarkerFormOutput)
        prompt = ChatPromptTemplate.from_messages(
            [("system", system_prompt), ("human", "{user_input}")]
        )

        chain = prompt | structured_llm
        result = await chain.ainvoke(
            {"user_input": user_input, "recent_bios": json.dumps(recent_bios)}
        )

        data = result.model_dump()
        if data.get("interpretation"):
            data["interpretation"] = data["interpretation"].lower()
            if data["interpretation"] not in ["low", "normal", "high"]:
                data["interpretation"] = "normal"

        return {"suggested_data": data, "success": True}

    async def _fill_medication_form(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        patient_id = context.get("patient_id")
        recent_meds = []
        if patient_id:
            recent_meds = await self._get_recent_medications_context(UUID(patient_id))

        system_prompt = """You are a medical assistant helping to record a new medication.
Extract the medication name, dosage, frequency, reason, and any notes from the user's input.
Frequency should be a clear label like 'Once Daily', 'Twice Daily', 'Every 8 hours', etc.

Style Matching: The patient currently takes: {recent_meds}.
"""

        structured_llm = llm.with_structured_output(MedicationFormOutput)
        prompt = ChatPromptTemplate.from_messages(
            [("system", system_prompt), ("human", "{user_input}")]
        )

        chain = prompt | structured_llm
        result = await chain.ainvoke(
            {"user_input": user_input, "recent_meds": json.dumps(recent_meds)}
        )

        return {"suggested_data": result.model_dump(), "success": True}

    async def _suggest_category_icon(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Suggest Lucide icons based on category name/description"""
        system_prompt = """You are a UI expert for a medical application. 
        The user is creating a medical examination category (e.g., 'Hematology', 'Radiology').
        Suggest 5-8 appropriate Lucide icon names that represent this category.
        
        Rules:
        - Return ONLY the Lucide icon names in PascalCase (e.g., 'Activity', 'Droplet', 'Stethoscope').
        - Ensure the icons are available in the Lucide library.
        - Prioritize medical or health-related icons.
        """

        structured_llm = llm.with_structured_output(CategoryIconSuggestionOutput)
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("human", "Suggest icons for this medical category: {user_input}"),
            ]
        )

        chain = prompt | structured_llm
        result = await chain.ainvoke({"user_input": user_input})

        return {"suggested_icons": result.suggested_icons, "success": True}

    async def _generate_category_icon(
        self,
        llm,
        user_input: str,
        reference_image: Optional[str] = None,
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Generate or refine a custom Lucide-style SVG icon"""
        instruction = context.get("instruction") if context else None
        previous_svg = context.get("previous_svg") if context else None

        system_prompt = """You are a minimalist UI designer specializing in medical iconography and SVG vector art.
        Your task is to generate or refine a clean, modern SVG icon that matches the aesthetic of the Lucide library.

        STYLE GUIDELINES:
        - Minimalistic but Accurate: Capture the essential shape of the medical concept or organ.
        - Lucide Aesthetic: Icons should be lightweight, "open", and consist of clean line work.
        - Professional: Ensure the icon is clearly recognizable for the clinical specialty.
        - Fills: You may use fill="currentColor" for specific paths if it helps define the shape (e.g. solid lungs or a filled heart), but keep it simple.

        TECHNICAL SPECIFICATIONS:
        - Viewport: 24x24 (strictly).
        - Stroke Width: 2px (strictly).
        - Colors: Use 'currentColor' for stroke and/or fill.
        - Line Quality: Use 'round' for both linecap and linejoin.
        - Transparency: The SVG background is naturally transparent.
        - Padding: Keep the icon content within the 2 to 22 range.

        OUTPUT REQUIREMENTS:
        - Return ONLY the raw SVG code within the 'svg_content' field.
        - Ensure valid XML with xmlns="http://www.w3.org/2000/svg" and viewBox="0 0 24 24".
        - Optimized: No metadata, comments, titles, or nested tags.
        """

        if previous_svg:
            human_prompt = f"Refine this existing medical icon for: '{user_input}'.\n\nCURRENT SVG:\n{previous_svg}"
            if instruction:
                human_prompt += f"\n\nREFINE INSTRUCTION: {instruction}"
            else:
                human_prompt += "\n\nPlease improve the visual representation while maintaining the style."
        else:
            human_prompt = f"Create a professional, minimalistic, and accurate medical icon for: '{user_input}'."
            if instruction:
                human_prompt += f"\n\nUser Instructions: {instruction}"
            else:
                human_prompt += "\n\nDesign a simple but recognizable visual for this medical specialty using clean paths."

        if reference_image:
            human_prompt += "\n\nI have provided a reference image. Please use it as a guide for the icon structure/metaphor, but convert it to the requested minimalistic SVG line style."

        messages = [SystemMessage(content=system_prompt)]

        human_content = [{"type": "text", "text": human_prompt}]
        if reference_image:
            # Ensure it has the correct prefix
            if not reference_image.startswith("data:"):
                # Assume it's a jpeg base64 if no prefix
                reference_image = f"data:image/jpeg;base64,{reference_image}"

            human_content.append(
                {"type": "image_url", "image_url": {"url": reference_image}}
            )

        messages.append(HumanMessage(content=human_content))

        structured_llm = llm.with_structured_output(CategoryIconGenerationOutput)
        result = await structured_llm.ainvoke(messages)

        # Sanitize and optimize the generated SVG
        svg = sanitize_svg(result.svg_content)

        return {
            "svg_content": svg,
            "justification": result.justification,
            "success": True,
        }
