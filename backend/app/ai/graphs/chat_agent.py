"""LangGraph mirror of the agentic-chat reasoning loop (Phases 3.2 + 3.3).

``run_chat_graph`` is the single chat reasoning engine — a StateGraph whose
custom stream emits the family chat vocabulary (ai-features §5) plus
health's payload events (``citation`` / ``task`` / ``interrupt`` /
``session``, plan 24 §2) natively. The engine yields one event dict per
stream item, ``{"event": <name>, ...fields}``; the internal ``"done"``
completion signal (``{"event": "done", "reached_max": bool}``) is the one
non-vocabulary item — it never reaches the SSE wire (the non-streaming
consumer reads the iteration cap from it).

Decision point (create_agent vs custom): custom StateGraph. The loop
intercepts HITL proposals at tool-result level (trimmed feedback substituted
into the ToolMessage, no citation, proactive message persistence) and has
conditional final-save semantics (streaming always, non-streaming only on a
clean break) — none of that is hostable in ``create_agent`` middleware
without behavior risk. Ratified by the integrator (Phase 3.3).

Graph shape::

    START -> agent_step -> route_step ──(no tool calls)──────────> finalize
                    │               └─(tool calls)-> tool_exec -> route_exec
                    └──────────────(under cap)<─────────────────────┘
                                       (ask_user)-> await_user -> route_wait
                                                    (others cap)-> finalize

Phase 3.3 HITL ruling: ``ask_user`` PAUSES the turn at the question card —
``tool_exec`` emits the card (``task`` payload event) + persists
proactively (committed by the superstep checkpoint), then hands to the
side-effect-free ``await_user`` node whose ``interrupt()`` is its first
statement, so NOTHING re-executes on resume. ``/resume`` delivers the
resolution summary as ``Command(resume=...)`` — it becomes the ask_user
``ToolMessage`` and the model continues the same conversation.
``propose_*`` keeps continue-after-propose (card events + HitlTask rows +
idempotent /resolve, unchanged).

Fault tolerance (ratified): graph-default retry covers ``agent_step`` (LLM
calls are read-only; state commits only on node success). ``tool_exec`` is
pinned to a single attempt — clinical tools stay non-idempotent even
checkpointed. There is intentionally NO error_handler: exceptions bubble to
the SSE error classifier exactly like the loop engine (an error->finalize
handler would persist partial content and change the failure contract).

Checkpoint-serializability: the state carries ONLY serializable data
(messages, counters, task dicts). Per-run handles (LLM runnable, tools,
chat service, user/tenant context) ride in ``config["configurable"]`` under
the ``runtime`` key — never checkpointed — and must be supplied again by the
resume caller (:func:`resume_interrupted_chat_graph`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import (
    Any,
    TypedDict,
)
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, interrupt

from app.ai.agents.hitl import (
    _hitl_llm_feedback,
    _hitl_proposal_note,
    _parse_hitl_proposal,
)
from app.ai.flow_events import (
    delta_event,
    flow_failed_event,
    flow_finished_event,
    flow_started_event,
    node_finished_event,
    node_started_event,
    tool_call_event,
)
from app.ai.flow_events_payload import (
    citation_event,
    interrupt_event,
    task_event,
)
from app.ai.graphs.checkpointer import get_runtime_saver

logger = logging.getLogger(__name__)

_RETRYABLE_EXC_NAMES = {
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "ConnectionError",
    "TimeoutError",
}


def _stream_writer():
    """Stream-writer accessor that also works under direct node invocation
    (``graph.nodes[...]`` tests run outside the Pregel loop, where
    ``get_stream_writer`` raises — events are simply dropped there)."""
    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _event: None


def _stringify_args(args: Any) -> str:
    """§5 ``tool_call.args`` is a serialized-safe string, never a raw dict."""
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    return json.dumps(args, ensure_ascii=False, default=str)


def _visible_text(content: Any) -> str:
    """Visible text of one message/chunk ``content``.

    Normalizes the two LangChain content shapes: plain ``str`` and the
    block-list form (``[{"type": "text", "text": ...}, ...]``) emitted by
    Anthropic-style providers and OpenAI-compatible gateways. Non-text
    blocks (``thinking``/``redacted_thinking``/``tool_use``/``image``)
    contribute nothing — they are not part of the visible answer and must
    never reach the persisted ``content.text``. Anything else is dropped
    rather than stringified (model output is untrusted input).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


class ChatGraphState(TypedDict):
    # Serializable per-run configuration (checkpointed: streaming/caps must
    # survive an interrupt resume).
    streaming: bool
    session_id: str | None
    max_iterations: int
    # Mutable run state (all serializable).
    history: list[Any]
    iteration: int  # LLM calls made so far
    total_content: str
    all_tool_calls: list[dict[str, Any]]
    all_citations: list[str]
    all_tasks: list[dict[str, Any]]
    # agent_step -> tool_exec handoff: None = no tool calls this iteration
    # (clean break); [] = tool calls consumed by tool_exec.
    pending_tool_calls: list[dict[str, Any]] | None
    # tool_exec -> await_user handoff (ask_user only): the interrupt payload
    # (the HITL task dict) + the tool_call_id awaiting its ToolMessage.
    pending_interrupt: dict[str, Any] | None


def _chat_graph_nodes():
    async def agent_step(state: ChatGraphState, config) -> dict[str, Any]:
        """One LLM call (streamed or not) — provider-quirk content dedup is
        copied verbatim from the loop engine for event parity."""
        writer = _stream_writer()
        runtime = config["configurable"]["runtime"]

        def emit(event: dict[str, Any]) -> None:
            writer(event)

        llm_with_tools = runtime["llm_with_tools"]
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
                            emit(tool_call_event(id=tc_name, name=tc_name, status="running"))
                            tool_name_yielded.add(tc_name)

                chunk_text = _visible_text(chunk.content)
                if chunk_text:
                    # Some providers re-emit accumulated content rather than
                    # true deltas — reconcile against what we've received.
                    if (
                        content_received
                        and chunk_text.startswith(content_received)
                        and len(chunk_text) > len(content_received)
                    ):
                        content_received = chunk_text
                    else:
                        content_received += chunk_text

                    if not total_content:
                        delta = content_received
                    elif content_received.startswith(total_content):
                        delta = content_received[len(total_content) :]
                    elif total_content.startswith(content_received):
                        delta = ""
                    else:
                        delta = content_received

                    if content_yielded_iter and delta.startswith(content_yielded_iter):
                        actual_yield = delta[len(content_yielded_iter) :]
                    else:
                        actual_yield = delta

                    if actual_yield:
                        emit(delta_event(actual_yield))
                        content_yielded_iter += actual_yield
                        total_content += actual_yield

                final_chunk = chunk if final_chunk is None else final_chunk + chunk

            tool_calls = final_chunk.tool_calls if final_chunk else None
            content_for_history = content_received
        else:
            response = await llm_with_tools.ainvoke(history)
            response_text = _visible_text(response.content)
            if response_text:
                if not total_content:
                    delta = response_text
                elif response_text.startswith(total_content):
                    delta = response_text[len(total_content) :]
                elif total_content.startswith(response_text):
                    delta = ""
                else:
                    delta = response_text
                if delta:
                    emit(delta_event(delta))
                    total_content += delta
            tool_calls = response.tool_calls
            content_for_history = response_text

        updates: dict[str, Any] = {
            "iteration": state["iteration"] + 1,
            "total_content": total_content,
            "pending_tool_calls": list(tool_calls) if tool_calls else None,
            "content_for_history": content_for_history,
        }
        if tool_calls:
            history.append(AIMessage(content=content_for_history, tool_calls=tool_calls))
            updates["history"] = history
        return updates

    async def tool_exec(state: ChatGraphState, config) -> dict[str, Any]:
        writer = _stream_writer()
        runtime = config["configurable"]["runtime"]

        def emit(event: dict[str, Any]) -> None:
            writer(event)

        history = list(state["history"])
        tools = runtime["tools"]
        streaming = state["streaming"]
        session_id = state["session_id"]
        chat_session_service = runtime["chat_session_service"]
        log_label = runtime["log_label"]
        total_content = state["total_content"]
        all_tool_calls = list(state["all_tool_calls"])
        all_citations = list(state["all_citations"])
        all_tasks = list(state["all_tasks"])
        proactive_message = runtime["proactive_message"]
        pending_interrupt = state["pending_interrupt"]

        logger.info(
            f"{log_label}: tool calls detected, entering reasoning loop "
            f"iteration {state['iteration']}"
        )

        async def save_proactive() -> None:
            """Persist the assistant message the moment a HITL card is emitted
            so the task survives stream interruptions (breaking /resolve (404)
            and /resume otherwise). Streaming only; once per run."""
            nonlocal proactive_message
            if not (streaming and session_id and proactive_message is None):
                return
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
                    f"{all_tasks[-1].get('task_type') if all_tasks else '?'})"
                )
            except Exception as e:
                logger.error(f"Failed to proactively save HITL task: {e}", exc_info=True)

        for tool_call in state["pending_tool_calls"] or []:
            tool_name = tool_call["name"]
            selected_tool = next((t for t in tools if t.name == tool_name), None)
            if selected_tool:
                args_str = _stringify_args(tool_call["args"])
                emit(
                    tool_call_event(
                        id=tool_call.get("id") or tool_name,
                        name=tool_name,
                        status="running",
                    )
                )
                observation = await selected_tool.ainvoke(tool_call["args"])

                hitl_task = _parse_hitl_proposal(observation)
                if hitl_task and (
                    streaming
                    and hitl_task.get("task_type") == "ask_user"
                    and pending_interrupt is None
                ):
                    # ask_user (Phase 3.3 ruling): the turn PAUSES at the
                    # question card. Emit the card + persist proactively here
                    # (committed by the superstep checkpoint), then hand off
                    # to the side-effect-free await_user node whose interrupt()
                    # is its first statement — nothing re-executes on resume.
                    # A second ask_user in the same iteration (the tool prompt
                    # forbids it) degrades to continue-mode below.
                    all_tasks.append(hitl_task)
                    note = _hitl_proposal_note(observation)
                    feedback = _hitl_llm_feedback(hitl_task, note)
                    emit(
                        tool_call_event(
                            id=tool_call.get("id") or tool_name,
                            name=tool_name,
                            status="done",
                            args=args_str,
                            result=feedback,
                        )
                    )
                    emit(task_event(hitl_task))
                    all_tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": feedback,
                        }
                    )
                    await save_proactive()
                    pending_interrupt = {
                        "task": hitl_task,
                        "tool_call_id": tool_call.get("id"),
                    }
                elif hitl_task:
                    all_tasks.append(hitl_task)
                    note = _hitl_proposal_note(observation)
                    feedback = _hitl_llm_feedback(hitl_task, note)
                    # Trimmed tool result so the chip resolves to "finished".
                    emit(
                        tool_call_event(
                            id=tool_call.get("id") or tool_name,
                            name=tool_name,
                            status="done",
                            args=args_str,
                            result=feedback,
                        )
                    )
                    # Dedicated payload event drives the interactive task card.
                    emit(task_event(hitl_task))
                    all_tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": feedback,
                        }
                    )
                    # Proposals are NOT data sources — no citation.
                    history.append(ToolMessage(content=feedback, tool_call_id=tool_call["id"]))
                    await save_proactive()
                else:
                    result_str = str(observation)
                    emit(
                        tool_call_event(
                            id=tool_call.get("id") or tool_name,
                            name=tool_name,
                            status="done",
                            args=args_str,
                            result=result_str,
                        )
                    )
                    emit(citation_event(selected_tool.name))

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

        runtime["proactive_message"] = proactive_message
        return {
            "history": history,
            "all_tool_calls": all_tool_calls,
            "all_citations": all_citations,
            "all_tasks": all_tasks,
            "pending_tool_calls": [],
            "pending_interrupt": pending_interrupt,
        }

    async def await_user(state: ChatGraphState, config) -> dict[str, Any]:
        """Pause the run for ask_user answers (Phase 3.3).

        The interrupt() call is the FIRST statement — this node must stay
        side-effect-free because LangGraph re-runs it from the top on resume
        (the resume value becomes interrupt()'s return value). The answers
        arrive as the ToolMessage responding to the ask_user tool_call_id.
        """
        pending = state["pending_interrupt"] or {}
        decision = interrupt(pending.get("task"))
        history = list(state["history"])
        tool_call_id = pending.get("tool_call_id")
        if tool_call_id:
            history.append(ToolMessage(content=str(decision), tool_call_id=tool_call_id))
        return {"history": history, "pending_interrupt": None}

    def route_step(state: ChatGraphState) -> str:
        if not state["pending_tool_calls"]:
            return "finalize"
        return "tool_exec"

    def route_exec(state: ChatGraphState) -> str:
        if state["pending_interrupt"]:
            return "await_user"
        # Each graph iteration = one LLM call + its tool executions, matching
        # the loop engine's ``for i in range(max_iterations)``.
        if state["iteration"] >= state["max_iterations"]:
            return "finalize"
        return "agent_step"

    def route_wait(state: ChatGraphState) -> str:
        if state["iteration"] >= state["max_iterations"]:
            return "finalize"
        return "agent_step"

    def route_start(state: ChatGraphState) -> str:
        # The loop engine with an exhausted cap never calls the LLM at all.
        if state["max_iterations"] <= 0:
            return "finalize"
        return "agent_step"

    async def finalize(state: ChatGraphState, config) -> dict[str, Any]:
        writer = _stream_writer()
        runtime = config["configurable"]["runtime"]

        # Clean break = agent_step produced no tool calls (iteration > 0).
        # A zero/exhausted cap (route_start) never ran the LLM — iteration 0
        # — which the loop engine reports as reached_max=True.
        clean_break = state["pending_tool_calls"] is None and state["iteration"] > 0
        session_id = state["session_id"]
        streaming = state["streaming"]
        chat_session_service = runtime["chat_session_service"]
        proactive_message = runtime["proactive_message"]
        # Crash-resume dedup: if the process died between the proactive save
        # and the final update, re-attach the proactive message by its task's
        # proposal_id so the resume writes an UPDATE, not a duplicate card.
        if (
            proactive_message is None
            and session_id
            and state["all_tasks"]
            and runtime.get("user_id")
            and runtime.get("tenant_id")
        ):
            try:
                proactive_message = await chat_session_service.find_message_by_proposal(
                    session_id=session_id,
                    proposal_id=state["all_tasks"][0].get("proposal_id"),
                    user_id=runtime["user_id"],
                    tenant_id=runtime["tenant_id"],
                )
            except Exception as e:
                logger.warning(f"Proactive-message re-lookup failed: {e}")

        # Final save: streaming always (matches prior behaviour);
        # non-streaming only on a clean no-tool-calls break.
        if session_id and chat_session_service is not None and (streaming or clean_break):
            if proactive_message is not None:
                await chat_session_service.update_message_fields(
                    proactive_message,
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
        writer({"event": "done", "reached_max": not clean_break})
        return {}

    return (
        agent_step,
        tool_exec,
        await_user,
        route_step,
        route_exec,
        route_wait,
        route_start,
        finalize,
    )


def build_chat_graph(checkpointer: Any | None = None):
    """Compile the chat-agent graph (per run; request-scoped handles ride in
    ``config["configurable"]``, never in the checkpointed state)."""
    (
        agent_step,
        tool_exec,
        await_user,
        route_step,
        route_exec,
        route_wait,
        route_start,
        finalize,
    ) = _chat_graph_nodes()

    def _flow_events(name: str, fn):
        """Emit family ``node_started`` / ``node_finished`` events around a
        node body."""

        async def wrapped(state: ChatGraphState, config) -> dict[str, Any]:
            writer = _stream_writer()
            writer(node_started_event(name))
            try:
                result = await fn(state, config)
            except GraphInterrupt:
                # A pause is not a failure: the node re-runs on resume.
                writer(node_finished_event(name, "interrupted"))
                raise
            except Exception:
                writer(node_finished_event(name, "failed"))
                raise
            writer(node_finished_event(name, "ok"))
            return result

        wrapped.__name__ = name
        return wrapped

    builder = StateGraph(ChatGraphState)
    builder.set_node_defaults(
        retry_policy=RetryPolicy(max_attempts=2, retry_on=(ConnectionError, TimeoutError))
    )
    builder.add_node("agent_step", _flow_events("agent_step", agent_step))
    builder.add_node(
        "tool_exec",
        _flow_events("tool_exec", tool_exec),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    builder.add_node("await_user", _flow_events("await_user", await_user))
    builder.add_node("finalize", _flow_events("finalize", finalize))
    builder.add_conditional_edges(START, route_start)
    builder.add_conditional_edges("agent_step", route_step)
    builder.add_conditional_edges("tool_exec", route_exec)
    builder.add_conditional_edges("await_user", route_wait)
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def _initial_state(
    history: list[Any],
    max_iterations: int,
    *,
    streaming: bool,
    session_id: UUID | None,
) -> ChatGraphState:
    return {
        "streaming": streaming,
        "session_id": str(session_id) if session_id else None,
        "max_iterations": max_iterations,
        "history": history,
        "iteration": 0,
        "total_content": "",
        "all_tool_calls": [],
        "all_citations": [],
        "all_tasks": [],
        "pending_tool_calls": None,
        "pending_interrupt": None,
    }


def _make_config(
    session_id: UUID | None,
    checkpointer: Any | None,
    runtime: dict[str, Any],
) -> dict[str, Any] | None:
    if checkpointer is not None and session_id is None:
        raise ValueError("A checkpointer requires a session_id (thread_id).")
    configurable: dict[str, Any] = {"runtime": runtime}
    if session_id is not None:
        configurable["thread_id"] = str(session_id)
    return {"configurable": configurable}


async def run_chat_graph(
    llm_with_tools,
    tools: list[Any],
    history: list[Any],
    max_iterations: int,
    *,
    streaming: bool,
    chat_session_service=None,
    session_id: UUID | None = None,
    log_label: str = "AI Assistance",
    checkpointer: Any | None = None,
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one chat turn, yielding the family + health payload event dicts
    (:class:`app.ai.flow_events_payload.FlowEvent`; plus the engine-internal
    ``done`` completion signal, which the SSE mapper drops)."""
    graph = build_chat_graph(checkpointer=checkpointer)
    runtime: dict[str, Any] = {
        "llm_with_tools": llm_with_tools,
        "tools": tools,
        "chat_session_service": chat_session_service,
        "log_label": log_label,
        "proactive_message": None,
        "user_id": user_id,
        "tenant_id": tenant_id,
    }
    config = _make_config(session_id, checkpointer, runtime)

    async def gen() -> AsyncIterator[dict[str, Any]]:
        yield flow_started_event()
        done_seen = False
        try:
            async for mode, payload in graph.astream(
                _initial_state(
                    history,
                    max_iterations,
                    streaming=streaming,
                    session_id=session_id,
                ),
                config=config,
                stream_mode=["custom"],
            ):
                if mode == "custom" and isinstance(payload, dict) and "event" in payload:
                    if payload["event"] == "done":
                        done_seen = True
                    yield payload
            if not done_seen:
                # The run paused at an ask_user interrupt (no finalize).
                yield interrupt_event()
            else:
                yield flow_finished_event()
        except Exception as exc:
            names = {cls.__name__ for cls in type(exc).__mro__}
            retryable = bool(names & _RETRYABLE_EXC_NAMES)
            yield flow_failed_event(retryable)
            raise

    async for event in gen():
        yield event


def chat_engine_iter(
    llm_with_tools,
    tools: list[Any],
    history: list[Any],
    max_iterations: int,
    *,
    streaming: bool,
    chat_session_service=None,
    session_id: UUID | None = None,
    log_label: str = "AI Assistance",
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    checkpointer: Any | None = None,
):
    """Run a chat turn on the LangGraph engine (the only engine since the
    Phase 8 decommission). Returns an async iterator of family + health
    payload event dicts; callers stay engine-agnostic. The lifespan-held
    checkpointer (thread_id = session id) is attached for sessioned runs —
    ask_user interrupts need the durable thread; ``user_id``/``tenant_id``
    enable the crash-resume dedup.
    """
    if checkpointer is None and session_id is not None:
        checkpointer = get_runtime_saver()
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
        user_id=user_id,
        tenant_id=tenant_id,
    )


async def has_pending_interrupt(session_id: UUID) -> bool:
    """True when the session's checkpointed graph run is paused at an
    ask_user interrupt (the /resume continuation should Command(resume=...)
    instead of starting a fresh turn)."""
    saver = get_runtime_saver()
    if saver is None:
        return False
    graph = build_chat_graph(checkpointer=saver)
    snapshot = await graph.aget_state({"configurable": {"thread_id": str(session_id)}})
    if snapshot is None or not snapshot.tasks:
        return False
    return any(task.interrupts for task in snapshot.tasks)


async def resume_interrupted_chat_graph(
    session_id: UUID,
    resume_value: str,
    *,
    llm_with_tools,
    tools: list[Any],
    chat_session_service=None,
    log_label: str = "AI Assistance (resume)",
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> AsyncIterator[dict[str, Any]] | None:
    """Resume a run paused at an ask_user interrupt.

    Returns the event-dict iterator, or ``None`` when there is no pending
    interrupt (caller falls back to a fresh-continuation turn). The summary
    the loop engine would have fed as the driving input arrives as the
    interrupt's return value — the ask_user ToolMessage — and the model
    continues the same conversation. The runtime handles are supplied fresh
    (they are never checkpointed).
    """
    saver = get_runtime_saver()
    if saver is None or not await has_pending_interrupt(session_id):
        return None
    graph = build_chat_graph(checkpointer=saver)
    runtime: dict[str, Any] = {
        "llm_with_tools": llm_with_tools,
        "tools": tools,
        "chat_session_service": chat_session_service,
        "log_label": log_label,
        "proactive_message": None,
        "user_id": user_id,
        "tenant_id": tenant_id,
    }
    config = _make_config(session_id, saver, runtime)

    async def gen() -> AsyncIterator[dict[str, Any]]:
        async for mode, payload in graph.astream(
            Command(resume=resume_value), config=config, stream_mode=["custom"]
        ):
            if mode == "custom" and isinstance(payload, dict) and "event" in payload:
                yield payload

    return gen()
