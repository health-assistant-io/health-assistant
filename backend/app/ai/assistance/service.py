"""AIAssistanceService — interactive AI facade.

This module is the public entry point for the interactive-AI subsystem. The
agentic-chat reasoning loop, HITL helpers, and chat prompts live in
:mod:`app.ai.agents` (extracted in Phase 2); the structured-output
(form-fill / definition / icon) handlers live in sibling modules under
:mod:`app.ai.assistance` (Phase 6c).

The HITL helpers and the service class are re-exported here for backward
compatibility with callers and tests.
"""

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.chat_agent import (
    build_chat_tools,
    reconstruct_history,
    run_reasoning_loop,
    stream_loop_as_sse,
)
from app.ai.agents.hitl import (
    _append_assistant_turn_to_history,
    _hitl_llm_feedback,
    _hitl_resolved_brief,
    _hitl_resolution_summary,
    _parse_hitl_proposal,
    resume_after_hitl as _resume_after_hitl,
)
from app.ai.agents.prompts import (
    build_chat_system_prompt,
    build_general_chat_system_prompt,
    session_title_prompt,
)
from app.ai.assistance.definitions import (
    define_anatomy_graph,
    define_biomarker,
    define_medication,
)
from app.ai.assistance.form_fillers import (
    fill_biomarker_form,
    fill_medication_form,
    magic_fill_examination,
)
from app.ai.assistance.icons import generate_category_icon, suggest_category_icon
from app.ai.providers.service import AIProviderService
from app.core.config import settings
from app.models.system_setting import SystemSetting
from app.models.tenant_model import TenantModel
from app.services.chat_session_service import ChatSessionService
from app.utils.prompt_guard import check_user_input_safety

# Re-exported for backward compatibility — tests import the HITL helpers from
# ``app.ai.assistance.service``.
__all__ = [
    "AIAssistanceService",
    "_append_assistant_turn_to_history",
    "_hitl_llm_feedback",
    "_hitl_resolved_brief",
    "_hitl_resolution_summary",
    "_parse_hitl_proposal",
]

logger = logging.getLogger(__name__)


class AIAssistanceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ai_provider_service = AIProviderService(db)
        self.chat_session_service = ChatSessionService(db)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

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
        elif task_type == "define_anatomy_graph":
            return await self._define_anatomy_graph(llm, user_input, context)
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

    # ------------------------------------------------------------------
    # Chat — delegates to the shared reasoning loop (Phase 2)
    # ------------------------------------------------------------------

    async def _generate_session_title(self, llm, user_input: str) -> str:
        """Generate a short title for a chat session"""
        prompt = session_title_prompt(user_input)
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
        """Stream a chat response (SSE). Body lives in run_reasoning_loop."""
        patient_id = context.get("patient_id")
        examination_id = context.get("examination_id")
        session_id_str = context.get("session_id")
        session_id = UUID(session_id_str) if session_id_str else None

        # Auto-create session if not provided.
        if not session_id and user_id and tenant_id:
            title = await self._generate_session_title(llm, user_input)
            p_id = UUID(patient_id) if patient_id else None
            session = await self.chat_session_service.create_session(
                user_id=user_id, tenant_id=tenant_id, patient_id=p_id, title=title
            )
            session_id = session.id
            yield f"[SESSION_ID] {session_id}"

        # Save user message.
        if session_id:
            await self.chat_session_service.save_message(
                session_id=session_id, role="user", content={"text": user_input}
            )

        tools = await build_chat_tools(
            self.db,
            tenant_id,
            patient_id,
            user_id,
            examination_id=examination_id,
            label="chat",
        )
        llm_with_tools = llm.bind_tools(tools) if tools else llm

        system_prompt = build_chat_system_prompt(context)
        history = await reconstruct_history(
            self.chat_session_service,
            session_id,
            user_id,
            tenant_id,
            system_prompt,
            user_input,
        )

        max_iterations = await self._get_max_iterations(tenant_id)
        loop = run_reasoning_loop(
            llm_with_tools,
            tools,
            history,
            max_iterations,
            streaming=True,
            chat_session_service=self.chat_session_service,
            session_id=session_id,
            log_label="AI Assistance",
        )
        async for chunk in stream_loop_as_sse(loop):
            yield chunk

    async def resume_after_hitl(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        message_id: Optional[UUID] = None,
    ):
        """Stream a HITL continuation turn. Delegates to
        :func:`app.ai.agents.hitl.resume_after_hitl`."""
        max_iterations = await self._get_max_iterations(tenant_id)
        async for chunk in _resume_after_hitl(
            self.db,
            self.chat_session_service,
            self.ai_provider_service,
            max_iterations,
            session_id,
            tenant_id,
            user_id,
            message_id,
        ):
            yield chunk

    async def _general_chat(
        self,
        llm,
        user_input: str,
        context: Dict[str, Any],
        tenant_id: UUID,
        user_id: UUID,
    ) -> Dict[str, Any]:
        """Non-streaming chat with tool support. Collects content events from
        :func:`run_reasoning_loop` (streaming=False) into the response dict."""
        patient_id = context.get("patient_id")
        examination_id = context.get("examination_id")
        session_id_str = context.get("session_id")
        session_id = UUID(session_id_str) if session_id_str else None

        # Auto-create session if not provided.
        if not session_id and user_id and tenant_id:
            title = await self._generate_session_title(llm, user_input)
            p_id = UUID(patient_id) if patient_id else None
            session = await self.chat_session_service.create_session(
                user_id=user_id, tenant_id=tenant_id, patient_id=p_id, title=title
            )
            session_id = session.id

        # Save user message.
        if session_id:
            await self.chat_session_service.save_message(
                session_id=session_id, role="user", content={"text": user_input}
            )

        tools = await build_chat_tools(
            self.db,
            tenant_id,
            patient_id,
            user_id,
            examination_id=examination_id,
            label="chat",
        )
        llm_with_tools = llm.bind_tools(tools) if tools else llm

        system_prompt = build_general_chat_system_prompt(context)
        history = await reconstruct_history(
            self.chat_session_service,
            session_id,
            user_id,
            tenant_id,
            system_prompt,
            user_input,
        )

        max_iterations = await self._get_max_iterations(tenant_id)
        full_message = ""
        reached_max = False
        async for kind, data in run_reasoning_loop(
            llm_with_tools,
            tools,
            history,
            max_iterations,
            streaming=False,
            chat_session_service=self.chat_session_service,
            session_id=session_id,
            log_label="AI Assistance",
        ):
            if kind == "content":
                full_message += data
            elif kind == "done":
                reached_max = data

        if reached_max:
            return {
                "message": "I'm sorry, I reached my maximum reasoning limit.",
                "success": False,
            }
        return {
            "message": full_message,
            "session_id": session_id,
            "success": True,
        }

    # ------------------------------------------------------------------
    # Structured-output task handlers (Phase 6c delegates).
    #
    # The implementations live in form_fillers.py / definitions.py / icons.py.
    # These stay as thin methods so the dispatcher (``self._define_biomarker``
    # etc.) and direct test calls (``svc._magic_fill_examination(...)``) keep
    # working unchanged.
    #
    # NOTE: ``_define_biomarker`` / ``_define_medication`` /
    # ``_suggest_category_icon`` / ``_generate_category_icon`` do not query the
    # DB, so they intentionally do NOT pass ``self.db`` — this preserves the
    # ``AIAssistanceService.__new__(...)`` call shape used by tests (an instance
    # built without running ``__init__`` has no ``db``).
    # ------------------------------------------------------------------

    async def _define_biomarker(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await define_biomarker(llm, user_input, context)

    async def _define_medication(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await define_medication(llm, user_input, context)

    async def _define_anatomy_graph(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await define_anatomy_graph(llm, user_input, context)

    async def _magic_fill_examination(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await magic_fill_examination(self.db, llm, user_input, context)

    async def _fill_biomarker_form(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await fill_biomarker_form(self.db, llm, user_input, context)

    async def _fill_medication_form(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await fill_medication_form(self.db, llm, user_input, context)

    async def _suggest_category_icon(
        self, llm, user_input: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await suggest_category_icon(llm, user_input, context)

    async def _generate_category_icon(
        self,
        llm,
        user_input: str,
        reference_image: Optional[str] = None,
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        return await generate_category_icon(llm, user_input, reference_image, context)
