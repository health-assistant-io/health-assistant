"""LangGraph mirror of the agentic-chat reasoning loop (Phase 3.2).

``run_chat_graph`` reproduces :func:`app.ai.agents.chat_agent.run_reasoning_loop`
event-for-event as a StateGraph, selected by ``AI_AGENT_ENGINE=loop|graph``.
Both engines yield the same ``(kind, data)`` tuples, so
:func:`app.ai.agents.chat_agent.stream_loop_as_sse` and the non-streaming
consumer work unchanged — the SSE sentinel contract is frozen.

Decision point (create_agent vs custom): custom StateGraph. The loop
intercepts HITL proposals at tool-result level (trimmed feedback substituted
into the ToolMessage, no citation, proactive message persistence) and has
conditional final-save semantics (streaming always, non-streaming only on a
clean break) — none of that is hostable in ``create_agent`` middleware
without behavior risk. Documented for the integrator; revisit if the loop
grows standard tool-agent needs.

Graph shape::

    START -> agent_step -> route_step ──(no tool calls)──────────> finalize
                    │               └─(tool calls)-> tool_exec -> route_tools
                    └──────────────(under cap)<─────────────────────┘
                                                    └─(cap reached)-> finalize

Caps: ``max_iterations`` (resolved by the caller from tenant/system/env, the
same value the loop engine receives). Node fault tolerance via
``set_node_defaults`` per the Phase 3.0 spike (async nodes only).
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, TypedDict
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.ai.agents.chat_agent import run_reasoning_loop
from app.ai.agents.hitl import (
    _hitl_llm_feedback,
    _hitl_proposal_note,
    _parse_hitl_proposal,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class ChatGraphState(TypedDict):
    # Static per-run configuration (written once at START).
    llm_with_tools: Any
    tools: List[Any]
    streaming: bool
    chat_session_service: Any
    session_id: Optional[UUID]
    log_label: str
    max_iterations: int
    # Mutable run state.
    history: List[Any]
    iteration: int  # LLM calls made so far
    total_content: str
    all_tool_calls: List[Dict[str, Any]]
    all_citations: List[str]
    all_tasks: List[Dict[str, Any]]
    proactive_message: Any
    # agent_step -> tool_exec handoff: None = no tool calls this iteration
    # (clean break); [] = tool calls consumed by tool_exec.
    pending_tool_calls: Optional[List[Dict[str, Any]]]


def _chat_graph_nodes():
    async def agent_step(state: ChatGraphState) -> Dict[str, Any]:
        """One LLM call (streamed or not) — provider-quirk content dedup is
        copied verbatim from the loop engine for event parity."""
        writer = get_stream_writer()

        def emit(kind: str, data: Any) -> None:
            writer({"kind": kind, "data": data})

        llm_with_tools = state["llm_with_tools"]
        history = list(state["history"])
        streaming = state["streaming"]
        total_content = state["total_content"]

        tool_calls = None
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
                            emit("tool_call_start", tc_name)
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
                        delta = content_received[len(total_content) :]
                    elif total_content.startswith(content_received):
                        delta = ""
                    else:
                        delta = content_received

                    if content_yielded_iter and delta.startswith(
                        content_yielded_iter
                    ):
                        actual_yield = delta[len(content_yielded_iter) :]
                    else:
                        actual_yield = delta

                    if actual_yield:
                        emit("content", actual_yield)
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
                    delta = response.content[len(total_content) :]
                elif total_content.startswith(response.content):
                    delta = ""
                else:
                    delta = response.content
                if delta:
                    emit("content", delta)
                    total_content += delta
            tool_calls = response.tool_calls
            content_for_history = response.content

        updates: Dict[str, Any] = {
            "iteration": state["iteration"] + 1,
            "total_content": total_content,
            "pending_tool_calls": list(tool_calls) if tool_calls else None,
            "content_for_history": content_for_history,
        }
        if tool_calls:
            history.append(
                AIMessage(content=content_for_history, tool_calls=tool_calls)
            )
            updates["history"] = history
        return updates

    async def tool_exec(state: ChatGraphState) -> Dict[str, Any]:
        writer = get_stream_writer()

        def emit(kind: str, data: Any) -> None:
            writer({"kind": kind, "data": data})

        history = list(state["history"])
        tools = state["tools"]
        streaming = state["streaming"]
        session_id = state["session_id"]
        chat_session_service = state["chat_session_service"]
        log_label = state["log_label"]
        total_content = state["total_content"]
        all_tool_calls = list(state["all_tool_calls"])
        all_citations = list(state["all_citations"])
        all_tasks = list(state["all_tasks"])
        proactive_message = state["proactive_message"]

        logger.info(
            f"{log_label}: tool calls detected, entering reasoning loop "
            f"iteration {state['iteration']}"
        )

        for tool_call in state["pending_tool_calls"] or []:
            tool_name = tool_call["name"]
            selected_tool = next(
                (t for t in tools if t.name == tool_name), None
            )
            if selected_tool:
                emit("tool_call_exec", tool_name)
                observation = await selected_tool.ainvoke(tool_call["args"])

                hitl_task = _parse_hitl_proposal(observation)
                if hitl_task:
                    all_tasks.append(hitl_task)
                    note = _hitl_proposal_note(observation)
                    feedback = _hitl_llm_feedback(hitl_task, note)
                    # Trimmed tool result so the chip resolves to "finished".
                    trimmed = {
                        "name": tool_name,
                        "args": tool_call["args"],
                        "result": feedback,
                    }
                    emit("tool_call_result", trimmed)
                    # Dedicated sentinel drives the interactive task card.
                    emit("hitl_task", hitl_task)
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
                        ToolMessage(
                            content=feedback, tool_call_id=tool_call["id"]
                        )
                    )
                    # PROACTIVE PERSISTENCE (streaming only): save the message
                    # the moment a HITL task is emitted so the task card
                    # survives stream interruptions. Without this, the final
                    # save would never run and the task would be lost —
                    # breaking /resolve (404) and /resume.
                    if streaming and session_id and proactive_message is None:
                        try:
                            proactive_message = (
                                await chat_session_service.save_message(
                                    session_id=session_id,
                                    role="assistant",
                                    content={"text": total_content},
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
                    result_str = str(observation)
                    payload = {
                        "name": tool_name,
                        "args": tool_call["args"],
                        "result": result_str,
                    }
                    emit("tool_call_result", payload)
                    emit("citation", selected_tool.name)

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
                            content=str(observation),
                            tool_call_id=tool_call["id"],
                        )
                    )
            else:
                history.append(
                    ToolMessage(
                        content=f"Tool {tool_name} not found.",
                        tool_call_id=tool_call["id"],
                    )
                )
        emit("tool_call_finished", None)

        return {
            "history": history,
            "all_tool_calls": all_tool_calls,
            "all_citations": all_citations,
            "all_tasks": all_tasks,
            "proactive_message": proactive_message,
            "pending_tool_calls": [],
        }

    def route_step(state: ChatGraphState) -> str:
        if not state["pending_tool_calls"]:
            return "finalize"
        return "tool_exec"

    def route_tools(state: ChatGraphState) -> str:
        # Each graph iteration = one LLM call + its tool executions, matching
        # the loop engine's ``for i in range(max_iterations)``.
        if state["iteration"] >= state["max_iterations"]:
            return "finalize"
        return "agent_step"

    def route_start(state: ChatGraphState) -> str:
        # The loop engine with an exhausted cap never calls the LLM at all.
        if state["max_iterations"] <= 0:
            return "finalize"
        return "agent_step"

    async def finalize(state: ChatGraphState) -> Dict[str, Any]:
        writer = get_stream_writer()

        def emit(kind: str, data: Any) -> None:
            writer({"kind": kind, "data": data})

        clean_break = state["pending_tool_calls"] is None
        session_id = state["session_id"]
        chat_session_service = state["chat_session_service"]
        streaming = state["streaming"]
        # Final save: streaming always (matches prior behaviour);
        # non-streaming only on a clean no-tool-calls break.
        if session_id and chat_session_service is not None and (
            streaming or clean_break
        ):
            if state["proactive_message"] is not None:
                await chat_session_service.update_message_fields(
                    state["proactive_message"],
                    content={"text": state["total_content"]},
                    tool_calls=state["all_tool_calls"],
                    citations=state["all_citations"],
                    tasks=state["all_tasks"] or None,
                )
            else:
                await chat_session_service.save_message(
                    session_id=session_id,
                    role="assistant",
                    content={"text": state["total_content"]},
                    tool_calls=state["all_tool_calls"],
                    citations=state["all_citations"],
                    tasks=state["all_tasks"] or None,
                )
        emit("done", not clean_break)
        return {}

    return agent_step, tool_exec, route_step, route_tools, route_start, finalize


def build_chat_graph(checkpointer: Optional[Any] = None):
    """Compile the chat-agent graph (per run; request-scoped tools/DB are
    carried in state, so nothing is shared between runs)."""
    (
        agent_step,
        tool_exec,
        route_step,
        route_tools,
        route_start,
        finalize,
    ) = _chat_graph_nodes()
    builder = StateGraph(ChatGraphState)
    builder.add_node("agent_step", agent_step)
    builder.add_node("tool_exec", tool_exec)
    builder.add_node("finalize", finalize)
    builder.add_conditional_edges(START, route_start)
    builder.add_conditional_edges("agent_step", route_step)
    builder.add_conditional_edges("tool_exec", route_tools)
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


async def run_chat_graph(
    llm_with_tools,
    tools: List[Any],
    history: List[Any],
    max_iterations: int,
    *,
    streaming: bool,
    chat_session_service=None,
    session_id: Optional[UUID] = None,
    log_label: str = "AI Assistance",
    checkpointer: Optional[Any] = None,
) -> AsyncIterator[Tuple[str, Any]]:
    """Engine-selectable mirror of :func:`run_reasoning_loop`: consumes the
    graph's custom stream and re-emits the loop's ``(kind, data)`` events."""
    graph = build_chat_graph(checkpointer=checkpointer)
    initial: ChatGraphState = {
        "llm_with_tools": llm_with_tools,
        "tools": tools,
        "streaming": streaming,
        "chat_session_service": chat_session_service,
        "session_id": session_id,
        "log_label": log_label,
        "max_iterations": max_iterations,
        "history": history,
        "iteration": 0,
        "total_content": "",
        "all_tool_calls": [],
        "all_citations": [],
        "all_tasks": [],
        "proactive_message": None,
        "pending_tool_calls": None,
    }
    config = (
        {"configurable": {"thread_id": str(session_id)}} if session_id else None
    )
    async for mode, payload in graph.astream(
        initial, config=config, stream_mode=["custom"]
    ):
        if mode == "custom" and isinstance(payload, dict) and "kind" in payload:
            yield (payload["kind"], payload["data"])


def chat_engine_iter(
    llm_with_tools,
    tools: List[Any],
    history: List[Any],
    max_iterations: int,
    *,
    streaming: bool,
    chat_session_service=None,
    session_id: Optional[UUID] = None,
    log_label: str = "AI Assistance",
    checkpointer: Optional[Any] = None,
):
    """Select the chat engine per ``AI_AGENT_ENGINE`` (``loop`` | ``graph``).

    Returns an async iterator of the shared ``(kind, data)`` event vocabulary;
    callers are engine-agnostic. Unknown values fall back to ``loop``.
    ``checkpointer`` is used by the graph engine only (Phase 3.3).
    """
    if settings.AI_AGENT_ENGINE == "graph":
        return run_chat_graph(
            llm_with_tools,
            tools,
            history,
            max_iterations,
            streaming=streaming,
            chat_session_service=chat_session_service,
            session_id=session_id,
            log_label=log_label,
            checkpointer=checkpointer,
        )
    return run_reasoning_loop(
        llm_with_tools,
        tools,
        history,
        max_iterations,
        streaming=streaming,
        chat_session_service=chat_session_service,
        session_id=session_id,
        log_label=log_label,
    )
