"""The agentic-chat reasoning loop + shared chat infrastructure.

Phase 2: collapses the three near-duplicate loops that lived in
``AIAssistanceService`` (``_chat_stream`` ~330L, ``resume_after_hitl`` ~330L,
``_general_chat`` ~196L) into ONE :func:`run_reasoning_loop`.

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
  * :func:`run_reasoning_loop`   — the ONE loop (event generator).
  * :func:`stream_loop_as_sse`   — event -> SSE-sentinel mapper.
"""
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.hitl import _append_assistant_turn_to_history, _hitl_llm_feedback, _parse_hitl_proposal
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
    tools = get_tools(db, tenant_id, UUID(patient_id), examination_id=exam_id)
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
    driving_input: str,
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
                current_history.append(HumanMessage(content=msg.content.get("text")))
            elif msg.role == "assistant":
                _append_assistant_turn_to_history(msg, current_history)
    current_history.append(HumanMessage(content=driving_input))
    return current_history


# ---------------------------------------------------------------------------
# The reasoning loop (replaces 3 duplicates)
# ---------------------------------------------------------------------------


async def run_reasoning_loop(
    llm_with_tools,
    tools: List[Any],
    history: List[Any],
    max_iterations: int,
    *,
    streaming: bool,
    chat_session_service=None,
    session_id: Optional[UUID] = None,
    log_label: str = "AI Assistance",
) -> AsyncIterator[Tuple[str, Any]]:
    """The single agentic reasoning loop. Async generator yielding typed events.

    Events yielded (``(kind, data)``):
      * ``("content", delta)``            — a content delta to emit/accumulate.
      * ``("tool_call_start", name)``     — tool name seen streaming in.
      * ``("tool_call_exec", name)``      — tool about to be invoked.
      * ``("tool_call_result", payload)`` — ``{"name","args","result"}`` dict.
      * ``("citation", name)``            — cite this tool (data source).
      * ``("hitl_task", task)``           — HITL proposal detected.
      * ``("tool_call_finished",)``       — all tool calls this iteration done.
      * ``("done", reached_max)``         — loop ended; ``reached_max`` True iff
        the iteration cap was hit without a no-tool-calls break.

    The loop owns the accumulators and performs both the proactive save (the
    moment a HITL proposal is detected — streaming only, so the card survives
    stream interruptions) and the final save (streaming always; non-streaming
    only on a clean no-tool-calls break). Callers do NOT save.
    """
    total_content = ""
    all_tool_calls: List[Dict[str, Any]] = []
    all_citations: List[str] = []
    all_tasks: List[Dict[str, Any]] = []
    proactive_message = None
    clean_break = False

    for i in range(max_iterations):
        tool_calls = None
        content_for_history = ""

        if streaming:
            final_chunk = None
            tool_name_yielded: set = set()
            content_received = ""
            content_yielded_iter = ""

            async for chunk in llm_with_tools.astream(history):
                if chunk.tool_call_chunks:
                    for tc_chunk in chunk.tool_call_chunks:
                        tc_name = tc_chunk.get("name")
                        if tc_name and tc_name not in tool_name_yielded:
                            yield ("tool_call_start", tc_name)
                            tool_name_yielded.add(tc_name)

                if chunk.content:
                    # Some providers re-emit accumulated content rather than
                    # true deltas — reconcile against what we've received.
                    if (
                        content_received
                        and chunk.content.startswith(content_received)
                        and len(chunk.content) > len(content_received)
                    ):
                        content_received = chunk.content
                    else:
                        content_received += chunk.content

                    if not total_content:
                        delta = content_received
                    elif content_received.startswith(total_content):
                        delta = content_received[len(total_content):]
                    elif total_content.startswith(content_received):
                        delta = ""
                    else:
                        delta = content_received

                    if content_yielded_iter and delta.startswith(content_yielded_iter):
                        actual_yield = delta[len(content_yielded_iter):]
                    else:
                        actual_yield = delta

                    if actual_yield:
                        yield ("content", actual_yield)
                        content_yielded_iter += actual_yield
                        total_content += actual_yield

                final_chunk = chunk if final_chunk is None else final_chunk + chunk

            tool_calls = final_chunk.tool_calls if final_chunk else None
            content_for_history = content_received
        else:
            response = await llm_with_tools.ainvoke(history)
            if response.content:
                if not total_content:
                    delta = response.content
                elif response.content.startswith(total_content):
                    delta = response.content[len(total_content):]
                elif total_content.startswith(response.content):
                    delta = ""
                else:
                    delta = response.content
                if delta:
                    yield ("content", delta)
                    total_content += delta
            tool_calls = response.tool_calls
            content_for_history = response.content

        if not tool_calls:
            clean_break = True
            break

        logger.info(
            f"{log_label}: tool calls detected, entering reasoning loop "
            f"iteration {i + 1}"
        )

        history.append(AIMessage(content=content_for_history, tool_calls=tool_calls))

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            selected_tool = next((t for t in tools if t.name == tool_name), None)
            if selected_tool:
                yield ("tool_call_exec", tool_name)
                observation = await selected_tool.ainvoke(tool_call["args"])

                hitl_task = _parse_hitl_proposal(observation)
                if hitl_task:
                    all_tasks.append(hitl_task)
                    feedback = _hitl_llm_feedback(hitl_task)
                    # Trimmed tool result so the chip resolves to "finished".
                    trimmed = {
                        "name": tool_name,
                        "args": tool_call["args"],
                        "result": feedback,
                    }
                    yield ("tool_call_result", trimmed)
                    # Dedicated sentinel drives the interactive task card.
                    yield ("hitl_task", hitl_task)
                    all_tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": feedback,
                        }
                    )
                    # Proposals are NOT data sources — no citation.
                    history.append(
                        ToolMessage(content=feedback, tool_call_id=tool_call["id"])
                    )
                    # PROACTIVE PERSISTENCE (streaming only): save the message
                    # the moment a HITL task is emitted so the task card
                    # survives stream interruptions (LLM errors, client
                    # disconnects). Without this, the final save would never
                    # run and the task would be lost — breaking /resolve (404)
                    # and /resume ("No task-bearing message").
                    if streaming and session_id and proactive_message is None:
                        try:
                            proactive_message = await chat_session_service.save_message(
                                session_id=session_id,
                                role="assistant",
                                content={"text": total_content},
                                tool_calls=list(all_tool_calls),
                                citations=list(all_citations),
                                tasks=list(all_tasks),
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
                    result_str = str(observation)
                    payload = {
                        "name": tool_name,
                        "args": tool_call["args"],
                        "result": result_str,
                    }
                    yield ("tool_call_result", payload)
                    yield ("citation", selected_tool.name)

                    all_tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": result_str,
                        }
                    )
                    all_citations.append(selected_tool.name)

                    history.append(
                        ToolMessage(
                            content=str(observation), tool_call_id=tool_call["id"]
                        )
                    )
            else:
                history.append(
                    ToolMessage(
                        content=f"Tool {tool_name} not found.",
                        tool_call_id=tool_call["id"],
                    )
                )
        yield ("tool_call_finished", None)

    # Final save: streaming always (matches prior behaviour); non-streaming
    # only on a clean no-tool-calls break (the former _general_chat saved on
    # the break and returned, never on the max-iterations fallback).
    if session_id and chat_session_service is not None and (streaming or clean_break):
        if proactive_message is not None:
            await chat_session_service.update_message_fields(
                proactive_message,
                content={"text": total_content},
                tool_calls=all_tool_calls,
                citations=all_citations,
                tasks=all_tasks or None,
            )
        else:
            await chat_session_service.save_message(
                session_id=session_id,
                role="assistant",
                content={"text": total_content},
                tool_calls=all_tool_calls,
                citations=all_citations,
                tasks=all_tasks or None,
            )

    yield ("done", not clean_break)


async def stream_loop_as_sse(
    loop: AsyncIterator[Tuple[str, Any]],
) -> AsyncIterator[str]:
    """Map :func:`run_reasoning_loop` events to the SSE sentinel vocabulary the
    frontend parser consumes (``[TOOL_CALL_*]`` / ``[CITATION]`` /
    ``[HITL_TASK]``). ``("content", delta)`` is yielded verbatim; ``("done",)``
    emits nothing. Used by the streaming chat + resume paths."""
    async for kind, data in loop:
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
