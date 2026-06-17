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
from app.utils.svg import sanitize_svg
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.patient import Observation
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.examination_category import ExaminationCategory
from app.core.constants import CATEGORY_NAMES

logger = logging.getLogger(__name__)


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
        """Main entry point for AI assistance"""
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
            chatbot_tools = ChatbotTools(self.db, tenant_id, UUID(patient_id))
            tools = chatbot_tools.get_tools()
            try:
                from app.services.integration_tool_aggregator import aggregate as integration_aggregate
                integration_tools = await integration_aggregate(
                    self.db, user_id, tenant_id, UUID(patient_id)
                )
                tools = tools + integration_tools
            except Exception as e:
                logger.warning(f"Failed to load integration tools for chat (continuing with built-ins): {e}")

        system_prompt = f"""You are Health Assistant AI, a professional medical data assistant. 
        Answer the user's questions clearly and professionally using Markdown.
        
        FORMATTING RULES:
        - Use Markdown TABLES for presenting lists of data (biomarkers, medications, examinations) when multiple records are involved. This makes the data easier to read.
        - For scientific units with exponents (like 10^3, 10^6, m^2), use UNICODE superscript characters (e.g., ³, ⁶, ⁹, ²) instead of the caret (^) symbol.
        
        BIOMARKER & TELEMETRY RULES:
        1. Discovery: If asked about a health metric, ALWAYS use the `search_available_biomarkers` tool first to find its exact `slug` and verify its `is_telemetry` type (unless you already know it).
        2. Clinical Data: If `is_telemetry` is FALSE, you MUST fetch the data using `get_biomarker_history`. This targets standard FHIR laboratory records.
        3. Telemetry Data: If `is_telemetry` is TRUE (e.g., heart rate, steps, continuous monitors), you MUST fetch the data using `get_aggregated_biomarker_trends`. This targets high-frequency TimescaleDB records. NEVER use `get_biomarker_history` for telemetry data.
        
        REPETITION POLICY:
        - DO NOT repeat your own preamble or any text you generated before calling a tool.
        - Focus ONLY on the new information derived from the tool results.

        CITATION POLICY:
        - When you use information from a tool result, you MUST cite it inline using the format [Ref: type=uuid] or [Ref: type=slug].
        - Use "observation" for a specific lab result value (use the 'id' field).
        - Use "biomarker" for general biomarker info or TELEMETRY TRENDS. You can use the 'slug' (e.g. [Ref: biomarker=heart-rate]) or the 'id' (UUID).
        - Use "medication" for prescriptions, "examination" for clinical visits, "event" for health journeys, and "document" for uploaded reports/images.
        - GRANULARITY: If you report multiple data points (e.g., several biomarkers from a single visit), cite EACH one individually with its specific "observation" ID. 
        - TELEMETRY: For high-frequency telemetry (heart rate, steps), individual IDs are not available. Cite the "biomarker" slug instead.
        - PREFERENCE: Always prefer a specific "observation" citation over a general "examination" or "document" citation when reporting numerical lab results or specific findings.

        - Example: "Total Cholesterol was 225 mg/dL [Ref: observation=uuid1] and LDL was 149 mg/dL [Ref: observation=uuid2]."
        - Example Telemetry: "Your heart rate averaged 72 bpm [Ref: biomarker=heart-rate] during your last workout."
        - ALWAYS provide the FULL UUID or SLUG. NEVER truncate it with dots.
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

        # Add current user input as the last message
        current_history.append(HumanMessage(content=user_input))

        # TRACKING ACROSS ITERATIONS: Keep track of what we've yielded to the user
        total_content_yielded = ""
        all_tool_calls = []
        all_citations = []

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

                    args_str = json.dumps(tool_call["args"])
                    result_str = str(observation)
                    payload_dict = {
                        "name": tool_name,
                        "args": tool_call["args"],
                        "result": result_str
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

        # Save assistant message
        if session_id:
            await self.chat_session_service.save_message(
                session_id=session_id,
                role="assistant",
                content={"text": total_content_yielded},
                tool_calls=all_tool_calls,
                citations=all_citations,
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
            chatbot_tools = ChatbotTools(self.db, tenant_id, UUID(patient_id))
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
        1. Discovery: If asked about a health metric, ALWAYS use the `search_available_biomarkers` tool first to find its exact `slug` and verify its `is_telemetry` type (unless you already know it).
        2. Clinical Data: If `is_telemetry` is FALSE, you MUST fetch the data using `get_biomarker_history`. This targets standard FHIR laboratory records.
        3. Telemetry Data: If `is_telemetry` is TRUE (e.g., heart rate, steps, continuous monitors), you MUST fetch the data using `get_aggregated_biomarker_trends`. This targets high-frequency TimescaleDB records. NEVER use `get_biomarker_history` for telemetry data.

        CITATION POLICY:
        - When you use information from a tool result, you MUST cite it inline using the format [Ref: type=uuid] or [Ref: type=slug].
        - Use "observation" for a specific lab result value (use the 'id' field).
        - Use "biomarker" for general biomarker info or TELEMETRY TRENDS. You can use the 'slug' (e.g. [Ref: biomarker=heart-rate]) or the 'id' (UUID).
        - Use "medication" for prescriptions, "examination" for clinical visits, "event" for health journeys, and "document" for uploaded reports/images.
        - GRANULARITY: If you report multiple data points (e.g., several biomarkers from a single visit), cite EACH one individually with its specific "observation" ID. 
        - TELEMETRY: For high-frequency telemetry (heart rate, steps), individual IDs are not available. Cite the "biomarker" slug instead.
        - PREFERENCE: Always prefer a specific "observation" citation over a general "examination" or "document" citation when reporting numerical lab results or specific findings.
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

        # Log to terminal for visual verification
        print(f"\n[AI-ASSIST] Biomarker Definition for '{user_input}':")
        print(json.dumps(result.model_dump(), indent=2))

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

        # Log to terminal for visual verification
        print(f"\n[AI-ASSIST] Medication Definition for '{user_input}':")
        print(json.dumps(result.model_dump(), indent=2))

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

        system_prompt = f"""You are a medical assistant helping to record a new examination visit.
Extract the examination date, clinical notes, patient notes, category slug, and any doctor names from the user's input.

Doctor Names: Omit titles like 'Dr.', 'Doctor', 'MD', etc. Only return the actual name (e.g. return 'Smith' or 'John Smith' instead of 'Dr. Smith').

Available Category SLUGS: {slugs_str}
Pick EXACTLY one most appropriate category SLUG from the list above. 
If unsure, you may suggest a new compact clinical specialty slug (e.g., 'dermatology').
Do NOT concatenate multiple categories. Pick the primary one.
Ensure the slug is lowercase and uses kebab-case.

Output the date in ISO format (YYYY-MM-DD). If no year is mentioned, assume 2026.
If no month or day is mentioned, use today's date if appropriate or leave null.
Today's date is 2026-03-22.
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
