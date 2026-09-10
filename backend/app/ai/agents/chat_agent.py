"""Shared chat infrastructure (tool assembly, history, SSE mapping).

The reasoning loop itself was replaced by the LangGraph engine
(:mod:`app.ai.graphs.chat_agent`) in Phases 3.2-3.6 and deleted in Phase 8;
what remains here is the shared, engine-agnostic surface:

The loop is an async generator yielding typed ``(kind, data)`` event tuples,
parameterised by ``streaming`` (``astream`` vs ``ainvoke``). It owns the
provider-quirk content dedup, tool execution, HITL-proposal detection, and the
proactive + final saves — so all three callers share identical semantics.

  * Streaming callers (chat + resume) pipe the loop through
    :func:`stream_loop_as_sse` to emit the SSE sentinel vocabulary the frontend
    consumes.
  * The non-streaming caller (``_general_chat``) collects ``("content", …)``
    events into the response dict.

Public surface:
  * :func:`build_chat_tools`     — built-in + integration tool assembly.
  * :func:`reconstruct_history`  — rebuild the in-memory message list.
  * :func:`stream_loop_as_sse`   — event -> SSE-sentinel mapper.
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.hitl import (
    _append_assistant_turn_to_history,
)
from app.ai.assistance.attachments import build_multimodal_content, has_images
from app.ai.tools import get_tools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool assembly
# ---------------------------------------------------------------------------


async def build_chat_tools(
    db: AsyncSession,
    tenant_id: Optional[UUID],
    patient_id: Optional[str],
    user_id: Optional[UUID],
    examination_id: Optional[str] = None,
    label: str = "chat",
) -> List[Any]:
    """Assemble the built-in chatbot tools + any integration tools.

    Returns an empty list when patient/tenant context is missing (the chatbot
    runs tool-less in that case). Integration-tool load failures are logged
    and swallowed — built-ins still work.
    """
    if not (patient_id and tenant_id):
        return []

    exam_id = UUID(examination_id) if examination_id else None
    tools = get_tools(
        db, tenant_id, UUID(patient_id), examination_id=exam_id, user_id=user_id
    )
    try:
        from app.ai.tools.aggregator import aggregate as integration_aggregate

        integration_tools = await integration_aggregate(
            db, user_id, tenant_id, UUID(patient_id)
        )
        tools = tools + integration_tools
    except Exception as e:
        logger.warning(
            f"Failed to load integration tools for {label} "
            f"(continuing with built-ins): {e}"
        )
    return tools


# ---------------------------------------------------------------------------
# History reconstruction
# ---------------------------------------------------------------------------


async def reconstruct_history(
    chat_session_service,
    session_id: Optional[UUID],
    user_id: Optional[UUID],
    tenant_id: Optional[UUID],
    system_prompt: str,
    driving_input: Union[str, List[Dict[str, Any]]],
) -> List[Any]:
    """Build the in-memory LLM message list for a chat turn.

    Layout: ``[SystemMessage(prompt)] + [replayed past turns] + [HumanMessage(driving_input)]``.

    The last persisted message is excluded (it's the just-saved current user
    input / HITL summary, which the caller appends explicitly as the driving
    input). Past assistant turns are replayed via
    :func:`_append_assistant_turn_to_history` so every ``tool_call_id`` is
    followed by a ``ToolMessage`` (OpenAI contract).

    NOTE (behaviour fix in Phase 2): the former ``_general_chat`` reconstructed
    past assistant turns WITHOUT following ToolMessages — a latent OpenAI
    ``400 tool_call_ids did not have response messages`` on non-streaming turns
    that followed a tool-calling turn. Unifying on this helper fixes that.
    """
    current_history: List[Any] = [SystemMessage(content=system_prompt)]
    if session_id:
        past_messages = await chat_session_service.get_session_messages(
            session_id, user_id, tenant_id
        )
        # Exclude the message we just saved (which is the driving input) to
        # avoid duplication; replay the prior 10 turns.
        for msg in past_messages[:-1][-10:]:
            if msg.role == "user":
                # Reconstruct multimodal content when the persisted message
                # carries image attachments (vision input) so the model keeps
                # visual context across turns. Plain text stays a plain str.
                content_json = msg.content if isinstance(msg.content, dict) else {}
                if has_images(content_json):
                    current_history.append(
                        HumanMessage(
                            content=build_multimodal_content(
                                content_json.get("text", ""),
                                content_json.get("images"),
                            )
                        )
                    )
                else:
                    current_history.append(
                        HumanMessage(content=content_json.get("text"))
                    )
            elif msg.role == "assistant":
                _append_assistant_turn_to_history(msg, current_history)
    current_history.append(HumanMessage(content=driving_input))
    return current_history


# ---------------------------------------------------------------------------
# The reasoning loop (replaces 3 duplicates)
# ---------------------------------------------------------------------------


FLOW_EVENT_PREFIX = "[FLOW_EVENT] "


async def stream_loop_as_sse(
    loop: AsyncIterator[Tuple[str, Any]],
    *,
    flow_events: bool = False,
) -> AsyncIterator[str]:
    """Map chat-engine events to the SSE sentinel vocabulary the
    frontend parser consumes (``[TOOL_CALL_*]`` / ``[CITATION]`` /
    ``[HITL_TASK]``). ``("content", delta)`` is yielded verbatim; ``("done",)``
    emits nothing. Used by the streaming chat + resume paths."""
    async for kind, data in loop:
        if kind == "flow_event":
            # Phase 6.2 dual-emit: family events ride as gated additive
            # frames; dropped unless the caller opted in.
            if flow_events:
                yield f"{FLOW_EVENT_PREFIX}{json.dumps(data)}"
            continue
        if kind == "content":
            yield data
        elif kind == "tool_call_start":
            yield f"[TOOL_CALL_START] {data}"
        elif kind == "tool_call_exec":
            yield f"[TOOL_CALL_EXEC] {data}"
        elif kind == "tool_call_result":
            yield f"[TOOL_CALL_RESULT] {json.dumps(data)}"
        elif kind == "citation":
            yield f"[CITATION] {data}"
        elif kind == "hitl_task":
            yield f"[HITL_TASK] {json.dumps(data)}"
        elif kind == "tool_call_finished":
            yield "[TOOL_CALL_FINISHED]"
        # "done" -> no SSE emission
