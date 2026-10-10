"""Shared chat infrastructure (tool assembly, history, SSE mapping).

The reasoning loop itself was replaced by the LangGraph engine
(:mod:`app.ai.graphs.chat_agent`) in Phases 3.2-3.6 and deleted in Phase 8;
what remains here is the shared, engine-agnostic surface:

  * Streaming callers (chat + resume) pipe the engine's event dicts through
    :func:`stream_events_as_sse` to emit the family chat vocabulary
    (ai-features §5) plus health's payload events.
  * The non-streaming caller (``_general_chat``) collects ``delta`` events
    into the response dict.

Public surface:
  * :func:`build_chat_tools`      — built-in + integration tool assembly.
  * :func:`reconstruct_history`   — rebuild the in-memory message list.
  * :func:`stream_events_as_sse`  — event dicts -> SSE frames.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
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
    tenant_id: UUID | None,
    patient_id: str | None,
    user_id: UUID | None,
    examination_id: str | None = None,
    label: str = "chat",
) -> list[Any]:
    """Assemble the built-in chatbot tools + any integration tools.

    Returns an empty list when patient/tenant context is missing (the chatbot
    runs tool-less in that case). Integration-tool load failures are logged
    and swallowed — built-ins still work.
    """
    if not (patient_id and tenant_id):
        return []

    exam_id = UUID(examination_id) if examination_id else None
    tools = get_tools(db, tenant_id, UUID(patient_id), examination_id=exam_id, user_id=user_id)
    try:
        from app.ai.tools.aggregator import aggregate as integration_aggregate

        integration_tools = await integration_aggregate(db, user_id, tenant_id, UUID(patient_id))
        tools = tools + integration_tools
    except Exception as e:
        logger.warning(
            f"Failed to load integration tools for {label} (continuing with built-ins): {e}"
        )
    return tools


# ---------------------------------------------------------------------------
# History reconstruction
# ---------------------------------------------------------------------------


async def reconstruct_history(
    chat_session_service,
    session_id: UUID | None,
    user_id: UUID | None,
    tenant_id: UUID | None,
    system_prompt: str,
    driving_input: str | list[dict[str, Any]],
    stop_before_id: UUID | None = None,
) -> list[Any]:
    """Build the in-memory LLM message list for a chat turn.

    Layout: ``[SystemMessage(prompt)] + [replayed past turns] + [HumanMessage(driving_input)]``.

    The last persisted message is excluded (it's the just-saved current user
    input / HITL summary, which the caller appends explicitly as the driving
    input). Past assistant turns are replayed via
    :func:`_append_assistant_turn_to_history` so every ``tool_call_id`` is
    followed by a ``ToolMessage`` (OpenAI contract).

    Plan 13 H2 branching: the replay walks the ACTIVE PATH (pointer walk),
    not raw ``created_at`` order — hidden variants never reach the model.
    ``stop_before_id`` (regenerate turns) truncates the walk at the target
    user message: the re-roll re-enters as the driving input and its old
    subtree is dropped.

    NOTE (behaviour fix in Phase 2): the former ``_general_chat`` reconstructed
    past assistant turns WITHOUT following ToolMessages — a latent OpenAI
    ``400 tool_call_ids did not have response messages`` on non-streaming turns
    that followed a tool-calling turn. Unifying on this helper fixes that.
    """
    current_history: list[Any] = [SystemMessage(content=system_prompt)]
    if session_id:
        past_messages = await chat_session_service.get_active_path_messages(
            session_id, user_id, tenant_id
        )
        if stop_before_id is not None:
            stop_ids = [i for i, m in enumerate(past_messages) if m.id == stop_before_id]
            past_messages = past_messages[: stop_ids[0]] if stop_ids else past_messages[:-1]
        else:
            # Exclude the message we just saved (which is the driving input)
            # to avoid duplication; replay the prior 10 turns.
            past_messages = past_messages[:-1][-10:]
        for msg in past_messages:
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
                    current_history.append(HumanMessage(content=content_json.get("text")))
            elif msg.role == "assistant":
                _append_assistant_turn_to_history(msg, current_history)
    current_history.append(HumanMessage(content=driving_input))
    return current_history


# ---------------------------------------------------------------------------
# SSE mapping (family vocabulary, plan 24 V3)
# ---------------------------------------------------------------------------


async def stream_events_as_sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Map the chat engine's event dicts to SSE frames.

    Every wire frame is one JSON object with the event name in ``event``
    (:class:`app.ai.flow_events_payload.FlowEvent`); the engine-internal ``done``
    completion signal emits nothing. Used by the streaming chat + resume
    paths.
    """
    async for event in events:
        if event.get("event") == "done":
            continue
        yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
